"""Acceptance checks against the real app and its running OpenCode service."""

import re
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pytest
from plain.runtime import settings
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

ORIGIN = "http://127.0.0.1:8421"
PREFIX = "/agents/opencode"



def test_embedded_conversation_and_real_assets(app_session):
    response = app_session.get("/runs/53/checks/12/conversation")
    assert response.status_code == 200
    match = re.search(r'<iframe[^>]+src="([^"]+)"', response.text)
    assert match, "The check in Session history must embed OpenCode"
    assert match[1].startswith(PREFIX + "/")
    assert "4098" not in response.text
    assert "copy-opencode-password" not in response.text
    frame = app_session.get(match[1])
    assert frame.status_code == 200
    assert "www-authenticate" not in frame.headers
    assert frame.headers["x-frame-options"] == "SAMEORIGIN"
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', frame.text)
    assert scripts
    for path in scripts:
        assert path.startswith(PREFIX + "/")
        asset = app_session.get(path)
        assert asset.status_code == 200
        assert "javascript" in asset.headers["content-type"]
    health = app_session.get(PREFIX + "/global/health")
    assert health.status_code == 200 and health.json()["healthy"]


def test_proxy_requires_app_login_and_same_origin(app_session):
    anonymous = httpx.get(ORIGIN + PREFIX + "/global/health")
    assert anonymous.status_code == 302
    assert anonymous.headers["location"].startswith("/login?")
    assert "www-authenticate" not in anonymous.headers
    denied = app_session.post(PREFIX + "/session", headers={"Origin": "https://example.org"})
    assert denied.status_code in {400, 403}


def test_real_event_stream(app_session):
    with app_session.stream("GET", PREFIX + "/global/event", timeout=10) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        line = next(line for line in response.iter_lines() if line.startswith("data:"))
        assert "server.connected" in line


def test_real_terminal_websocket(app_session):
    headers = {"Origin": ORIGIN}
    params = {"directory": str(Path(settings.APP_DATA_DIR) / "opencode" / "work")}
    created = app_session.post(
        PREFIX + "/pty", params=params, headers=headers,
        json={"command": "/bin/cat", "args": [], "title": "Proxy transport verification"},
    )
    assert created.status_code == 200
    terminal = created.json()["id"]
    endpoint = PREFIX + f"/pty/{terminal}"
    try:
        token = app_session.post(
            endpoint + "/connect-token", params=params,
            headers={**headers, "x-opencode-ticket": "1"},
        )
        assert token.status_code == 200
        query = {**params, "ticket": token.json()["ticket"]}
        url = ORIGIN.replace("http", "ws", 1) + endpoint + "/connect?" + urlencode(query)
        # A real existing terminal must remain inaccessible without app login.
        with pytest.raises(InvalidStatus), connect(url, origin="https://example.org", proxy=None):
            pytest.fail("Cross-origin anonymous WebSocket was accepted")
        cookie = "; ".join(f"{key}={value}" for key, value in app_session.cookies.items())
        with connect(url, origin=ORIGIN, additional_headers={"Cookie": cookie}, proxy=None) as ws:
            ws.send("account-checker-websocket-ok\n")
            output = ""
            while "account-checker-websocket-ok" not in output:
                payload = ws.recv(timeout=10)
                output += payload.decode() if isinstance(payload, bytes) else payload
            assert "account-checker-websocket-ok" in output
    finally:
        removed = app_session.delete(endpoint, params=params, headers=headers)
        assert removed.status_code == 200
