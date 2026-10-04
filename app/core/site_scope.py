"""One import/export boundary for every source and provider. No cookie values in logs."""

from copy import deepcopy
from urllib.parse import urlsplit

GLOBAL_SETTINGS = {
    "userAgent", "userAgentMetadata", "platform", "locale", "languages", "acceptLanguage",
    "timezone", "viewport", "screen", "window", "mobile", "touch", "maxTouchPoints",
    "colorScheme", "reducedMotion", "contrast", "forcedColors", "reducedTransparency",
    "geolocation", "hardwareConcurrency", "deviceMemory", "fonts", "fontSettings", "zoom",
    "requiredCapabilities", "randomSeed", "source", "permissions",
}


def normalize_sites(sites):
    if isinstance(sites, str):
        sites = sites.replace(",", "\n").splitlines()
    if sites is None:
        return None  # Explicit all-sites import, never the empty selection.
    result = []
    for site in sites:
        site = site.strip().lower().rstrip(".")
        if not site:
            continue
        if "/" in site or "*" in site or ":" in site or "@" in site:
            raise ValueError("Choose site domains, without paths, ports, protocols or wildcards")
        if "." not in site and site != "localhost":
            raise ValueError("Choose a full site domain")
        host = site.encode("idna").decode()
        if urlsplit("https://" + host).hostname != host:
            raise ValueError("Invalid site domain")
        if host not in result:
            result.append(host)
    if not result:
        raise ValueError("Select at least one site, or explicitly choose all sites")
    return result


def matches(host, sites):
    host = host.lower().lstrip(".").rstrip(".")
    return sites is None or any(host == s or host.endswith("." + s) for s in sites)


def selected_url(url, sites):
    parsed = urlsplit(url)
    return parsed.scheme in {"http", "https"} and matches(parsed.hostname or "", sites)


def select_state(state, sites):
    sites = normalize_sites(sites)
    if sites is None:
        return deepcopy(state)
    settings = {k: deepcopy(v) for k, v in state.get("settings", {}).items() if k in GLOBAL_SETTINGS}
    if "urls" in state.get("settings", {}):
        settings["urls"] = [u for u in state["settings"]["urls"] if selected_url(u, sites)]
    # Per-origin permission maps must obey the same boundary as origin storage.
    if isinstance(settings.get("permissions"), dict):
        settings["permissions"] = {
            k: v for k, v in settings["permissions"].items()
            if "://" not in k or selected_url(k, sites)
        }
    settings["siteScope"] = sites
    return {
        "cookies": [deepcopy(c) for c in state.get("cookies", [])
                    if matches(c.get("domain", ""), sites)
                    and (not c.get("partitionKey") or selected_url(c["partitionKey"].get("topLevelSite", ""), sites))],
        "origins": [deepcopy(o) for o in state.get("origins", []) if selected_url(o.get("origin", ""), sites)],
        "settings": settings,
        "siteScope": sites,
    }


def checkin_state(base, returned, sites):
    """Replace only shared site data; excluded sites were never checked out."""
    from .storage import cookie_key

    if sites is None:
        return returned
    selected = select_state(base, sites)
    returned = select_state(returned, sites)
    cookie_keys = {cookie_key(c) for c in selected['cookies']}
    origins = {o['origin'] for o in selected['origins']}
    settings = {**base.get('settings', {}), **{k: v for k, v in returned['settings'].items() if k != 'siteScope'}}
    for key in ('permissions', 'urls'):
        previous = base.get('settings', {}).get(key)
        current = returned['settings'].get(key)
        if key == 'permissions' and isinstance(previous, dict) and isinstance(current, dict):
            settings[key] = {**{k: v for k, v in previous.items()
                               if '://' in k and not selected_url(k, sites)}, **current}
        elif key == 'urls' and isinstance(previous, list) and isinstance(current, list):
            settings[key] = [u for u in previous if not selected_url(u, sites)] + current
    return {**base, 'settings': settings,
            'cookies': [c for c in base.get('cookies', []) if cookie_key(c) not in cookie_keys] + returned['cookies'],
            'origins': [o for o in base.get('origins', []) if o['origin'] not in origins] + returned['origins']}
