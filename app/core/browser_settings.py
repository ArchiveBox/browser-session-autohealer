"""Provider-neutral presentation and editing of a persona's desired browser settings.

Adapters supply support and defaults. This inventory is not a live browser measurement.
The teleport recorder's categories inform the inventory; they do not imply local support.
"""

import math
from copy import deepcopy
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

GROUPS = [
    (
        "Location & language",
        "◎",
        [
            ("timezone", "Time zone"),
            ("locale", "Language / locale"),
            ("acceptLanguage", "Requested content languages"),
            ("geolocation", "Location"),
        ],
    ),
    (
        "Screen & appearance",
        "▣",
        [
            ("viewport", "Browser window"),
            ("screen", "Screen dimensions & depth"),
            ("window", "Outer window"),
            ("zoom", "Page zoom"),
            ("mobile", "Mobile emulation"),
            ("colorScheme", "Color scheme"),
            ("reducedMotion", "Motion preference"),
            ("contrast", "Contrast preference"),
            ("forcedColors", "Forced colors"),
            ("reducedTransparency", "Transparency preference"),
        ],
    ),
    (
        "Browser identity",
        "◈",
        [
            ("userAgent", "Browser identification"),
            ("platform", "Operating system identity"),
            ("userAgentMetadata", "Browser client hints"),
            ("hardwareConcurrency", "Reported processor cores"),
            ("deviceMemory", "Reported memory (GB)"),
            ("maxTouchPoints", "Touch points"),
            ("languages", "Browser languages"),
            ("randomSeed", "Randomness seed"),
        ],
    ),
    (
        "Site permissions",
        "⚿",
        [
            ("permissions.geolocation", "Location access"),
            ("permissions.notifications", "Notifications"),
            ("permissions.camera", "Camera"),
            ("permissions.microphone", "Microphone"),
            ("permissions.clipboard-read", "Read clipboard"),
            ("permissions.clipboard-write", "Write clipboard"),
        ],
    ),
    (
        "Saved site data",
        "▤",
        [
            ("cookies", "Cookies & sign-in"),
            ("localStorage", "Site preferences"),
            ("sessionStorage", "Tab session data"),
            ("indexedDB", "Site databases"),
            ("opfs", "Site files"),
        ],
    ),
    (
        "Tabs & page state",
        "▧",
        [
            ("urls", "Open tabs & addresses"),
            ("uiState.scroll", "Page & element scroll positions"),
            ("uiState.forms", "Form values & selections"),
            ("uiState.focus", "Focused element"),
        ],
    ),
    (
        "Advanced browser behavior",
        "⚙",
        [
            ("chromeArgs", "Extra launch arguments"),
            ("requiredCapabilities", "Required state preservation"),
            ("recording", "Browser recording"),
            ("streamReplay", "Action replay / streaming"),
            ("extension", "Browser extensions"),
        ],
    ),
]

# Only controls for the app's current editable persona configuration contract.
EDIT_GROUPS = [
    (
        "Location & language",
        "◎",
        [
            ("timezone", "Time zone", "text", "America/Los_Angeles", []),
            ("locale", "Language / locale", "text", "en-US", []),
            ("geolocation.latitude", "Latitude", "number", "−90 to 90", []),
            ("geolocation.longitude", "Longitude", "number", "−180 to 180", []),
            ("geolocation.accuracy", "Location accuracy (meters)", "number", "0 or greater", []),
        ],
    ),
    (
        "Screen & appearance",
        "▣",
        [
            ("viewport.width", "Width (pixels)", "number", "Browser default", []),
            ("viewport.height", "Height (pixels)", "number", "Browser default", []),
            ("viewport.deviceScaleFactor", "Display scale", "number", "Browser default", []),
            (
                "colorScheme",
                "Color scheme",
                "select",
                "",
                [("light", "Light"), ("dark", "Dark"), ("no-preference", "No preference")],
            ),
            (
                "reducedMotion",
                "Motion preference",
                "select",
                "",
                [("reduce", "Reduce motion"), ("no-preference", "No preference")],
            ),
        ],
    ),
    (
        "Browser identity",
        "◈",
        [
            (
                "userAgent",
                "Browser identification (user agent)",
                "text",
                "Use the browser's own identity",
                [],
            ),
        ],
    ),
]


