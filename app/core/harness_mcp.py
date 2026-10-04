"""Give the check agent the real browser-harness, bound to one checkout.

No site conditions, selectors, navigation recipes, or success decisions live here.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.core.recovery import redaction

args = argparse.ArgumentParser()
args.add_argument("--run-dir", required=True)
WORK = Path(args.parse_args().run_dir).resolve()
SERVER = FastMCP("browser-harness")


def redact(text):
    text = redaction.redact(text, redaction.read(WORK))
    return re.sub(
        r"([?&](?:auth|token|key|code)=)[^\s\"'<>]+", r"\1[redacted]", text, flags=re.IGNORECASE
    )


@SERVER.tool()
def agent_helpers(code: str | None = None) -> str:
    """Read or replace this checkout's agent_helpers.py without importing it.

    Use this to develop reusable browser-harness helpers, including repairing
    imports that prevent execute_python from starting. Omit code to read.
    Never store credentials, tokens or browser endpoints in source.
    """
    context = json.loads((WORK / 'harness-context.json').read_text())
    if not context.get('active'):
        raise ValueError('This checkout has ended')
    path = WORK / 'agent-workspace' / 'agent_helpers.py'
    if code is not None:
        if len(code) > 30000:
            raise ValueError('Keep reusable helpers under 30000 characters')
        compile(code, 'agent_helpers.py', 'exec')
        path.parent.mkdir(mode=0o700, exist_ok=True)
        path.write_text(code)
    return redact(path.read_text()) if path.is_file() else ''


@SERVER.tool()
def execute_python(code: str, purpose: str) -> str:
    """Run Python using browser-harness's pre-imported helpers in this checkout.

    Inspect live browser state, then write the smallest useful program. Helpers
    include page_info, list_tabs, switch_tab, goto_url, js, capture_screenshot,
    wait_for_load, click_at_xy, fill_input, cdp. No Playwright/Puppeteer. Print
    concise evidence. Never print credentials, cookies, input values, or auth URLs.
    Browser findings are evidence for the outer agent, never fabricated results.
    Each call is a fresh Python process; variables do not persist between calls.
    """
    if len(code) > 30000:
        raise ValueError("Split the browser task into smaller programs")
    context = json.loads((WORK / "harness-context.json").read_text())
    if not context.get("active"):
        raise ValueError("This checkout has ended")
    step = uuid.uuid4().hex
    artifacts = WORK / "agent-workspace"
    artifacts.mkdir(mode=0o700, exist_ok=True)
    (artifacts / f"{step}.py").write_text(code)
    helper = artifacts / 'agent_helpers.py'
    helper_source = helper.read_text() if helper.is_file() else ''
    if helper_source:
        (artifacts / f'{step}.helpers.py').write_text(helper_source)
    helper_digest = hashlib.sha256(helper_source.encode()).hexdigest() if helper_source else ''
    prelude = (
        redaction.screenshot_prelude(redaction.read(WORK))
        if (WORK / "recovery-context.json").exists()
        else ""
    )
    environment = {
        **os.environ,
        "BU_CDP_WS": context["cdp"],
        "BH_TAB_MARKER": "0",
        "BH_RECORD": "0",
        "BH_TELEMETRY": "0",
        "BU_AUTOSPAWN": "",
    }
    if context.get("container"):
        command = [
            "docker",
            "exec",
            "-i",
            "-e",
            "BU_CDP_WS",
            "-e",
            "BH_TAB_MARKER=0",
            "-e",
            "BH_RECORD=0",
            "-e",
            "BH_TELEMETRY=0",
            "-e",
            "BU_AUTOSPAWN=",
            "-e",
            "BH_HOME=/run/harness",
            "-e",
            "BH_AGENT_WORKSPACE=/run/agent-workspace",
            "-w",
            "/run/agent-workspace",
            context["container"],
            "browser-harness",
        ]
    else:
        # Local host mode still uses the exact provisioned endpoint, never discovers the everyday browser.
        environment.update(
            BH_HOME=str(WORK / "harness"), BH_AGENT_WORKSPACE=str(artifacts), BU_NAME="default"
        )
        command = [
            "uv",
            "run",
            "--project",
            str(Path(__file__).resolve().parents[2]),
            "browser-harness",
        ]
    try:
        result = subprocess.run(
            command,
            input=prelude + code,
            text=True,
            capture_output=True,
            env=environment,
            cwd=artifacts,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired:
        output = {
            "step": step,
            "helper_digest": helper_digest,
            "purpose": purpose,
            "exit_code": 124,
            "stdout": "",
            "stderr": "Browser program exceeded its 90 second limit",
        }
        (artifacts / f"{step}.json").write_text(json.dumps(output))
        return json.dumps(output)
    output = {
        "step": step,
        "helper_digest": helper_digest,
        "check_id": context["check_id"],
        "purpose": purpose,
        "exit_code": result.returncode,
        "stdout": redact(result.stdout[-16000:]),
        "stderr": redact(result.stderr[-4000:]),
    }
    # Every executed browser step leaves visual evidence, including failed steps.
    # This captures the current page without supplying any site-specific behavior.
    screenshot = f"step-{step}.png"
    screenshot_path = (
        str(Path("/run/agent-workspace") / screenshot)
        if context.get("container")
        else str(artifacts / screenshot)
    )
    try:
        captured = subprocess.run(
            command,
            input=prelude + f"capture_screenshot({screenshot_path!r})",
            text=True,
            capture_output=True,
            env=environment,
            cwd=artifacts,
            timeout=15,
            check=False,
        )
        if captured.returncode or not (artifacts / screenshot).is_file():
            output["screenshot_error"] = (
                redact(captured.stderr[-1000:]) or "No browser image was saved"
            )
        else:
            output["screenshot"] = screenshot
    except subprocess.TimeoutExpired:
        output["screenshot_error"] = "Browser screenshot did not finish within 15 seconds"
    (artifacts / f"{step}.json").write_text(json.dumps(output))
    return json.dumps(output)


@SERVER.tool()
def view_screenshot(filename: str) -> Image:
    """View a captured PNG. Accept its basename or the supplied screenshot_path."""
    workspace = WORK / "agent-workspace"
    supplied = Path(filename)
    if supplied.parent not in (Path("."), Path("/run/agent-workspace"), workspace):
        raise ValueError("Use a PNG inside this run's agent workspace")
    path = workspace / supplied.name
    if path.suffix != ".png" or path.is_symlink():
        raise ValueError("Use a PNG inside this run's agent workspace")
    if not path.is_file():
        raise ValueError("Capture the screenshot with browser-harness first")
    return Image(path=str(path))


if __name__ == "__main__":
    SERVER.run(transport="stdio")
