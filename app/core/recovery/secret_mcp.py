"""Opaque credential placeholders and Stagehand actions for the OpenCode loop."""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
os.chdir(Path(__file__).resolve().parents[3])

from mcp.server.fastmcp import FastMCP
from plain.runtime import setup

setup()

from app.core.recovery import redaction
from app.core.recovery.broker import Broker
from app.core.recovery.config import Unavailable, audit, load


def stagehand(context, directory, action, **payload):
    project = Path(__file__).resolve().parents[3]
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "TMPDIR", "LANG"}}
    document = {
        "action": action,
        "cdp": context["cdp"],
        "target_id": context["target_id"],
        "model": context["model"],
        "check_id": context.get('check_id'),
        "origins": context["binding"]["origins"],
        "apiKey": os.environ.get("OPENAI_API_KEY"),
        "knownValues": redaction.read(directory),
        **payload,
    }
    if context.get("container"):
        command = [
            "docker",
            "exec",
            "-i",
            "-e",
            "ACCOUNT_CHECKER_STAGEHAND_MODULE=file:///account-checker/node_modules/@browserbasehq/stagehand/dist/esm/index.js",
            context["container"],
            "node",
            "/driver/recovery/stagehand.mjs",
        ]
    else:
        command = ["node", str(Path(__file__).with_name("stagehand.mjs"))]
    result = subprocess.run(
        command,
        input=json.dumps(document),
        capture_output=True,
        text=True,
        timeout=70,
        env=env,
        cwd=project,
        check=False,
    )
    # Read only the explicit result envelope, never forward package logs or stderr.
    lines = [
        line.removeprefix("ACCOUNT_CHECKER_RESULT=")
        for line in result.stdout.splitlines()
        if line.startswith("ACCOUNT_CHECKER_RESULT=")
    ]
    if result.returncode or not lines:
        raise Unavailable("Stagehand browser action is unavailable")
    return json.loads(redaction.redact(lines[-1], redaction.read(directory)))


def server(directory):
    context = json.loads((directory / "recovery-context.json").read_text())
    broker = Broker(
        load(), context["binding"], directory, since=datetime.fromisoformat(context["since"])
    )
    mcp = FastMCP("private-login")

    def safe(action, operation):
        try:
            current = json.loads((directory / "recovery-context.json").read_text())
            if not current.get("active") or current.get("nonce") != context.get("nonce"):
                raise Unavailable("This recovery session has ended")
            if time.time() > context["expires_at"]:
                raise Unavailable("Recovery session expired")
            result = operation()
            with (directory / "recovery-actions.jsonl").open("a") as log:
                log.write(json.dumps({"tool": action, "result": result}) + "\n")
            audit(directory, action, "ok")
            return result
        except Unavailable as exc:
            audit(directory, action, "blocked")
            return {"status": "blocked", "reason": str(exc)}
        except Exception:  # noqa: BLE001 - upstream diagnostics can contain secrets
            audit(directory, action, "unavailable")
            return {
                "status": "unavailable",
                "reason": "Local provider or login browser is unavailable",
            }

    @mcp.tool()
    def request_placeholder(purpose: str) -> dict:
        """Get a single-use secret placeholder for username/password/otp/email_code/sms_code/email_link. No raw value is returned."""
        return safe("request_placeholder", lambda: broker.request(purpose))

    @mcp.tool()
    def act(instruction: str, placeholders: dict[str, str]) -> dict:
        """Fill ONE credential field with ONE Stagehand v3 action. Use %variable% mapped to an opaque placeholder. Do not combine fields, navigation, or submission. Values resolve locally; inspect the result using browser-harness."""

        def execute():
            if len(instruction) > 3000 or len(placeholders) != 1:
                raise Unavailable("Provide one field action and exactly one credential placeholder")
            if any(
                not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,40}", k) or f"%{k}%" not in instruction
                for k in placeholders
            ):
                raise Unavailable("Use each variable name as %name% in the instruction")
            destination = stagehand(context, directory, "origin").get("origin")
            if not destination:
                raise Unavailable("Browser is outside approved login origins")
            values = {}
            for name, placeholder in placeholders.items():
                if broker.purpose(placeholder) == "email_link":
                    raise Unavailable("Use follow_verification_link for an email link")
                values[name] = broker.consume(placeholder, destination)
            # Encrypted local state lets later HTML/screenshot observations mask reflected values.
            redaction.remember(directory, {placeholders[k]: v for k, v in values.items()})
            return stagehand(context, directory, "act", instruction=instruction, variables=values)

        return safe("act", execute)

    @mcp.tool()
    def follow_verification_link(placeholder: str) -> dict:
        """Open a locally retrieved verification link. Never returns the raw URL."""

        def follow():
            if broker.purpose(placeholder) != "email_link":
                raise Unavailable("This placeholder is not a verification link")
            destination = stagehand(context, directory, "origin").get("origin")
            if not destination:
                raise Unavailable("Browser is outside approved login origins")
            value = broker.consume(placeholder, destination)
            redaction.remember(directory, {placeholder: value})
            return stagehand(context, directory, "link", url=value)

        return safe("follow_verification_link", follow)

    return mcp


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True, type=Path)
    server(parser.parse_args().directory).run(transport="stdio")
