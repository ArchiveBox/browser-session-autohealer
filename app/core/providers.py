"""Provider differences end at lifecycle and state-transfer capabilities."""

import json
import os
import re
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar

import httpx
from plain.exceptions import ValidationError

from . import storage


class ProviderAPIError(RuntimeError):
    """Keep a safe status for recovery UI without exposing upstream diagnostics."""

    def __init__(self, message, public_message):
        super().__init__(message)
        self.public_message = public_message


def provider_config(run):
    return run.runtime.get("provider_config", run.provider.config)


def browser_invocation(action, run, **payload):
    return adapter(run.provider, kind=run.runtime.get("provider_kind")).browser_invocation(
        action, run, **payload
    )


def host_browser_invocation(action, run, **payload):
    root = Path(__file__).resolve().parents[3]
    helper = os.environ.get(
        "ACCOUNT_CHECKER_CHROME_HELPER",
        str(root / "abx-plugins/abx_plugins/plugins/chrome/chrome_utils.js"),
    )
    modules = os.environ.get(
        "NODE_MODULES_DIR", str(Path.home() / ".config/abx/lib/npm/node_modules")
    )
    work = storage.private_dir(storage.data_root() / "runs" / str(run.id))
    env = {
        **os.environ,
        "ACCOUNT_CHECKER_CHROME_HELPER": helper,
        "NODE_MODULES_DIR": modules,
        "PERSONAS_DIR": str(work / "personas"),
        "ACTIVE_PERSONA": "working",
    }
    command = ["node", str(Path(__file__).with_name("browser.cjs"))]
    document = {
        "action": action,
        "work": str(work),
        "cdp": run.runtime.get("cdp"),
        "browserContextId": run.runtime.get("browser_context_id"),
        "settingsManaged": bool(run.runtime.get('session_watch_pid')),
        "settings": run.runtime.get("settings", run.persona.config),
        **payload,
    }
    return command, document, env, work


def browser_command(action, run, **payload):
    command, document, env, work = browser_invocation(action, run, **payload)
    result = subprocess.run(
        command,
        input=json.dumps(document),
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=90,
    )
    diagnostic = result.stderr
    if payload.get('apiKey'):
        diagnostic = diagnostic.replace(payload['apiKey'], '[redacted]')
    (work / f"{action}.stderr.log").write_text(diagnostic)
    if result.returncode:
        # Do not echo subprocess input, CDP bearer URLs, or cookie values.
        error = diagnostic.strip().splitlines()[-1] if diagnostic.strip() else "no diagnostic"
        for key, value in os.environ.items():
            if key.endswith(("_API_KEY", "_TOKEN")) and value:
                error = error.replace(value, "[redacted]")
        error = re.sub(r"wss?://\S+", "[browser endpoint]", error)
        raise RuntimeError(error[:400])
    return json.loads(result.stdout)


@contextmanager
def watch_browser(run, target, state, *, capture=True):
    command, document, env, work = browser_invocation("watch", run, target=target["targetId"], state=state, capture=capture)
    token = target["targetId"]
    ready, stop = work / f"watch-{token}.ready", work / f"watch-{token}.stop"
    ready.unlink(missing_ok=True)
    stop.unlink(missing_ok=True)
    (work / "active-target").write_text(token)
    with (work / f"watch-{token}.log").open("w") as log:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=log, text=True, env=env)
        process.stdin.write(json.dumps(document))
        process.stdin.close()
        try:
            deadline = time.monotonic() + 15
            while not ready.exists():
                if process.poll() is not None:
                    raise RuntimeError("The live browser viewer could not start; its diagnostic log was retained")
                if time.monotonic() >= deadline:
                    raise RuntimeError("The live browser viewer did not become ready")
                time.sleep(.1)
            yield
        finally:
            stop.touch()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                raise RuntimeError("The live browser viewer did not stop cleanly") from None
            if process.returncode:
                raise RuntimeError("The live browser viewer stopped with an error")


