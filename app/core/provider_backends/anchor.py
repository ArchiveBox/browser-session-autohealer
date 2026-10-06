"""Fresh Anchor Browser sessions using the application's shared CDP driver."""

import os
import re
import time
from typing import ClassVar
from urllib.parse import quote, urlsplit

import httpx

from ..providers import CDPAdapter, provider_config


class Anchor(CDPAdapter):
    network_fields: ClassVar[dict] = {'country_code': 'Country', 'proxy_region': 'Region', 'proxy_city': 'City'}

    def session_lifetime(self, config):
        return config.get('max_duration', 30) * 60

    label = "Anchor Browser"
    description = "An isolated cloud browser with portable site data and an interactive live view."
    config_help = (
        "Store ANCHOR_BROWSER_API_KEY in the ignored .env file. Configure proxy, "
        "extra_stealth, captcha_solver, country_code, headless, max_duration and idle_timeout. "
        "Proxy and extra stealth default to enabled; CAPTCHA solving defaults to disabled. "
        "Timeouts are in minutes. Native IndexedDB and OPFS transfer are not supported."
    )

    def settings_support(self, config):
        support = super().settings_support(config)
        support["viewport"] = {
            "supported": True,
            "detail": "Viewport width and height apply; Anchor's tested stealth browser retains its own pixel ratio.",
            "default": {"width": 1440, "height": 1000},
        }
        for key, detail in {
            "viewport.deviceScaleFactor": "The tested stealth browser retains its own pixel ratio despite CDP overrides.",
            "userAgent": "The tested stealth browser retains its own browser version in the user agent despite CDP overrides.",
            "userAgentMetadata": "Some client hints apply, but full browser version and platform version do not match saved values.",
            "screen": "Screen dimensions and depth apply, but the available screen height remains provider controlled.",
            "deviceMemory": "The cloud browser reports its own device memory; the shared CDP driver does not override it.",
            "colorScheme": "The tested stealth browser retains its own color preference despite CDP overrides.",
            "reducedMotion": "The tested stealth browser retains its own motion preference despite CDP overrides.",
        }.items():
            support[key] = {"supported": False, "detail": detail}
        return support

    def validate_config(self, config):
        unknown = set(config) - {
            "proxy", "extra_stealth", "captcha_solver", "country_code", "headless",
            "max_duration", "idle_timeout", 'proxy_region', 'proxy_city',
        }
        if unknown:
            raise ValueError("Unsupported Anchor Browser connection settings: " + ", ".join(sorted(unknown)))
        for key in ("proxy", "extra_stealth", "captcha_solver", "headless"):
            if key in config and type(config[key]) is not bool:
                raise ValueError(f"Anchor Browser {key} must be true or false")
        for key in ("max_duration", "idle_timeout"):
            if key in config and (type(config[key]) is not int or config[key] < 1):
                raise ValueError(f"Anchor Browser {key} must be a positive number of minutes")
        if "country_code" in config and (
            not isinstance(config["country_code"], str)
            or not re.fullmatch(r"[a-z]{2}", config["country_code"])
        ):
            raise ValueError("Anchor Browser country_code must be a lowercase two-letter country code")
        if not config.get("proxy", True):
            if config.get("extra_stealth", True) or config.get("captcha_solver", False):
                raise ValueError("Anchor Browser extra_stealth and captcha_solver require proxy=true")
            if any(key in config for key in self.network_fields):
                raise ValueError("Anchor Browser country_code requires proxy=true")
        for key in ('proxy_region', 'proxy_city'):
            if key in config and (not isinstance(config[key], str) or not config[key].strip()):
                raise ValueError(f'{key} must be a nonempty name')
        if config.get('proxy_city') and not config.get('proxy_region'):
            raise ValueError('Anchor city requires a region')

    def api(self, method, path, **kwargs):
        key = os.environ.get("ANCHOR_BROWSER_API_KEY")
        if not key:
            raise ValueError("Set ANCHOR_BROWSER_API_KEY in .env or the process environment")
        try:
            response = httpx.request(
                method, "https://api.anchorbrowser.io/v1" + path,
                headers={"anchor-api-key": key}, timeout=30, **kwargs,
            )
        except httpx.HTTPError:
            raise RuntimeError("Anchor Browser API request failed before a response was received") from None
        if response.is_error:
            try:
                detail = response.json().get("error", {}).get("message", "")
                detail = re.sub(r"(?:https?|wss?)://\S+", "[redacted URL]", str(detail).replace(key, "[redacted]"))[:300]
            except (ValueError, AttributeError):
                detail = ""
            raise RuntimeError(f"Anchor Browser returned HTTP {response.status_code}: {detail}")
        try:
            return response.json()["data"]
        except (ValueError, KeyError, TypeError):
            raise RuntimeError("Anchor Browser returned an invalid API response") from None

    def launch(self, run):
        config = provider_config(run)
        self.validate_config(config)
        viewport = run.runtime["settings"].get("viewport", {})
        proxy = {"active": config.get("proxy", True), "type": "anchor_proxy"}
        if proxy["active"]:
            proxy["country_code"] = config.get("country_code", "us")
            proxy.update({key: config['proxy_' + key] for key in ('region', 'city') if config.get('proxy_' + key)})
        # Omit profile and identities: the collection owns every checkout's state.
        result = self.api("POST", "/sessions", json={
            "session": {
                "recording": {"active": False},
                "timeout": {
                    "max_duration": config.get("max_duration", 30),
                    "idle_timeout": config.get("idle_timeout", 5),
                },
                "proxy": proxy,
            },
            "browser": {
                "viewport": {key: viewport.get(key, default) for key, default in (("width", 1440), ("height", 1000))},
                "headless": {"active": config.get("headless", False)},
                "extra_stealth": {"active": config.get("extra_stealth", True)},
                "captcha_solver": {"active": config.get("captcha_solver", False)},
                "adblock": {"active": False},
                "popup_blocker": {"active": False},
                "tracing": {"active": False, "sources": False, "snapshots": False},
            },
        })
        if not isinstance(result, dict) or not isinstance(result.get("id"), str) or not result["id"]:
            raise RuntimeError("Anchor Browser did not return an owned session ID")
        if not isinstance(result.get("cdp_url"), str) or urlsplit(result["cdp_url"]).scheme not in {"ws", "wss"}:
            self.stop_session(result["id"])
            raise RuntimeError("Anchor Browser did not return a usable CDP endpoint")
        return {"cdp": result["cdp_url"], "session_id": result["id"], "live_url": result.get("live_view_url")}

    def live_url(self, run):
        return None if provider_config(run).get("headless", False) else run.runtime.get("live_url")

    def stop_session(self, session_id):
        path = "/sessions/" + quote(session_id, safe="")
        result = self.api("DELETE", path)
        if result.get("status") != "success":
            raise RuntimeError("Anchor Browser did not acknowledge session termination")
        deadline = time.monotonic() + 15
        while True:
            result = self.api("GET", path)
            if result.get("status") == "completed":
                return
            if result.get("status") in {"error", "failed", "timed_out"}:
                raise RuntimeError("Anchor Browser session ended unsuccessfully")
            if time.monotonic() >= deadline:
                raise RuntimeError("Anchor Browser has not confirmed that the browser stopped")
            time.sleep(.25)

    def stop(self, run):
        self.stop_session(run.runtime["session_id"])
