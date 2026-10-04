"""Read-only acceptance against the real Brave profile's CDP cookie store."""

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from websockets.sync.client import connect

from app.core.importers import capture_cdp

SITES = ["news.ycombinator.com", "linkedin.com"]


def test_import_reads_the_cookie_store_of_the_selected_profile():
    port_file = Path.home() / "Library/Application Support/BraveSoftware/Brave-Browser/DevToolsActivePort"
    port, path = port_file.read_text().splitlines()[:2]
    endpoint = "ws://127.0.0.1:" + port + path
    with connect(endpoint, proxy=None, max_size=10 * 1024 * 1024) as ws:
        sequence = 0

        def call(method, params=None):
            nonlocal sequence
            sequence += 1
            ws.send(json.dumps({"id": sequence, "method": method, "params": params or {}}))
            while True:
                response = json.loads(ws.recv(timeout=10))
                if response.get("id") == sequence:
                    assert "error" not in response
                    return response["result"]

        targets = [t for t in call("Target.getTargets")["targetInfos"]
                   if t["type"] == "page" and urlsplit(t["url"]).hostname == "www.linkedin.com"]
        contexts = {t.get("browserContextId") for t in targets}
        assert len(contexts) == 1
        context = contexts.pop()
        cookies = call("Storage.getCookies", {"browserContextId": context})["cookies"]
        auth = [c for c in cookies if c["domain"].lstrip(".") in {"linkedin.com", "www.linkedin.com"} and c["name"] == "li_at"]
        assert len(auth) == 1, "This acceptance test requires the real signed-in source profile"
        expected = hashlib.sha256(auth[0]["value"].encode()).hexdigest()
    state = capture_cdp(endpoint, SITES)
    # Compare fingerprints so a failure never dumps cookie values into pytest.
    actual = [hashlib.sha256(c["value"].encode()).hexdigest() for c in state["cookies"]
              if c["domain"].lstrip(".") in {"linkedin.com", "www.linkedin.com"} and c["name"] == "li_at"]
    assert actual == [expected]
