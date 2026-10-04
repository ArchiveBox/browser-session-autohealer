"""Exercise the real ZenRows CDP bridge against an owned empty Chrome process."""

import json
import os
import secrets
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.sync.client import connect


def test_bridge_retains_browser_across_independent_clients(tmp_path):
    binary = os.environ.get("CHROME_BINARY") or next(
        (str(p) for p in (
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/usr/bin/chromium"),
        ) if p.is_file()),
        None,
    )
    assert binary, "Configure a real Chrome binary for the transport acceptance test"
    chrome = bridge = None
    profile, ready = tmp_path / "chrome", tmp_path / "bridge.ready"
    with (tmp_path / "chrome.log").open("w") as chrome_log, (tmp_path / "bridge.log").open("w") as bridge_log:
        try:
            chrome = subprocess.Popen([
                binary, "--headless=new", "--no-first-run", "--no-default-browser-check",
                "--remote-debugging-port=0", f"--user-data-dir={profile}", "about:blank",
            ], stdout=chrome_log, stderr=chrome_log)
            active = profile / "DevToolsActivePort"
            deadline = time.monotonic() + 15
            while not active.exists():
                assert chrome.poll() is None, "The owned Chrome process exited before readiness"
                assert time.monotonic() < deadline, "The owned Chrome process did not become ready"
                time.sleep(.05)
            port, path = active.read_text().splitlines()[:2]
            bridge = subprocess.Popen([
                "uv", "run", "python", "-m", "app.core.provider_backends.zenrows_bridge",
            ], stdin=subprocess.PIPE, stdout=bridge_log, stderr=bridge_log, text=True)
            bridge.stdin.write(json.dumps({
                "endpoint": f"ws://127.0.0.1:{port}{path}",
                "ready": str(ready), "token": secrets.token_urlsafe(32),
            }))
            bridge.stdin.close()
            deadline = time.monotonic() + 15
            while not ready.exists():
                assert bridge.poll() is None, "The real CDP bridge exited before readiness"
                assert time.monotonic() < deadline, "The real CDP bridge did not become ready"
                time.sleep(.05)
            endpoint = json.loads(ready.read_text())["cdp"]
            parts = urlsplit(endpoint)
            invalid = urlunsplit((parts.scheme, parts.netloc, "/invalid-session-token", "", ""))
            with connect(invalid) as rejected:
                with pytest.raises(ConnectionClosedError) as closed:
                    rejected.recv(timeout=5)
                assert closed.value.rcvd.code == 1008
            assert bridge.poll() is None and chrome.poll() is None

            def command(client, number, method, params=None, session=None):
                message = {"id": number, "method": method, "params": params or {}}
                if session:
                    message["sessionId"] = session
                client.send(json.dumps(message))
                while True:
                    reply = json.loads(client.recv(timeout=10))
                    if reply.get("id") == number:
                        assert "error" not in reply, f"The real browser rejected {method}"
                        return reply["result"]

            with connect(endpoint) as first:
                version = command(first, 1, "Browser.getVersion")
                target = command(first, 2, "Target.createTarget", {"url": "about:blank"})["targetId"]
                attached = command(first, 3, "Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
                command(first, 4, "Runtime.evaluate", {"expression": "window.transportProbe=42"}, attached)
                with connect(endpoint) as second:
                    # Both outstanding calls use the same client-local request ID.
                    for client in (first, second):
                        client.send(json.dumps({"id": 20, "method": "Browser.getVersion"}))
                    for client in (first, second):
                        reply = json.loads(client.recv(timeout=10))
                        assert reply["id"] == 20
                        assert reply["result"]["product"] == version["product"]
                    assert command(second, 1, "Browser.getVersion")["product"] == version["product"]
                    second.send(json.dumps({"id": 21, "method": "Runtime.evaluate", "sessionId": attached, "params": {"expression": "window.transportProbe"}}))
                    rejection = json.loads(second.recv(timeout=10))
                    assert rejection["id"] == 21 and rejection["error"]["code"] == -32001
                    second_session = command(second, 2, "Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
                    value = command(second, 3, "Runtime.evaluate", {"expression": "window.transportProbe", "returnByValue": True}, second_session)
                    assert value["result"]["value"] == 42
                assert command(first, 5, "Runtime.evaluate", {"expression": "window.transportProbe", "returnByValue": True}, attached)["result"]["value"] == 42
            assert chrome.poll() is None and bridge.poll() is None
            with connect(endpoint) as reconnected:
                targets = command(reconnected, 1, "Target.getTargets")["targetInfos"]
                assert any(t["targetId"] == target for t in targets)
                reconnected.send(json.dumps({"id": 2, "method": "Browser.close"}))
            assert chrome.wait(timeout=10) == 0
            assert bridge.wait(timeout=10) == 0
        finally:
            for process in (bridge, chrome):
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
