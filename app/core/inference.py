"""Plain-English access checks using OpenCode and the real browser-harness."""

import inspect
import json
import os
import re
import secrets
import subprocess
import time
from importlib.resources import files
from pathlib import Path

import httpx
from jsonschema import validate
from plain.runtime import settings

from .models import AppConfig
from .storage import data_root, private_dir

STATES = [
    "accessible",
    "login_required",
    "captcha_required",
    "email_code_required",
    "sms_code_required",
    "otp_required",
    "cookie_consent",
    "promo_blocked",
    "rate_limited",
    "human_required",
]
ORIGIN = "http://127.0.0.1:4098"


def config():
    row = AppConfig.query.filter(key="inference").first()
    return row.value if row else {"model": "openai/gpt-6.1-sol", "enabled": True}


def password():
    path = Path(settings.APP_CONFIG_DIR) / "opencode-password"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32))
    return path.read_text()


def client(directory=None):
    return httpx.Client(
        base_url=ORIGIN,
        auth=("opencode", password()),
        timeout=300,
        params={"directory": str(directory)} if directory else None,
    )


def access_agent():
    from browser_harness import helpers

    skill = files("browser_harness").joinpath("SKILL.md").read_text()
    # Publish signatures from the installed harness, so agents do not guess
    # action arguments from helper names or rely on a different package version.
    reference = "\n".join(
        f"browser_harness.helpers.{name}{inspect.signature(getattr(helpers, name))}: {inspect.getdoc(getattr(helpers, name)) or ''}"
        for name in (
            "page_info", "list_tabs", "switch_tab", "goto_url", "wait_for_load",
            "js", "capture_screenshot", "click_at_xy", "fill_input", "cdp",
        )
    )
    prompts = Path(__file__).with_name("prompts")
    prompt = prompts.joinpath("access.txt").read_text() + "\n" + prompts.joinpath("learning.txt").read_text()
    return {
        "mode": "primary",
        "description": "Inspect user access using browser-harness",
        "prompt": prompt + "\nBrowser-harness skill (task scope above takes precedence):\n" + skill
            + "\nInstalled helper API:\n" + reference,
        "steps": 18,
        "permission": {"*": "deny", "browser_harness_*": "allow"},
    }


def ensure_server():
    with client() as api:
        try:
            response = api.get("/global/health", timeout=2)
            response.raise_for_status()
            return
        except httpx.ConnectError:
            pass
    root = private_dir(data_root() / "opencode")
    work = private_dir(root / "work")
    (work / ".ignore").write_text("*\n")
    from .recovery.runner import agent as recovery_agent
    from .recovery.runner import signup_agent

    cfg = {
        "$schema": "https://opencode.ai/config.json",
        "snapshot": False,
        "enabled_providers": ["openai"],
        "model": config()["model"],
        "small_model": config()["model"],
        "default_agent": "access",
        "permission": {"*": "deny"},
        "agent": {"access": access_agent(), "recovery": recovery_agent(), 'signup': signup_agent()},
    }
    env = {
        **os.environ,
        "OPENCODE_CONFIG_CONTENT": json.dumps(cfg),
        "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
        "OPENCODE_DISABLE_FFF": "true",
        "OPENCODE_DISABLE_EXTERNAL_SKILLS": "true",
        "OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER": "true",
        "GIT_DIR": os.devnull,
        "OPENCODE_SERVER_PASSWORD": password(),
        "BROWSER": "false",
    }
    for key, folder in [
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"),
        ("XDG_CACHE_HOME", "cache"),
    ]:
        env[key] = str(private_dir(root / folder))
    with (root / "server.log").open("ab") as log:
        process = subprocess.Popen(
            ["opencode", "serve", "--pure", "--hostname", "127.0.0.1", "--port", "4098"],
            cwd=work,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    (root / "server.pid").write_text(str(process.pid))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("OpenCode server failed to start; inspect its private server log")
        with client() as api:
            try:
                if api.get("/global/health", timeout=1).status_code == 200:
                    return
            except httpx.ConnectError, httpx.ReadTimeout:
                pass
        time.sleep(0.25)
    raise RuntimeError("OpenCode server startup timed out")


RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "state": {"type": "string", "enum": STATES},
        "reason": {"type": "string"},
        "identity": {"type": "string"},
        "url": {"type": "string"},
        "checks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "assertion": {"type": "string"},
                    "passed": {"type": "boolean"},
                    "evidence": {"type": "string"},
                },
                "required": ["assertion", "passed", "evidence"],
                "additionalProperties": False,
            },
        },
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["passed", "state", "reason", "identity", "url", "checks", "issues"],
    "additionalProperties": False,
}


