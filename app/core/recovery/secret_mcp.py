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
from app.core.recovery.config import Pending, Unavailable, audit, load


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
    if action == 'capture':
        return json.loads(lines[-1])
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
        except Pending as exc:
            audit(directory, action, 'pending')
            return {'status': 'pending', 'reason': str(exc)}
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

    if context['binding'].get('signup'):
        signup = context['binding']['signup']

        @mcp.tool()
        def select_signup_option(selector: str, placeholder: str) -> dict:
            """Select a private autofill fact in an ALREADY OPEN custom dropdown. Supply an observed CSS selector for its role=option elements. The broker matches the value and clicks locally, without exposing it to inference. Use for birth month/country; never passwords or codes."""
            def select():
                from app.core.onboarding import AUTOFILL_PURPOSES
                if broker.purpose(placeholder) not in AUTOFILL_PURPOSES or len(selector) > 500:
                    raise Unavailable('Choose a basic autofill fact and an observed option selector')
                destination = stagehand(context, directory, 'origin').get('origin')
                if not destination:
                    raise Unavailable('Browser is outside approved signup origins')
                value = broker.consume(placeholder, destination)
                redaction.remember(directory, {'fact:' + placeholder:value})
                return stagehand(context, directory, 'select', selector=selector, value=value)
            return safe('select_signup_option', select)

        @mcp.tool()
        def use_signup_tab(target_id: str) -> dict:
            """Select an observed signup/OAuth popup in this persona browser for subsequent autofill; only approved origins are accepted."""
            def select():
                candidate = {**context, 'target_id': target_id}
                if not stagehand(candidate, directory, 'origin').get('origin'):
                    raise Unavailable('The selected tab is outside approved signup origins')
                context['target_id'] = target_id
                return {'status': 'selected'}
            return safe('use_signup_tab', select)

        @mcp.tool()
        def prepare_signup() -> dict:
            """Provision the requested contacts once and list available autofill facts. No values are returned."""
            def prepare():
                from app.core.models import PersonaSetup
                from app.core.onboarding import prepare, read
                result = prepare(signup['setup_id'], signup['site'])
                data = read(PersonaSetup.query.get(id=signup['setup_id']))
                values = {**data['facts'], **{k:v for k,v in data['accounts'][signup['site']].items() if isinstance(v,str)},
                          **{k:v for k,v in data.get('cloaked', {}).items() if isinstance(v,str)}}
                redaction.remember(directory, values)
                return result
            return safe('prepare_signup', prepare)

        @mcp.tool()
        def capture_security(purpose: str, selector: str) -> dict:
            """Store the manual TOTP key or recovery-code list locally, selecting one observed DOM element. purpose is totp or recovery_codes. Never returns its value."""
            def capture():
                from urllib.parse import urlsplit

                from app.core.onboarding import SITES, save_security
                if purpose not in {'totp', 'recovery_codes'} or len(selector) > 500:
                    raise Unavailable('Choose an authenticator key or recovery-code element')
                host = urlsplit(stagehand(context, directory, 'origin').get('origin', '')).hostname or ''
                domain = SITES[signup['site']]['domain']
                if host != domain and not host.endswith('.' + domain):
                    raise Unavailable('Capture security details only on the account being set up')
                result = stagehand(context, directory, 'capture', selector=selector)
                if result.get('status') != 'captured':
                    raise Unavailable('Could not read the selected security field')
                value = result['private_value']
                save_security(signup['setup_id'], signup['site'], purpose, value)
                redaction.remember(directory, {purpose: value})
                return {'status': 'saved', 'purpose': purpose}
            return safe('capture_security', capture)

        @mcp.tool()
        def save_account(authentication: str = 'password') -> dict:
            """Save credentials/MFA to encrypted storage and 1Password. Specify the observed authentication: password, google or facebook. SSO logins never get an unused password saved to the vault."""
            from app.core.onboarding import save_account as save
            return safe('save_account', lambda: save(signup['setup_id'], signup['site'], authentication))

        @mcp.tool()
        def identity_provider_placeholder(provider: str, purpose: str) -> dict:
            """Get username/password/otp for a previously verified Google or Facebook account when its OAuth sign-in asks. Credentials are restricted to that identity provider's origin."""
            def request():
                from app.core.models import Check, PersonaSetup
                from app.core.onboarding import binding
                setup = PersonaSetup.query.get(id=signup['setup_id'])
                site = {'google': 'gmail', 'facebook': 'facebook'}.get(provider)
                if site not in setup.completed or purpose not in {'username', 'password', 'otp'}:
                    raise Unavailable('Choose a verified identity provider and credential purpose')
                task = Check.query.filter(account__persona=setup.persona, pattern='signup:' + site).first()
                field = binding(task).get('fields', {}).get(purpose) if task else None
                if not field:
                    raise Unavailable('Identity provider credential is not configured')
                origins = ['https://accounts.google.com'] if provider == 'google' else ['https://www.facebook.com', 'https://facebook.com']
                return broker.request(purpose, field={**field, 'origins': origins})
            return safe('identity_provider_placeholder', request)

        @mcp.tool()
        def request_browser_email(target_id: str, selector: str, purpose: str, subject: str) -> dict:
            """Read one Gmail Show original message from the persona's signed-in browser. Select its raw RFC822 source element. Validate sender/DKIM/recipient/freshness locally and return a single-use email_code or email_link placeholder; never the message."""
            def retrieve():
                from app.core.onboarding import browser_email
                mail_context = {**context, 'target_id': target_id,
                    'binding': {**context['binding'], 'origins': ['https://mail.google.com']}}
                result = stagehand(mail_context, directory, 'capture', selector=selector)
                if result.get('status') != 'captured':
                    raise Unavailable('Open the verification email’s Show original view in Gmail')
                value, identity = browser_email(result['private_value'], signup['setup_id'], signup['site'], broker.since, purpose, subject)
                broker.binding['fields'][purpose] = {'source': 'browser_message', 'value': value, 'message_id': identity}
                redaction.remember(directory, {identity: value})
                return broker.request(purpose)
            return safe('request_browser_email', retrieve)

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
            remembered = {}
            for name, placeholder in placeholders.items():
                purpose = broker.purpose(placeholder)
                if purpose == "email_link":
                    raise Unavailable("Use follow_verification_link for an email link")
                values[name] = broker.consume(placeholder, destination)
                key = 'fact:' + placeholder if purpose.startswith('dob_') else placeholder
                remembered[key] = values[name]
            # Encrypted local state lets later HTML/screenshot observations mask reflected values.
            redaction.remember(directory, remembered)
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