def _get(config, path):
    value = config
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _display(key, value):
    if key in {
        "randomSeed",
        "cookies",
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "opfs",
        "urls",
        "extension",
    } or key.startswith("uiState."):
        return "Saved; contents not displayed"
    if key == "chromeArgs":
        return (
            f"{len(value)} arguments; values not displayed"
            if isinstance(value, list)
            else "Saved; values not displayed"
        )
    if key == "viewport" and isinstance(value, dict):
        scale = value.get('deviceScaleFactor')
        return f"{value.get('width', '?')} × {value.get('height', '?')} px · {round(scale, 3) if isinstance(scale, (float, int)) else '?'}× scale"
    if key in {"screen", "window"} and isinstance(value, dict):
        text = f"{value.get('width', '?')} × {value.get('height', '?')} px"
        return text + (f" · {value['colorDepth']}-bit color" if value.get("colorDepth") else "")
    if key == "userAgentMetadata" and isinstance(value, dict):
        return " · ".join(str(value[k]) for k in ("platform", "platformVersion", "architecture", "bitness") if value.get(k)) + " · " + ", ".join(f"{b['brand']} {b['version']}" for b in value.get("brands", []))
    if key in {"languages", "siteScope"} and isinstance(value, list):
        return ", ".join(value)
    if key == "geolocation" and isinstance(value, dict):
        return f"{value.get('latitude', '?')}, {value.get('longitude', '?')} · ±{value.get('accuracy', '?')} m"
    if isinstance(value, bool):
        return "On" if value else "Off"
    if key == "requiredCapabilities" and isinstance(value, list):
        return ", ".join(str(v) for v in value) or "None required"
    if isinstance(value, (str, int, float)):
        return str(value)
    return "Saved; structured value"


def inventory(config, *, support=None, checkpoint=None):
    """Return grouped values with independent source and adapter support labels.

    support maps inventory IDs to {supported: bool|None, detail: str, default: value}.
    None means unknown or differing adapter behavior. Checkpoint summaries contain counts,
    never cookie/storage values. No provider-specific knowledge belongs in this function.
    """
    config = config if isinstance(config, dict) else {}
    support = support or {}
    summary = checkpoint.summary if checkpoint else {}
    groups = []
    for title, icon, fields in GROUPS:
        rows = []
        for key, label in fields:
            declaration = support.get(key, {})
            value = _get(config, key)
            source, display = "Unknown", "Not recorded"
            if value is not None:
                source, display = "Saved", _display(key, value)
            elif "default" in declaration:
                source, display = "Default", _display(key, declaration["default"])
            elif key == "cookies" and "cookies" in summary:
                source, display = (
                    "Recorded",
                    f"{summary['cookies']} cookies in this saved browser version",
                )
            supported = declaration.get("supported")
            rows.append(
                {
                    "key": key,
                    "label": label,
                    "value": display,
                    "source": source,
                    "support": "Supported"
                    if supported is True
                    else "Not supported"
                    if supported is False
                    else "Unknown",
                    "tone": "success" if supported is True else "neutral",
                    "detail": declaration.get(
                        "detail", "No adapter declaration is available for this setting."
                    ),
                }
            )
        groups.append({"title": title, "icon": icon, "rows": rows})
    known = {key.split(".")[0] for _, _, fields in GROUPS for key, _ in fields}
    return {"groups": groups, "other_count": len(set(config) - known)}


def edit_context(config=None, values=None):
    config = config if isinstance(config, dict) else {}
    groups = []
    for title, icon, fields in EDIT_GROUPS:
        rows = []
        for key, label, kind, placeholder, options in fields:
            name = "browser_" + key.replace(".", "_")
            saved = _get(config, key)
            value = values.get(name, "") if values is not None else saved
            rows.append(
                {
                    "key": key,
                    "name": name,
                    "label": label,
                    "type": kind,
                    "placeholder": placeholder,
                    "options": options,
                    "value": value if isinstance(value, (str, int, float)) else "",
                }
            )
        groups.append({"title": title, "icon": icon, "fields": rows})
    required = config.get("requiredCapabilities")
    return {
        "groups": groups,
        "argument_count": len(config.get("chromeArgs", []))
        if isinstance(config.get("chromeArgs"), list)
        else 0,
        "required": ", ".join(required)
        if isinstance(required, list) and all(isinstance(v, str) for v in required)
        else "",
        "required_value": values.get("browser_requiredCapabilities", "")
        if values is not None
        else ", ".join(required)
        if isinstance(required, list) and all(isinstance(v, str) for v in required)
        else "",
    }