class CDPAdapter:
    """Settings shared by adapters that use this application's CDP driver."""

    capabilities: ClassVar[dict] = {
        "native": False, "cookies": True, "localStorage": True,
        "sessionStorage": True, "indexedDB": False, "opfs": False, "screencast": True,
    }
    network_fields: ClassVar[dict] = {}
    network_config_fields: ClassVar[set] = set()

    def task_health_config(self, config):
        """Compare browser configuration while network requirements are checked live."""
        return {key: value for key, value in config.items() if key not in self.network_config_fields}

    def location_options(self, location, config):
        """Routing is adapter-owned; plain CDP uses the browser's existing network."""
        return {}

    def validate_handoff(self, config):
        self.validate_config(config)

    def session_lifetime(self, config):
        return 1800

    def connection(self, run):
        return {'cdp_url': run.runtime['cdp'],
                'twocaptcha': run.runtime.get('twocaptcha_status', {'status': 'disabled'}),
                'browser_context_id': run.runtime.get('browser_context_id'),
                'targets': run.runtime.get('tabs', []), 'capabilities': self.capabilities}

    def native_path(self, run):
        return None

    def browser_invocation(self, action, run, **payload):
        return host_browser_invocation(action, run, **payload)

    def live_url(self, run):
        return None  # The shared CDP viewer supplies local interaction.

    def settings_support(self, config):
        support = {
            "viewport": {
                "supported": True,
                "detail": "The browser driver sets the viewport and pixel ratio for each check tab.",
                "default": {"width": 1440, "height": 1000, "deviceScaleFactor": 1},
            },
            "timezone": {"supported": True, "detail": "An override is set when a timezone is saved."},
            "locale": {"supported": True, "detail": "A browser locale override is set when saved."},
            "userAgent": {"supported": True, "detail": "A user agent override is set when saved."},
            "acceptLanguage": {
                "supported": True,
                "detail": "Applied with the user agent override.",
                **({"default": config.get("locale") or "en-US"} if config.get("userAgent") else {}),
            },
            "geolocation": {
                "supported": True,
                "detail": "Saved coordinates are set for the check tab; this does not grant site location permission.",
            },
            "colorScheme": {
                "supported": True,
                "detail": "The check tab uses this preferred color scheme.",
                "default": "light",
            },
            "reducedMotion": {
                "supported": True,
                "detail": "The check tab uses this motion preference.",
                "default": "no-preference",
            },
            "mobile": {
                "supported": True,
                "detail": "Applied through browser device emulation.",
                "default": False,
            },
            "platform": {"supported": True, "detail": "Applied with the user agent override."},
            **{key: {"supported": True, "detail": "Applied through the browser debugging protocol."}
               for key in ("userAgentMetadata", "screen", "window", "hardwareConcurrency", "maxTouchPoints", "contrast", "forcedColors", "reducedTransparency")},
            "randomSeed": {"supported": False, "detail": "A stable fingerprint seed is not applied."},
            "urls": {"supported": False, "detail": "Saved tabs are retained as history; checks open their own tabs."},
            "chromeArgs": {"supported": False, "detail": "This adapter does not accept custom Chrome launch arguments."},
            "requiredCapabilities": {
                "supported": True,
                "detail": "The worker refuses a check when the adapter cannot preserve a required capability.",
                "default": ["cookies", "localStorage", "sessionStorage"],
            },
            "recording": {"supported": False, "detail": "Checks retain screenshots, not a video recording."},
            "streamReplay": {"supported": False, "detail": "The live browser stream is not retained for replay."},
            "extension": {"supported": False, "detail": "An extension connection is not implemented."},
        }
        for key in ("cookies", "localStorage", "sessionStorage", "indexedDB", "opfs"):
            support[key] = {
                "supported": self.capabilities[key],
                "detail": (
                    "Preserved in the isolated native browser profile."
                    if key in {"indexedDB", "opfs"} and self.capabilities[key]
                    else "Portable state is restored and exported for visited origins."
                    if self.capabilities[key] and key != "cookies"
                    else "Cookies are restored and exported for the isolated browser."
                    if self.capabilities[key]
                    else "This adapter cannot round-trip this storage type."
                ),
            }
        for permission in ("geolocation", "notifications", "camera", "microphone", "clipboard-read", "clipboard-write"):
            support[f"permissions.{permission}"] = {
                "supported": False,
                "detail": "Saved site permission rules are not applied by this adapter.",
            }
        for field in ("scroll", "forms", "focus"):
            support[f"uiState.{field}"] = {
                "supported": False,
                "detail": "Saved page interaction state is not restored.",
            }
        return support


