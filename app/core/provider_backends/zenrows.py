"""ZenRows lifecycle and its provider-controlled browser fingerprint."""

import json
import os
import secrets
import signal
import subprocess
import time
from contextlib import suppress
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlencode

from .. import storage
from ..providers import CDPAdapter, provider_config


class ZenRows(CDPAdapter):
    network_fields: ClassVar[dict] = {'proxy_country': 'Country · us', 'proxy_region': 'Region · na'}

    def validate_handoff(self, config):
        raise ValueError('ZenRows currently requires a connection relay; direct session handoff is unavailable')

    label = "ZenRows"
    description = "An isolated cloud browser with residential proxies and a provider-managed fingerprint."
    config_help = "Set proxy_country or proxy_region and session_ttl_minutes (1–15). Store ZENROWS_API_KEY in .env. ZenRows controls viewport, user agent and device identity; these persona settings cannot be applied. Native IndexedDB and OPFS transfer are not supported."
    fixed_settings: ClassVar[set] = {"viewport", "window", "screen", "mobile", "userAgent", "userAgentMetadata", "platform", "acceptLanguage", "maxTouchPoints"}

    def validate_config(self, config):
        unknown = set(config) - {"proxy_country", "proxy_region", "session_ttl_minutes"}
        if unknown:
            raise ValueError("Unsupported ZenRows connection settings: " + ", ".join(sorted(unknown)))
        ttl = config.get("session_ttl_minutes", 15)
        if type(ttl) is not int or not 1 <= ttl <= 15:
            raise ValueError("ZenRows session_ttl_minutes must be 1–15")
        country = config.get("proxy_country")
        if country is not None and (not isinstance(country, str) or len(country) != 2 or not country.isascii() or not country.isalpha()):
            raise ValueError("ZenRows proxy_country must be a two-letter country code")
        if "proxy_region" in config and config["proxy_region"] not in {"eu", "na", "ap", "sa", "af", "me"}:
            raise ValueError("ZenRows proxy_region must be eu, na, ap, sa, af or me")
        if country and config.get("proxy_region"):
            raise ValueError("Choose ZenRows proxy_country or proxy_region")

    def settings_support(self, config):
        support = super().settings_support(config)
        for key in self.fixed_settings:
            support[key] = {"supported": False, "detail": "ZenRows controls this browser fingerprint value and ignores CDP overrides."}
        support["locale"] = {"supported": True, "detail": "CDP overrides the Intl locale; navigator language and Accept-Language remain provider controlled."}
        return support

    def driver_settings(self, settings):
        return {**{key: value for key, value in settings.items() if key not in self.fixed_settings}, "viewport": None}

    def launch(self, run):
        config = provider_config(run)
        self.validate_config(config)
        key = os.environ.get("ZENROWS_API_KEY")
        if not key:
            raise ValueError("Set ZENROWS_API_KEY in .env or the process environment")
        query = {"apikey": key, "session_ttl": f"{config.get('session_ttl_minutes', 15)}m"}
        for field in ("proxy_country", "proxy_region"):
            if config.get(field):
                query[field] = config[field].lower()
        work = storage.private_dir(storage.data_root() / "runs" / str(run.id))
        ready = work / "zenrows-bridge.ready"
        ready.unlink(missing_ok=True)
        launched = False
        with (work / "zenrows-bridge.log").open("w") as log:
            process = subprocess.Popen(
                ["uv", "run", "python", "-m", "app.core.provider_backends.zenrows_bridge"],
                cwd=Path(__file__).resolve().parents[3],
                stdin=subprocess.PIPE, stdout=log, stderr=log, text=True,
                start_new_session=True,
            )
            try:
                process.stdin.write(json.dumps({"endpoint": "wss://browser.zenrows.com?" + urlencode(query), "ready": str(ready), "token": secrets.token_urlsafe(32)}))
                process.stdin.close()
                deadline = time.monotonic() + 40
                while not ready.exists():
                    if process.poll() is not None:
                        raise RuntimeError("ZenRows could not start the browser connection keeper")
                    if time.monotonic() >= deadline:
                        raise RuntimeError("ZenRows did not make the browser connection ready")
                    time.sleep(.05)
                result = json.loads(ready.read_text())
                if not isinstance(result, dict) or not isinstance(result.get("cdp"), str) or type(result.get("bridge_pid")) is not int:
                    raise RuntimeError("ZenRows returned invalid browser connection readiness data")
                if process.poll() is not None:
                    raise RuntimeError("ZenRows browser connection keeper exited during startup")
                launched = True
                return {**result, "session_id": f"zenrows-{run.id}"}
            finally:
                if not process.stdin.closed:
                    with suppress(BrokenPipeError):
                        process.stdin.close()
                if not launched:
                    # uv and the Python keeper share this newly owned process group.
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        with suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                    ready.unlink(missing_ok=True)

    def stop(self, run):
        from ..providers import browser_command
        try:
            browser_command("close", run)
        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
            # CDP may already have expired. The keeper's signal path closes its
            # upstream directly, independent of the downstream browser command.
            pass
        finally:
            with suppress(ProcessLookupError):
                os.kill(int(run.runtime["bridge_pid"]), signal.SIGTERM)
        deadline = time.monotonic() + 15
        while True:
            result = subprocess.run(["ps", "-p", str(int(run.runtime["bridge_pid"])), "-o", "stat="], capture_output=True, text=True, check=False)
            if result.returncode not in (0, 1):
                raise RuntimeError("Could not confirm the ZenRows connection keeper stopped")
            if not result.stdout.strip() or result.stdout.strip().startswith("Z"):
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("ZenRows connection keeper has not stopped")
            time.sleep(.05)
