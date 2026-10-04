"""Fresh Kernel cloud browsers, driven by the shared CDP state-transfer tools."""

import os
import re
import time
from urllib.parse import quote, urlsplit

import httpx

from ..providers import CDPAdapter, provider_config


class Kernel(CDPAdapter):
    label = "Kernel"
    description = "An isolated Kernel cloud browser with portable site data and a live view."
    config_help = (
        "Store KERNEL_API_KEY in the ignored .env file. Optional settings: headless (false), "
        "stealth (true), timeout_seconds (1800), region (us-east, eu-west, ap-southeast), "
        "proxy ({mode: direct/default} or {id/name: an existing Kernel proxy}). Stealth uses "
        "Kernel's default stealth proxy when proxy is omitted. Native profile, IndexedDB "
        "and OPFS transfer are not supported."
    )

    def settings_support(self, config):
        return {
            **super().settings_support(config),
            "platform": {
                "supported": False,
                "detail": "Kernel retains the cloud browser's navigator.platform despite CDP overrides.",
            },
            "window": {
                "supported": False,
                "detail": "Kernel's desktop window dimensions do not round-trip saved native bounds.",
            },
        }

    def validate_config(self, config):
        unknown = set(config) - {"headless", "stealth", "timeout_seconds", "region", "proxy"}
        if unknown:
            raise ValueError("Unsupported Kernel connection settings: " + ", ".join(sorted(unknown)))
        for key in ("headless", "stealth"):
            if key in config and type(config[key]) is not bool:
                raise ValueError(f"Kernel {key} must be true or false")
        if "timeout_seconds" in config and (
            type(config["timeout_seconds"]) is not int
            or not 10 <= config["timeout_seconds"] <= 259200
        ):
            raise ValueError("Kernel timeout_seconds must be an integer from 10 to 259200")
        if "region" in config and config["region"] not in ("us-east", "eu-west", "ap-southeast"):
            raise ValueError("Kernel region must be us-east, eu-west or ap-southeast")
        if "proxy" in config:
            proxy = config["proxy"]
            if not isinstance(proxy, dict) or len(proxy) != 1 or not set(proxy) <= {"mode", "id", "name"}:
                raise ValueError("Kernel proxy must select exactly one of mode, id or name")
            if "mode" in proxy and proxy["mode"] not in ("direct", "default"):
                raise ValueError("Kernel proxy mode must be direct or default")
            for key in ("id", "name"):
                if key in proxy and (not isinstance(proxy[key], str) or not proxy[key].strip()):
                    raise ValueError(f"Kernel proxy {key} must be a non-empty string")

    def api(self, method, path, *, allow_missing=False, **kwargs):
        key = os.environ.get("KERNEL_API_KEY")
        if not key:
            raise ValueError("Set KERNEL_API_KEY in .env or the process environment")
        try:
            response = httpx.request(
                method,
                "https://api.onkernel.com" + path,
                headers={"Authorization": f"Bearer {key}"},
                timeout=30,
                **kwargs,
            )
        except httpx.HTTPError:
            # Request exceptions can contain bearer URLs; retain no raw exception text.
            raise RuntimeError("The Kernel API request could not complete") from None
        if response.is_error and not (allow_missing and response.status_code == 404):
            code = ""
            try:
                value = response.json().get("code")
                if isinstance(value, str) and re.fullmatch(r"[a-z_]{1,64}", value) and value != key:
                    code = f" ({value})"
            except (ValueError, AttributeError):
                pass
            # Response messages/details may echo credentials; only expose the bounded error code.
            raise RuntimeError(f"Kernel returned HTTP {response.status_code}{code}")
        return response

    def launch(self, run):
        config = provider_config(run)
        self.validate_config(config)
        # The collection owns browser state. Never attach a retained Kernel profile or pool.
        result = self.api("POST", "/browsers", json={
            "headless": config.get("headless", False),
            "stealth": config.get("stealth", True),
            "timeout_seconds": config.get("timeout_seconds", 1800),
            **{key: config[key] for key in ("region", "proxy") if key in config},
        }).json()
        session_id, cdp = result.get("session_id"), result.get("cdp_ws_url")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("Kernel did not return a browser session ID")
        if not isinstance(cdp, str) or urlsplit(cdp).scheme != "wss":
            self.api("DELETE", "/browsers/" + quote(session_id, safe=""), allow_missing=True)
            raise RuntimeError("Kernel did not return a secure browser connection")
        return {"session_id": session_id, "cdp": cdp}

    def live_url(self, run):
        if provider_config(run).get("headless", False):
            return None
        result = self.api("GET", "/browsers/" + quote(run.runtime["session_id"], safe="")).json()
        live = result.get("browser_live_view_url")
        return live if isinstance(live, str) and urlsplit(live).scheme == "https" else None

    def stop(self, run):
        path = "/browsers/" + quote(run.runtime["session_id"], safe="")
        self.api("DELETE", path, allow_missing=True)
        deadline = time.monotonic() + 15
        while True:
            result = self.api("GET", path, params={"include_deleted": "true"}, allow_missing=True)
            if result.status_code == 404 or result.json().get("deleted_at"):
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("Kernel has not confirmed that the browser stopped")
            time.sleep(.25)
