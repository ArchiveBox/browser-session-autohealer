"""Fresh Browser Use Cloud sessions using the application's shared CDP driver."""

import os
import re
import time
from typing import ClassVar
from urllib.parse import quote, urlsplit

import httpx

from ..providers import CDPAdapter, provider_config


class BrowserUse(CDPAdapter):
    network_config_fields: ClassVar[set] = {"proxy_country_code"}
    network_fields: ClassVar[dict] = {"proxy_country_code": "Proxy country"}

    label = "Browser Use Cloud"
    description = "An isolated Browser Use Cloud browser with portable site data and a live view."
    config_help = (
        "Store BROWSER_USE_API_KEY in the ignored .env file. Optional settings: "
        "timeout_minutes (15; 1-240) and proxy_country_code (two-letter country code, "
        "or null for direct routing). Browser Use Cloud plans may impose a lower timeout."
    )

    def session_lifetime(self, config):
        return config.get("timeout_minutes", 15) * 60

    def location_options(self, location, config):
        country = location.get("country") or config.get("proxy_country_code")
        if not country or not any(location.get(field) for field in ("country", "state", "city")):
            return {}
        return {"proxy_country_code": country.lower()}

    def settings_support(self, config):
        support = super().settings_support(config)
        support["viewport"] = {
            "supported": True,
            "detail": "Browser width and height are set when the cloud browser is created.",
            "default": {"width": 1440, "height": 900},
        }
        for key, detail in {
            "viewport.deviceScaleFactor": "Browser Use Cloud controls the device pixel ratio.",
            "userAgent": "Browser Use Cloud retains its hosted browser user agent despite CDP overrides.",
            "userAgentMetadata": "Browser Use Cloud retains its hosted browser client hints despite CDP overrides.",
            "platform": "Browser Use Cloud retains its hosted browser platform despite CDP overrides.",
            "screen": "Browser Use Cloud controls screen metrics beyond the configured browser dimensions.",
            "window": "Browser Use Cloud controls outer window bounds.",
        }.items():
            support[key] = {"supported": False, "detail": detail}
        return support

    def validate_config(self, config):
        unknown = set(config) - {"timeout_minutes", "proxy_country_code"}
        if unknown:
            raise ValueError(
                "Unsupported Browser Use connection settings: " + ", ".join(sorted(unknown))
            )
        if "timeout_minutes" in config and (
            type(config["timeout_minutes"]) is not int or not 1 <= config["timeout_minutes"] <= 240
        ):
            raise ValueError("Browser Use timeout_minutes must be an integer from 1 to 240")
        country = config.get("proxy_country_code")
        if country is not None and (
            not isinstance(country, str) or not re.fullmatch(r"[a-zA-Z]{2}", country)
        ):
            raise ValueError(
                "Browser Use proxy_country_code must be a two-letter country code or null"
            )

    def api(self, method, path, *, allow_missing=False, **kwargs):
        key = os.environ.get("BROWSER_USE_API_KEY")
        if not key:
            raise ValueError("Set BROWSER_USE_API_KEY in .env or the process environment")
        try:
            response = httpx.request(
                method,
                "https://api.browser-use.com/api/v4" + path,
                headers={"X-Browser-Use-API-Key": key},
                timeout=60,
                **kwargs,
            )
        except httpx.HTTPError:
            raise RuntimeError("Browser Use API request could not complete") from None
        if response.is_error:
            if allow_missing and response.status_code == 404:
                return None
            raise RuntimeError(f"Browser Use returned HTTP {response.status_code}")
        if not response.content:
            return {}
        try:
            result = response.json()
        except ValueError:
            raise RuntimeError("Browser Use returned an invalid API response") from None
        if not isinstance(result, dict):
            raise TypeError("Browser Use returned an invalid API response")
        return result

    def launch(self, run):
        config = dict(provider_config(run))
        self.validate_config(config)
        viewport = run.runtime.get("settings", {}).get("viewport", {})
        options = {
            "proxyCountryCode": (
                config["proxy_country_code"].lower()
                if config.get("proxy_country_code")
                else config.get("proxy_country_code", "us")
            ),
            "timeout": config.get("timeout_minutes", 15),
            "browserScreenWidth": viewport.get("width", 1440),
            "browserScreenHeight": viewport.get("height", 900),
        }
        from ..twocaptcha import enabled, uploaded_extension

        if enabled(run):

            def upload(package):
                with package["archive"].open("rb") as file:
                    return self.api(
                        "POST",
                        "/extensions",
                        files={"file": ("twocaptcha.zip", file, "application/zip")},
                    )["id"]

            options["extensionIds"] = [
                uploaded_extension("browser_use", os.environ.get("BROWSER_USE_API_KEY", ""), upload)
            ]
            options["solveCaptchas"] = False

        result = self.api("POST", "/browsers", json=options)
        if not isinstance(result.get("id"), str) or not result["id"]:
            raise RuntimeError("Browser Use did not return an owned browser ID")
        cdp = result.get("cdpUrl")
        if not isinstance(cdp, str) or urlsplit(cdp).scheme != "wss" or not urlsplit(cdp).hostname:
            self.stop_session(result["id"])
            raise RuntimeError("Browser Use did not return a secure browser connection")
        return {
            "session_id": result["id"],
            "cdp": cdp,
            "live_url": result.get("liveUrl"),
        }

    def live_url(self, run):
        live = run.runtime.get("live_url")
        return live if isinstance(live, str) and urlsplit(live).scheme == "https" else None

    def stop_session(self, session_id):
        path = "/browsers/" + quote(session_id, safe="")
        self.api("PATCH", path, json={"action": "stop"})
        deadline = time.monotonic() + 15
        while True:
            result = self.api("GET", path, allow_missing=True)
            if result is None or result.get("status") == "stopped":
                return
            if result.get("status") != "active":
                raise RuntimeError("Browser Use returned an unexpected browser status")
            if time.monotonic() >= deadline:
                raise RuntimeError("Browser Use has not confirmed that the browser stopped")
            time.sleep(0.25)

    def stop(self, run):
        self.stop_session(run.runtime["session_id"])
