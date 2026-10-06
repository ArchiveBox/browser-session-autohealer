"""Fresh Kernel cloud browsers, driven by the shared CDP state-transfer tools."""

import hashlib
import json
import os
import re
import time
from typing import ClassVar
from urllib.parse import quote, urlsplit

import httpx

from ..providers import CDPAdapter, provider_config


class Kernel(CDPAdapter):
    network_config_fields: ClassVar[set] = {'proxy', 'proxy_country', 'proxy_state', 'proxy_city'}
    network_fields: ClassVar[dict] = {'proxy.name': 'Saved proxy name', 'proxy_country': 'Country', 'proxy_state': 'State', 'proxy_city': 'City'}

    def location_options(self, location, config):
        country = location.get('country') or config.get('proxy_country')
        if not country or not any(location.get(field) for field in ('country', 'state', 'city')):
            return {}
        country = country.upper()
        state = (location.get('state') or '').upper().removeprefix(country + '-')
        return {
            'proxy': None,
            'proxy_country': country,
            'proxy_state': state if re.fullmatch('[A-Z]{2}', state) else None,
            'proxy_city': re.sub(r'\s+', '', location['city']).lower() if location.get('city') else None,
        }

    def session_lifetime(self, config):
        return config.get('timeout_seconds', 1800)

    label = "Kernel"
    description = "An isolated Kernel cloud browser with portable site data and a live view."
    config_help = (
        "Store KERNEL_API_KEY in the ignored .env file. Optional settings: headless (false), "
        "stealth (true), timeout_seconds (1800), region (us-east, eu-west, ap-southeast), "
        "proxy ({mode: direct/default} or {id/name: an existing Kernel proxy}). Stealth uses "
        "Kernel's default stealth proxy when proxy is omitted. Alternatively set proxy_country "
        "(uppercase ISO2), proxy_state (two-letter code), proxy_city (no spaces) to reuse or "
        "create a managed residential proxy. Native profile, IndexedDB "
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
        unknown = set(config) - {"headless", "stealth", "timeout_seconds", "region", "proxy", "proxy_country", "proxy_state", "proxy_city"}
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
        if any(key in config for key in ('proxy_country', 'proxy_state', 'proxy_city')):
            if 'proxy' in config:
                raise ValueError('Choose Kernel proxy or managed proxy geography')
            if not isinstance(config.get('proxy_country'), str) or not re.fullmatch('[A-Z]{2}', config['proxy_country']):
                raise ValueError('Kernel proxy_country must be an uppercase two-letter country code')
            if 'proxy_state' in config and (not isinstance(config['proxy_state'], str) or not re.fullmatch('[A-Z]{2}', config['proxy_state'])):
                raise ValueError('Kernel proxy_state must be an uppercase two-letter state code')
            if 'proxy_city' in config and (not isinstance(config['proxy_city'], str) or not config['proxy_city'] or re.search(r'\s', config['proxy_city'])):
                raise ValueError('Kernel proxy_city must be a city name without spaces')

    def location_proxy(self, config):
        geography = {key: config['proxy_' + key] for key in ('country', 'state', 'city') if config.get('proxy_' + key)}
        # Proxies are durable configuration, not one resource per browser.
        name = 'autohealer-location-' + hashlib.sha256(json.dumps(geography, sort_keys=True).encode()).hexdigest()[:24]
        offset = 0
        while True:
            response = self.api('GET', '/proxies', params={'name': name, 'limit': 100, 'offset': offset})
            proxies = response.json()
            if not isinstance(proxies, list):
                raise RuntimeError('Kernel did not return a proxy list')  # noqa: TRY004 - malformed remote API response
            for proxy in proxies:
                if proxy.get('type') == 'residential' and proxy.get('config') == geography and not proxy.get('bypass_hosts') and proxy.get('id'):
                    return {'id': proxy['id']}
            if response.headers.get('X-Has-More', '').lower() != 'true':
                break
            offset += 100
        result = self.api('POST', '/proxies', json={'name': name, 'type': 'residential', 'config': geography}).json()
        if not isinstance(result.get('id'), str) or not result['id']:
            raise RuntimeError('Kernel did not return a managed location proxy ID')
        return {'id': result['id']}

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
        config = dict(provider_config(run))
        self.validate_config(config)
        # The collection owns browser state. Never attach a retained Kernel profile or pool.
        options = {
            "headless": config.get("headless", False),
            "stealth": config.get("stealth", True),
            "timeout_seconds": config.get("timeout_seconds", 1800),
            **({'region': config['region']} if 'region' in config else {}),
        }
        routing = None
        try:
            proxy = self.location_proxy(config) if config.get('proxy_country') else config.get('proxy')
            result = self.api('POST', '/browsers', json={**options, **({'proxy': proxy} if proxy else {})}).json()
        except RuntimeError as error:
            # Managed location hints are best effort on plans without proxy access.
            # Required conditions still verify the observed egress before navigation.
            if not config.get('proxy_country') or str(error) != 'Kernel returned HTTP 403 (insufficient_plan)':
                raise
            routing = [{'code': 'location_routing_unavailable',
                'detail': 'Proxy location requires a higher Kernel plan; using the default route.'}]
            for field in ('proxy_country', 'proxy_state', 'proxy_city'):
                config.pop(field, None)
            run.runtime['provider_config'] = config
            result = self.api('POST', '/browsers', json=options).json()
        session_id, cdp = result.get("session_id"), result.get("cdp_ws_url")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("Kernel did not return a browser session ID")
        if not isinstance(cdp, str) or urlsplit(cdp).scheme != "wss":
            self.api("DELETE", "/browsers/" + quote(session_id, safe=""), allow_missing=True)
            raise RuntimeError("Kernel did not return a secure browser connection")
        return {"session_id": session_id, "cdp": cdp, **({'provider_location_diagnostics': routing} if routing else {})}

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