class Local(CDPAdapter):
    label = "Local Chrome"
    description = "An isolated Chrome profile on this computer or in Docker."
    config_help = "Docker: runtime=docker and image. Host: runtime=host, binary and headless. Browser credentials stay in the private profile."
    capabilities: ClassVar[dict] = {
        "native": True,
        "cookies": True,
        "localStorage": True,
        "sessionStorage": True,
        "indexedDB": True,
        "opfs": True,
        "screencast": True,
    }

    def validate_handoff(self, config):
        super().validate_handoff(config)
        if config.get('runtime') == 'docker':
            raise ValueError('Direct handoff requires the host runtime; this Docker image exposes CDP only inside its container')

    def session_lifetime(self, config):
        return 259200

    def settings_support(self, config):
        return {
            **super().settings_support(config),
            "chromeArgs": {
                "supported": True,
                "detail": "Additional arguments are passed to the isolated Chrome process at launch.",
                "default": [],
            },
        }

    def validate_config(self, config):
        unknown = set(config) - {"runtime", "image", "binary", "headless"}
        if unknown:
            raise ValueError("Unsupported local connection settings: " + ", ".join(sorted(unknown)))
        if config.get("runtime", "host") not in {"host", "docker"}:
            raise ValueError("Choose host or docker for the local runtime")
        if "headless" in config and not isinstance(config["headless"], bool):
            raise ValueError("headless must be true or false")
        for key in ("image", "binary"):
            if key in config and (not isinstance(config[key], str) or not config[key].strip()):
                raise ValueError(f"{key} must be a non-empty string")
        if config.get("runtime") == "docker" and any(key in config for key in ("binary", "headless")):
            raise ValueError("Docker uses the image's browser binary in headless mode; remove binary and headless")
        if config.get("runtime", "host") == "host" and "image" in config:
            raise ValueError("A Docker image requires runtime=docker")

    def browser_invocation(self, action, run, **payload):
        command, document, env, work = super().browser_invocation(action, run, **payload)
        if provider_config(run).get("runtime") == "docker":
            command = [
                "docker",
                "exec",
                "-i",
                "-e",
                "ACCOUNT_CHECKER_CHROME_HELPER=/venv/lib/python3.13/site-packages/abx_plugins/plugins/chrome/chrome_utils.js",
                "-e",
                "NODE_MODULES_DIR=/opt/archivebox/lib/pnpm/packages/chrome/node_modules",
                "-e",
                "CHROME_LOG_SUBPROCESS_OUTPUT=true",
                "-e",
                "PERSONAS_DIR=/run/personas",
                "-e",
                "ACTIVE_PERSONA=working",
                f"account-checker-run-{run.id}",
                "node",
                "/driver/browser.cjs",
            ]
            document["work"] = "/run"
            if document.get("screenshot"):
                document["screenshot"] = "/run/" + Path(document["screenshot"]).name
        return command, document, env, work

    def launch(self, run):
        self.validate_config(provider_config(run))
        work, profile = storage.fork_profile(run.base.digest, run.id,
            portable_only=run.runtime.get('provider_site_scope') is not None)
        preferences = profile / "Default/Preferences"
        if preferences.exists():
            config = json.loads(preferences.read_text())
            # abx-dl closes existing tabs after launch. Prevent restoring the
            # human's entire tab set before its readiness probe can run.
            config.setdefault("session", {})["restore_on_startup"] = 5
            preferences.write_text(json.dumps(config))
        # Chrome can restore the saved tab graph even when startup preferences
        # change (tracked preferences/crash recovery). Keep those files outside
        # the working profile. Session Storage itself is deliberately retained.
        saved_tabs = storage.private_dir(work / "imported-tab-sessions")
        for name in ["Sessions", "Last Session", "Last Tabs", "Current Session", "Current Tabs"]:
            path = profile / "Default" / name
            if path.exists():
                shutil.move(str(path), saved_tabs / name)
        from .twocaptcha import prepare_local
        prepare_local(run, work)
        if provider_config(run).get("runtime") == "docker":
            name = f"account-checker-run-{run.id}"
            image = provider_config(run).get("image", "archivebox/abx-dl:1.12.278")
            subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--shm-size=1g",
                    "--mount",
                    f"type=bind,src={work},dst=/run",
                    "--mount",
                    f"type=bind,src={Path(__file__).parent},dst=/driver,readonly",
                    "--mount",
                    f"type=bind,src={Path(__file__).resolve().parents[2] / 'node_modules'},dst=/account-checker/node_modules,readonly",
                    "--entrypoint",
                    "sleep",
                    image,
                    "infinity",
                ],
                check=True,
                capture_output=True,
            )
            try:
                result = browser_command(
                    "launch",
                    run,
                    binary="/opt/archivebox/lib/playwright/bin/chromium",
                    headless=True,
                )
                return {**result, "container": name, "image": image}
            except Exception:
                subprocess.run(["docker", "rm", "-f", name], check=True, capture_output=True)
                raise
        binary = provider_config(run).get("binary") or os.environ.get("CHROME_BINARY")
        if not binary:
            candidates = [
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path("/usr/bin/chromium"),
            ]
            binary = next((str(p) for p in candidates if p.exists()), None)
        if not binary:
            raise ValueError("Set the local provider's browser binary to the abx-dl Chrome binary")
        return browser_command(
            "launch", run, binary=binary, headless=provider_config(run).get("headless", True)
        )

    def stop(self, run):
        try:
            browser_command("close", run)
            if not run.runtime.get("container"):
                # Remote CDP Browser.close acknowledges the command before the
                # owned process has flushed its profile and exited.
                deadline = time.monotonic() + 15
                while True:
                    result = subprocess.run(
                        ["ps", "-p", str(int(run.runtime["pid"])), "-o", "stat="],
                        capture_output=True, text=True, check=False,
                    )
                    if result.returncode not in (0, 1):
                        raise RuntimeError("Could not confirm the owned Chrome process stopped")
                    status = result.stdout.strip()
                    if not status or status.startswith("Z"):
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Chrome has not exited; its profile is not ready to save")
                    time.sleep(.05)
        finally:
            if run.runtime.get("container"):
                subprocess.run(
                    ["docker", "rm", "-f", run.runtime["container"]],
                    check=True,
                    capture_output=True,
                )

    def native_path(self, run):
        return storage.data_root() / "runs" / str(run.id) / "personas/working/chrome_profile"