def check_access(run, plan, target):
    """Persist the actual programs and transcript alongside the resulting verdict.

    The application supplies no site selectors or browser check implementation.
    Each session discovers the page and writes its own browser-harness programs.
    """
    if not config().get("enabled", True):
        raise ValueError("Inference is disabled")
    model = run.runtime.get("inference", config())["model"]
    if not model.startswith("openai/"):
        raise ValueError("Only OpenAI models are configured for this app")
    ensure_server()
    work = private_dir(data_root() / "runs" / str(run.id))
    directory = private_dir(work / "agent")
    artifacts = private_dir(work / "agent-workspace")
    from .services import restore_agent_helpers

    restore_agent_helpers(plan['id'], artifacts)
    (directory / ".ignore").write_text("*\n")
    context = {
        "active": True,
        "cdp": run.runtime["cdp"],
        "container": run.runtime.get("container"),
        "check_id": plan["id"],
    }
    (work / "harness-context.json").write_text(json.dumps(context))
    screenshot = f"check-{plan['id']}.png"
    screenshot_path = (
        "/run/agent-workspace/" if context["container"] else str(artifacts) + "/"
    ) + screenshot
    previous = set(artifacts.glob("*.json"))
    with client(directory) as api:
        response = api.post(
            "/mcp",
            json={
                "name": "browser_harness",
                "config": {
                    "type": "local",
                    "command": [
                        "uv",
                        "run",
                        "--project",
                        str(Path(__file__).resolve().parents[2]),
                        "python",
                        str(Path(__file__).with_name("harness_mcp.py")),
                        "--run-dir",
                        str(work),
                    ],
                    "enabled": True,
                    "timeout": 100000,
                },
            },
        )
        response.raise_for_status()
        if response.json().get("browser_harness", {}).get("status") != "connected":
            raise RuntimeError("OpenCode could not connect to the browser-harness MCP")
        response = api.post("/session", json={"title": f"{run.persona.name} · {run.provider.name} · {plan['name']}"})
        response.raise_for_status()
        session_id = response.json()["id"]
        session_metadata = {"session_id": session_id, "directory": str(directory), "model": model}
        (work / f"agent-{plan['id']}.json").write_text(json.dumps(session_metadata))
        started = time.monotonic()
        try:
            response = api.post(
                f"/session/{session_id}/message",
                json={
                    "agent": "access",
                    "model": {"providerID": "openai", "modelID": model.split("/", 1)[1]},
                    "parts": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "check": plan,
                                    "task_id": plan["id"],
                                    "target_id": target["targetId"],
                                    "start_url": plan["url"],
                                    "screenshot_path": screenshot_path,
                                    "task": "Perform this read-only access check now in the real browser.",
                                    "response_format": "Finish with ONLY a JSON object matching this schema. Do not call no-op tools to finish.",
                                    "response_schema": RESULT_SCHEMA,
                                }
                            ),
                        }
                    ],
                },
            )
            response.raise_for_status()
        except Exception:
            api.post(f"/session/{session_id}/abort")
            raise
        finally:
            transcript = api.get(f"/session/{session_id}/message")
            transcript.raise_for_status()
            (work / f"transcript-{plan['id']}.json").write_text(transcript.text)
    payload = response.json()
    if payload.get("info", {}).get("error"):
        raise RuntimeError("OpenCode check failed; inspect the retained agent session")
    answer = "\n".join(
        p.get("text", "") for p in payload.get("parts", []) if p.get("type") == "text"
    )
    answer = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.strip())
    result = json.loads(answer)
    validate(result, RESULT_SCHEMA)
    if not isinstance(result, dict) or result.get("state") not in STATES:
        raise ValueError("OpenCode did not return a structured access verdict")
    programs = [
        json.loads(p.read_text())
        for p in sorted(set(artifacts.glob("*.json")) - previous, key=lambda p: p.stat().st_mtime)
    ]
    if not programs or not (artifacts / screenshot).is_file():
        raise ValueError("Agent verdict has no browser-harness execution or screenshot evidence")
    issues = result["issues"] + [
        f"Browser program {p['step']} failed" for p in programs if p["exit_code"]
    ]
    issues += [
        f"Browser step screenshot failed: {p['screenshot_error']}"
        for p in programs
        if p.get("screenshot_error")
    ]
    issues += [
        f"Agent tool {part['tool']} failed: {part['state'].get('error', '')[:250]}"
        for message in transcript.json()
        for part in message.get("parts", [])
        if part.get("type") == "tool" and part.get("state", {}).get("status") == "error"
    ]
    passed = (
        result["passed"] is True
        and result["state"] == "accessible"
        and all(c["passed"] is True for c in result["checks"])
    )
    evidence = {
        **result,
        "passed": passed,
        "title": plan["name"],
        "screenshot": screenshot,
        "programs": [{k: p[k] for k in ["step", "purpose", "exit_code"]} for p in programs],
        "duration_seconds": round(time.monotonic() - started, 1),
        "issues": issues,
    }
    # Use OpenCode's own UI; no duplicate transcript/session viewer in this app.
    classification = {
        "state": result["state"],
        "reason": result["reason"],
        **session_metadata,
        "url": f"/agents?run={run.id}&check={plan['id']}",
    }
    return evidence, classification