def parse_fields(data, current=None):
    """Edit known keys on a deep copy; preserve all unfamiliar keys, including nested ones.

    An absent field leaves its value unchanged. Blank scalar controls remove the override.
    Launch arguments are never reflected into HTML and need an explicit replacement action.
    """
    result = deepcopy(current) if isinstance(current, dict) else {}
    for key in ("timezone", "locale", "userAgent", "colorScheme", "reducedMotion"):
        name = "browser_" + key
        if name not in data:
            continue
        value = data[name].strip()
        if key == "timezone" and value:
            try:
                ZoneInfo(value)
            except ZoneInfoNotFoundError, ValueError:
                raise ValueError("Enter a valid time zone, such as America/Los_Angeles.") from None
        if key == "colorScheme" and value not in ("", "light", "dark", "no-preference"):
            raise ValueError("Choose a listed color scheme.")
        if key == "reducedMotion" and value not in ("", "reduce", "no-preference"):
            raise ValueError("Choose a listed motion preference.")
        if value:
            result[key] = value
        else:
            result.pop(key, None)
    for parent, fields in (
        ("viewport", ("width", "height", "deviceScaleFactor")),
        ("geolocation", ("latitude", "longitude", "accuracy")),
    ):
        names = {field: "browser_" + parent + "_" + field for field in fields}
        if not any(name in data for name in names.values()):
            continue
        saved = result.get(parent, {})
        nested = deepcopy(saved) if isinstance(saved, dict) else {}
        for field, name in names.items():
            if name not in data:
                continue
            raw = data[name].strip()
            if not raw:
                nested.pop(field, None)
                continue
            try:
                value = float(raw)
            except ValueError:
                raise ValueError(f"Enter a number for {parent} {field}.") from None
            if not math.isfinite(value):
                raise ValueError(f"Enter a finite number for {parent} {field}.")
            if parent == "viewport" and (
                value <= 0 or (field != "deviceScaleFactor" and not value.is_integer())
            ):
                raise ValueError(
                    "Window dimensions must be positive whole pixels; display scale must be positive."
                )
            if parent == "geolocation" and (
                (field == "latitude" and abs(value) > 90)
                or (field == "longitude" and abs(value) > 180)
                or (field == "accuracy" and value < 0)
            ):
                raise ValueError(
                    "Use latitude −90…90, longitude −180…180, and nonnegative accuracy."
                )
            nested[field] = int(value) if value.is_integer() else value
        if nested and not all(field in nested for field in fields):
            raise ValueError(
                f"Fill all three {parent} fields, or clear all three overrides. Other saved {parent} keys are preserved."
            )
        if nested:
            result[parent] = nested
        else:
            result.pop(parent, None)
    requirements_mode = data.get("browser_requirements_action", "keep")
    if requirements_mode == "default":
        result.pop("requiredCapabilities", None)
    elif requirements_mode == "none":
        result["requiredCapabilities"] = []
    elif requirements_mode == "replace":
        values = [
            part.strip()
            for part in data.get("browser_requiredCapabilities", "").split(",")
            if part.strip()
        ]
        if not values:
            raise ValueError(
                "Enter a required capability or choose the default / no requirements option."
            )
        result["requiredCapabilities"] = list(dict.fromkeys(values))
    elif requirements_mode != "keep":
        raise ValueError("Choose how to update state preservation requirements.")
    mode = data.get("browser_arguments_action", "keep")
    if mode == "clear":
        result.pop("chromeArgs", None)
    elif mode == "replace":
        arguments = [
            line.strip() for line in data.get("browser_arguments", "").splitlines() if line.strip()
        ]
        if any(not arg.startswith("--") for arg in arguments):
            raise ValueError("Enter one Chrome argument per line, beginning with --.")
        result["chromeArgs"] = arguments
    elif mode != "keep":
        raise ValueError("Choose whether to keep, clear, or replace launch arguments.")
    return result