class Browserbase(CDPAdapter):
    network_config_fields: ClassVar[set] = {'residential_proxies', 'proxy_country', 'proxy_state', 'proxy_city'}
    network_fields: ClassVar[dict] = {'proxy_country': 'Country', 'proxy_state': 'State', 'proxy_city': 'City'}

    def location_options(self, location, config):
        country = location.get('country') or config.get('proxy_country')
        if not country or not any(location.get(field) for field in ('country', 'state', 'city')):
            return {}
        country = country.upper()
        state = (location.get('state') or '').upper().removeprefix('US-')
        # Browserbase supports subdivision routing only for US state codes.
        return {
            'residential_proxies': True,
            'proxy_country': country,
            'proxy_state': state if country == 'US' and re.fullmatch('[A-Z]{2}', state) else None,
            'proxy_city': location.get('city') or None,
        }

    label = "Browserbase"
    description = "An isolated cloud browser with portable site data and an interactive live view."
    config_help = "Set project_id, region, verified (true/false) and residential_proxies (true/false). Store BROWSERBASE_API_KEY in the ignored .env file. Verified and residential proxies default to enabled; unsupported plans fail explicitly. Native IndexedDB and OPFS transfer are not supported."
    capabilities: ClassVar[dict] = {
        "native": False,
        "cookies": True,
        "localStorage": True,
        "sessionStorage": True,
        "indexedDB": False,
        "opfs": False,
        "screencast": True,
    }

    def validate_config(self, config):
        unknown = set(config) - {"project_id", "region", "verified", "residential_proxies", *self.network_fields}
        if unknown:
            raise ValueError("Unsupported Browserbase connection settings: " + ", ".join(sorted(unknown)))
        if not isinstance(config.get("project_id"), str) or not config["project_id"].strip():
            raise ValueError("Browserbase project ID is required")
        if "region" in config and (not isinstance(config["region"], str) or not config["region"].strip()):
            raise ValueError("Browserbase region must be a non-empty string")
        for key in ("verified", "residential_proxies"):
            if key in config and type(config[key]) is not bool:
                raise ValueError(f"Browserbase {key} must be true or false")
        geo = {k: config[k] for k in self.network_fields if config.get(k)}
        if geo:
            if not config.get('residential_proxies', True) or not re.fullmatch(r'[A-Z]{2}', geo.get('proxy_country', '')):
                raise ValueError('Proxy location requires residential proxies and an uppercase country code')
            if geo.get('proxy_state') and (geo['proxy_country'] != 'US' or not re.fullmatch(r'[A-Z]{2}', geo['proxy_state'])):
                raise ValueError('Browserbase state requires country US and a two-letter state code')
            if geo.get('proxy_city') and (not isinstance(geo['proxy_city'], str) or len(geo['proxy_city']) > 100):
                raise ValueError('Proxy city must be a name of at most 100 characters')

    def api(self, method, path, **kwargs):
        key = os.environ.get("BROWSERBASE_API_KEY")
        if not key:
            raise ValueError("Set BROWSERBASE_API_KEY in .env or the process environment")
        response = httpx.request(
            method,
            "https://api.browserbase.com/v1" + path,
            headers={"x-bb-api-key": key},
            timeout=30,
            **kwargs,
        )
        if response.is_error:
            try:
                detail = str(response.json().get("message", response.json().get("error", "")))[:300]
            except ValueError:
                detail = ""
            public_message = f"Browserbase returned HTTP {response.status_code}"
            if response.status_code == 403 and detail == "Verified mode is only available on the Enterprise plan":
                public_message += ": " + detail
            raise ProviderAPIError(
                f"Browserbase returned HTTP {response.status_code}: " + detail.replace(key, "[redacted]"),
                public_message,
            )
        return response.json()

    def launch(self, run):
        self.validate_config(provider_config(run))
        project = provider_config(run).get("project_id")
        if not project:
            raise ValueError("Browserbase project ID is required")
        # A fresh, non-persistent cloud session for every fork. The collection
        # owns state; never retain an opaque shared cloud context between runs.
        viewport = run.runtime["settings"].get("viewport", {})
        verified = provider_config(run).get("verified", True)
        browser_settings = {
            "viewport": {key: viewport.get(key, default) for key, default in (("width", 1440), ("height", 1000))},
            "recordSession": False,
            "logSession": False,
            "verified": verified,
        }
        # Browserbase's macOS hosts require Verified (Enterprise). Standard
        # sessions retain the persona's CDP emulation on the default Linux host.
        if verified:
            browser_settings["os"] = "mac" if run.runtime["settings"].get("platform") == "MacIntel" else "linux"
        from .twocaptcha import enabled, uploaded_extension
        extensions = {}
        if enabled(run):
            def upload(package):
                with package['archive'].open('rb') as file:
                    return self.api('POST', '/extensions', files={'file': ('twocaptcha.zip', file, 'application/zip')})['id']
            extensions['extensionId'] = uploaded_extension('browserbase',
                f'{project}:{os.environ.get("BROWSERBASE_API_KEY", "")}', upload)
            browser_settings['solveCaptchas'] = False
        result = self.api(
            "POST",
            "/sessions",
            json={
                **extensions,
                "projectId": project,
                "browserSettings": browser_settings,
                "proxies": ([{'type': 'browserbase', 'geolocation': {k.removeprefix('proxy_'): provider_config(run)[k]
                    for k in self.network_fields if provider_config(run).get(k)}}]
                    if provider_config(run).get('proxy_country') else provider_config(run).get("residential_proxies", True)),
                "keepAlive": True,
                "timeout": 1800,
                "region": provider_config(run).get("region", "us-west-2"),
            },
        )
        return {
            "cdp": result["connectUrl"],
            "session_id": result["id"],
        }

    def live_url(self, run):
        return self.api("GET", f"/sessions/{run.runtime['session_id']}/debug")["debuggerFullscreenUrl"]

    def stop(self, run):
        self.api(
            "POST",
            f"/sessions/{run.runtime['session_id']}",
            json={"projectId": provider_config(run)["project_id"], "status": "REQUEST_RELEASE"},
        )
        deadline = time.monotonic() + 15
        while True:
            result = self.api("GET", f"/sessions/{run.runtime['session_id']}")
            if result["status"] == "COMPLETED":
                return
            if result["status"] in {"ERROR", "TIMED_OUT"}:
                raise RuntimeError("Browserbase session ended with " + result["status"])
            if time.monotonic() >= deadline:
                raise RuntimeError("Browserbase has not confirmed that the browser stopped")
            time.sleep(.25)

    def native_path(self, run):
        return None


from .provider_backends.anchor import Anchor
from .provider_backends.browserless import Browserless
from .provider_backends.cdp import GenericCDP
from .provider_backends.kernel import Kernel

ADAPTERS = {
    "local": Local, "browserbase": Browserbase, "cdp": GenericCDP,
    "kernel": Kernel, "anchor": Anchor, "browserless": Browserless,
}


def validate_provider_kind(kind):
    if kind not in ADAPTERS:
        raise ValidationError("Choose an installed browser provider adapter")


def provider_choices():
    return [(kind, implementation.label) for kind, implementation in ADAPTERS.items()]


def provider_options():
    return [
        {"kind": kind, "label": implementation.label, "description": implementation.description, "config_help": implementation.config_help}
        for kind, implementation in ADAPTERS.items()
    ]


def adapter(provider=None, kind=None):
    try:
        return ADAPTERS[kind or provider.kind]()
    except KeyError:
        raise ValueError("This browser provider has no installed adapter") from None
