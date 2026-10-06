"""Browserless standard sessions, retained across independent CDP clients."""

import json
import os
import subprocess
import time
from typing import ClassVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from websockets.exceptions import InvalidStatus, WebSocketException
from websockets.sync.client import connect

from ..providers import CDPAdapter, provider_config


class Browserless(CDPAdapter):
    network_config_fields: ClassVar[set] = {'residential_proxies', 'proxy_country', 'proxy_state', 'proxy_city'}
    network_fields: ClassVar[dict] = {'proxy_country': 'Country', 'proxy_state': 'State', 'proxy_city': 'City'}

    def location_options(self, location, config):
        country = location.get('country') or config.get('proxy_country')
        if not country or not any(location.get(field) for field in ('country', 'state', 'city')):
            return {}
        # Browserless requires full subdivision names rather than ISO codes.
        state = location.get('state_name') or location.get('state') or ''
        if len(state) <= 3 or state.startswith(country.upper() + '-'):
            state = None
        return {
            'residential_proxies': True,
            'proxy_country': country.upper(),
            'proxy_state': state or None,
            'proxy_city': location.get('city') or None,
        }

    def session_lifetime(self, config):
        return config.get('session_timeout_ms', 120000) // 1000

    label = "Browserless.io"
    description = "An isolated cloud browser with reconnectable CDP and portable site data."
    config_help = "Set region (sfo, lon, ams), stealth, residential_proxies, proxy_country and session_timeout_ms. proxy_state and proxy_city require the Scale plan and use full names. Store BROWSERLESS_API_KEY in .env. The timeout must fit your plan. Native IndexedDB and OPFS transfer are not supported."

    def settings_support(self, config):
        support = super().settings_support(config)
        for key in ("platform", "hardwareConcurrency", "colorScheme"):
            support[key] = {
                "supported": False,
                "detail": "Browserless stealth replaces this value after navigation despite accepting the CDP override; standard Chromium may honor it.",
            }
        support["deviceMemory"] = {"supported": False, "detail": "Reported memory is controlled by the cloud browser; the shared CDP driver cannot override it."}
        support["viewport"] = {"supported": True, "detail": "CDP sets viewport geometry, but stealth adds pixel variation and pixel-ratio rounding; exact fingerprint equality is not supported."}
        support["window"] = {"supported": False, "detail": "Stealth can replace exposed outer window dimensions after navigation despite accepting the CDP override."}
        support["screen"] = {"supported": False, "detail": "Stealth can replace screen dimensions and depth after navigation; complete screen fingerprint transfer is not supported."}
        return support

    def validate_config(self, config):
        unknown = set(config) - {"region", "stealth", "residential_proxies", "session_timeout_ms", *self.network_fields}
        if unknown:
            raise ValueError("Unsupported Browserless connection settings: " + ", ".join(sorted(unknown)))
        if config.get("region", "sfo") not in {"sfo", "lon", "ams"}:
            raise ValueError("Browserless region must be sfo, lon or ams")
        for key in ("stealth", "residential_proxies"):
            if key in config and type(config[key]) is not bool:
                raise ValueError(f"Browserless {key} must be true or false")
        timeout = config.get("session_timeout_ms", 120000)
        if type(timeout) is not int or not 60000 <= timeout <= 3600000:
            raise ValueError("Browserless session_timeout_ms must be 60000–3600000")
        country = config.get("proxy_country")
        if country is not None and (not isinstance(country, str) or len(country) != 2 or not country.isascii() or not country.isalpha()):
            raise ValueError("Browserless proxy_country must be a two-letter country code")
        if country and not config.get("residential_proxies", True):
            raise ValueError("Browserless proxy_country requires residential_proxies")
        for key in ('proxy_state', 'proxy_city'):
            if config.get(key) is not None and (not country or not isinstance(config[key], str) or not config[key].strip()):
                raise ValueError(f'{key} requires a country and a nonempty name')

    def fine_location_available(self, config, key):
        """Read plan capability without starting a browser or exposing its token."""
        try:
            response = httpx.get(
                f"https://production-{config.get('region', 'sfo')}.browserless.io/proxy/cities",
                params={'country': config['proxy_country'].upper(), 'token': key},
                timeout=30,
            )
        except httpx.RequestError:
            raise RuntimeError('Browserless could not check fine proxy location support') from None
        if response.status_code == 200:
            return True
        if response.status_code in {400, 401} and 'city proxying is not available on your plan' in response.text.lower():
            return False
        raise RuntimeError(f'Browserless could not check fine proxy location support (HTTP {response.status_code})')

    def launch(self, run):
        config = dict(provider_config(run))
        self.validate_config(config)
        key = os.environ.get("BROWSERLESS_API_KEY")
        if not key:
            raise ValueError("Set BROWSERLESS_API_KEY in .env or the process environment")
        if (config.get('proxy_state') or config.get('proxy_city')) and not self.fine_location_available(config, key):
            config.pop('proxy_state', None)
            config.pop('proxy_city', None)
            run.runtime['provider_config'] = config
            run.runtime.setdefault('provider_location_diagnostics', []).append({
                'code': 'fine_location_unavailable',
                'detail': 'Browserless plan does not support state/city routing; using the requested country. Required location still needs observed-IP verification.',
            })
        timeout = config.get("session_timeout_ms", 120000)
        query = {"token": key, "timeout": timeout}
        if config.get("residential_proxies", True):
            query.update(proxy="residential", proxySticky="true")
            if config.get("proxy_country"):
                query["proxyCountry"] = config["proxy_country"].lower()
            for field in ('state', 'city'):
                if config.get('proxy_' + field):
                    query['proxy' + field.title()] = config['proxy_' + field].replace(' ', '').lower()
        route = "stealth" if config.get("stealth", True) else "chromium"
        endpoint = f"wss://production-{config.get('region', 'sfo')}.browserless.io/{route}?{urlencode(query)}"
        try:
            with connect(endpoint, open_timeout=30, close_timeout=5) as ws:
                def command(number, method, params=None, session=None):
                    message = {"id": number, "method": method, "params": params or {}}
                    if session:
                        message["sessionId"] = session
                    ws.send(json.dumps(message))
                    while True:
                        response = json.loads(ws.recv(timeout=30))
                        if response.get("id") == number:
                            if response.get("error"):
                                raise RuntimeError(f"Browserless rejected {method}")
                            return response["result"]

                try:
                    target = command(1, "Target.createTarget", {"url": "about:blank"})["targetId"]
                    session = command(2, "Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
                    result = command(3, "Browserless.reconnect", {"timeout": timeout}, session)
                    if result.get("error") or not result.get("browserWSEndpoint"):
                        raise RuntimeError("Browserless refused the session reconnect window; check your plan timeout limit")
                    parts = urlsplit(result["browserWSEndpoint"])
                    if parts.scheme != "wss" or not parts.hostname or not parts.hostname.endswith(".browserless.io"):
                        raise RuntimeError("Browserless returned an invalid reconnect endpoint")
                    query = dict(parse_qsl(parts.query))
                    query["token"] = key
                    cdp = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
                    return {"cdp": cdp, "session_id": parts.path.rsplit("/", 1)[-1]}
                except Exception:
                    ws.send(json.dumps({"id": 4, "method": "Browser.close"}))
                    raise
        except (WebSocketException, OSError, TimeoutError, ValueError, KeyError) as error:
            # WebSocket exceptions may embed bearer URLs. Never surface them.
            status = getattr(getattr(error, "response", None), "status_code", None)
            detail = f" (HTTP {status})" if status else ""
            raise RuntimeError("Browserless could not create a reconnectable session" + detail) from None

    def stop(self, run):
        from ..providers import browser_command
        try:
            browser_command("close", run)
        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
            # A TTL-expired browser cannot acknowledge close. Resource absence
            # must still be independently confirmed at its reconnect endpoint.
            pass
        deadline = time.monotonic() + 15
        detail = "the reconnect endpoint still accepted connections"
        while True:
            try:
                with connect(run.runtime["cdp"], open_timeout=5, close_timeout=3):
                    # Browser.close is acknowledged before cloud routing is removed.
                    pass
            except InvalidStatus as error:
                status = error.response.status_code
                if status == 404:
                    return
                if status != 429 and status < 500:
                    raise RuntimeError(f"Browserless could not confirm session termination (HTTP {status})") from None
                detail = f"HTTP {status}"
            except (WebSocketException, OSError, TimeoutError) as error:
                # Cloud shutdown can interrupt a handshake before route removal
                # becomes visible as 404. Poll absence; never infer it from EOF.
                detail = type(error).__name__
            if time.monotonic() >= deadline:
                raise RuntimeError("Browserless could not confirm session termination: " + detail)
            time.sleep(.1)
