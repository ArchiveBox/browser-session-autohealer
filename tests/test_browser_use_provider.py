from types import SimpleNamespace

import httpx
import pytest

from app.core.provider_backends.browser_use import BrowserUse


@pytest.mark.parametrize(
    "config",
    [
        {"api_key": "do-not-store-credentials"},
        {"profile_id": "shared"},
        {"timeout_minutes": True},
        {"timeout_minutes": 0},
        {"timeout_minutes": 241},
        {"proxy_country_code": "USA"},
    ],
)
def test_invalid_connection_settings_are_rejected(config):
    with pytest.raises(ValueError):
        BrowserUse().validate_config(config)


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"timeout_minutes": 1, "proxy_country_code": "us"},
        {"timeout_minutes": 240, "proxy_country_code": None},
    ],
)
def test_documented_connection_settings_are_accepted(config):
    BrowserUse().validate_config(config)


def test_browser_use_is_registered():
    from app.core.providers import ADAPTERS

    assert ADAPTERS["browser_use"] is BrowserUse


def test_location_options_selects_country_proxy():
    assert BrowserUse().location_options({"country": "US", "state": "US-CA"}, {}) == {
        "proxy_country_code": "us"
    }


def test_launch_creates_a_fresh_cloud_browser(monkeypatch):
    calls = []

    def api(self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            "id": "browser-id",
            "cdpUrl": "wss://connect.browser-use.com/devtools/browser/browser-id",
            "liveUrl": "https://cloud.browser-use.com/live/browser-id",
        }

    monkeypatch.setattr(BrowserUse, "api", api)
    run = SimpleNamespace(
        runtime={
            "provider_config": {"timeout_minutes": 30, "proxy_country_code": "US"},
            "settings": {"viewport": {"width": 1280, "height": 800}},
        },
        provider=SimpleNamespace(config={}),
    )

    runtime = BrowserUse().launch(run)

    assert runtime == {
        "session_id": "browser-id",
        "cdp": "wss://connect.browser-use.com/devtools/browser/browser-id",
        "live_url": "https://cloud.browser-use.com/live/browser-id",
    }
    assert calls == [
        (
            "POST",
            "/browsers",
            {
                "json": {
                    "proxyCountryCode": "us",
                    "timeout": 30,
                    "browserScreenWidth": 1280,
                    "browserScreenHeight": 800,
                }
            },
        )
    ]


def test_launch_disables_native_solver_when_twocaptcha_is_enabled(monkeypatch):
    from app.core import twocaptcha

    calls = []

    def api(self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            "id": "browser-id",
            "cdpUrl": "wss://connect.browser-use.com/devtools/browser/browser-id",
        }

    monkeypatch.setattr(BrowserUse, "api", api)
    monkeypatch.setattr(twocaptcha, "enabled", lambda run: True)
    monkeypatch.setattr(twocaptcha, "uploaded_extension", lambda *args: "extension-id")
    run = SimpleNamespace(
        runtime={"provider_config": {}, "settings": {}},
        provider=SimpleNamespace(config={}),
    )

    BrowserUse().launch(run)

    assert calls[0][2]["json"]["extensionIds"] == ["extension-id"]
    assert calls[0][2]["json"]["solveCaptchas"] is False


def test_launch_stops_session_when_cloud_returns_invalid_cdp(monkeypatch):
    calls = []

    def api(self, method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "POST":
            return {"id": "browser-id", "cdpUrl": "https://invalid.example"}
        return {"status": "stopped"}

    monkeypatch.setattr(BrowserUse, "api", api)
    run = SimpleNamespace(
        runtime={"provider_config": {}, "settings": {}},
        provider=SimpleNamespace(config={}),
    )

    with pytest.raises(RuntimeError, match="secure browser connection"):
        BrowserUse().launch(run)

    assert [(method, path) for method, path, _ in calls] == [
        ("POST", "/browsers"),
        ("PATCH", "/browsers/browser-id"),
        ("GET", "/browsers/browser-id"),
    ]


def test_stop_confirms_session_is_stopped(monkeypatch):
    statuses = iter(({"status": "active"}, {"status": "stopped"}))
    calls = []

    def api(self, method, path, **kwargs):
        calls.append((method, path))
        return {} if method == "PATCH" else next(statuses)

    monkeypatch.setattr(BrowserUse, "api", api)
    run = SimpleNamespace(runtime={"session_id": "browser/id"})

    BrowserUse().stop(run)

    assert calls == [
        ("PATCH", "/browsers/browser%2Fid"),
        ("GET", "/browsers/browser%2Fid"),
        ("GET", "/browsers/browser%2Fid"),
    ]


def test_api_keeps_credentials_out_of_errors(monkeypatch):
    key = "browser-use-secret"
    monkeypatch.setenv("BROWSER_USE_API_KEY", key)

    class Response:
        is_error = True
        status_code = 401
        content = b'{"detail":"browser-use-secret"}'

    calls = []
    monkeypatch.setattr(
        httpx, "request", lambda *args, **kwargs: calls.append((args, kwargs)) or Response()
    )

    with pytest.raises(RuntimeError, match="HTTP 401") as error:
        BrowserUse().api("GET", "/browsers")

    assert key not in str(error.value)
    assert calls[0][1]["headers"]["X-Browser-Use-API-Key"] == key
