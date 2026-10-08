"""Privileged adapters: all returned values stay in the local broker process.

No raw upstream MCP is registered with OpenCode. No send, reply, mailbox mutation,
attachment download, vault search, or arbitrary secret-reference tool is exposed.
"""

import asyncio
import email.policy
import imaplib
import json
import os
import re
import shutil
import ssl
import subprocess
from datetime import UTC, datetime
from email.parser import BytesParser
from email.utils import getaddresses
from urllib.parse import urlsplit

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import Pending, Unavailable, local_test_password, origin


def private_command(command, *, stdin=None):
    # Never forward stderr: providers can include a credential, message or URL in it.
    result = subprocess.run(
        command, input=stdin, capture_output=True, text=True, timeout=30, check=False
    )
    if result.returncode:
        raise Unavailable("Provider is unavailable or needs authentication")
    return result.stdout


def onepassword(cfg, field):
    reference = field.get("reference", "")
    parsed = urlsplit(reference)
    vault = cfg.get("vault", "")
    if parsed.scheme != "op" or not vault or parsed.netloc != vault:
        raise Unavailable("Credential must reference the configured 1Password vault")
    command = ["op", "read", reference, "--no-newline"]
    if cfg.get("account"):
        command += ["--account", cfg["account"]]
    # OTP references use ?attribute=otp; the seed must never be used as a code.
    value = private_command(command)
    if not value or value.startswith("otpauth://"):
        raise Unavailable("The 1Password field is empty or contains an OTP seed instead of a code")
    return value, ""


def imap_connection(cfg):
    host = cfg.get("host", "")
    loopback_test = cfg.get("local_test") is True and host in {"127.0.0.1", "::1"}
    if not host or not cfg.get("username"):
        raise Unavailable("Configure the IMAP mailbox first")
    if cfg.get("local_test") and not loopback_test:
        raise Unavailable("The test mailbox must be on loopback")
    if loopback_test:
        password = local_test_password()
        client = imaplib.IMAP4(host, int(cfg.get("port", 54143)), timeout=10)
    else:
        password, _ = onepassword(
            cfg.get("onepassword", {}), {"reference": cfg.get("password_ref", "")}
        )
        client = imaplib.IMAP4_SSL(
            host, int(cfg.get("port", 993)), ssl_context=ssl.create_default_context(), timeout=10
        )
    try:
        client.login(cfg["username"], password)
        status, _ = client.select(cfg.get("mailbox", "INBOX"), readonly=True)
        if status != "OK":
            raise Unavailable("Cannot open the configured mailbox read-only")
        return client
    except Exception:
        client.logout()
        raise


def imap_messages(cfg, field, since):
    senders = {s.lower() for s in field.get("senders", [])}
    recipient = field.get("recipient", "").lower()
    subject = field.get("subject", "")
    if not senders or not recipient or not subject:
        raise Unavailable("Set the expected sender, recipient, and subject for this site")
    client = imap_connection(cfg)
    try:
        status, values = client.uid("search", None, "SINCE", since.strftime("%d-%b-%Y"))
        if status != "OK":
            raise Unavailable("Mailbox search failed")
        validity = client.response("UIDVALIDITY")[1][0]
        messages = []
        for uid in values[0].split()[-100:]:
            status, chunks = client.uid("fetch", uid, "(INTERNALDATE RFC822.SIZE BODY.PEEK[])")
            if status != "OK":
                continue
            for chunk in chunks:
                if not isinstance(chunk, tuple):
                    continue
                match = re.search(rb'INTERNALDATE "([^"]+)"', chunk[0])
                if not match or len(chunk[1]) > 1_000_000:
                    continue
                received = datetime.strptime(match[1].decode(), "%d-%b-%Y %H:%M:%S %z")
                if received < since or received > datetime.now(UTC):
                    continue
                msg = BytesParser(policy=email.policy.default).parsebytes(chunk[1])
                sender = {a.lower() for _, a in getaddresses(msg.get_all("From", []))}
                recipients = {
                    a.lower()
                    for _, a in getaddresses(
                        msg.get_all("To", []) + msg.get_all("Delivered-To", [])
                    )
                }
                if len(sender) != 1 or not sender <= senders or recipient not in recipients:
                    continue
                if subject.casefold() not in str(msg.get("Subject", "")).casefold():
                    continue
                if not cfg.get("local_test"):
                    # The receiving mailserver must strip forged headers using its own authserv-id.
                    authserv = cfg.get("authserv_id", "")
                    headers = [
                        str(h)
                        for h in msg.get_all("Authentication-Results", [])
                        if str(h).split(";", 1)[0].strip() == authserv
                    ]
                    domain = next(iter(sender)).split("@")[-1]
                    authenticated = any(
                        re.search(
                            r"dkim=pass\b[^;]*\bheader\.d=" + re.escape(domain) + r"(?:\s|;|$)", h
                        )
                        for h in headers
                    )
                    if not authserv or not authenticated:
                        continue
                parts = [
                    p
                    for p in msg.walk()
                    if p.get_content_type() == "text/plain"
                    and p.get_content_disposition() != "attachment"
                ]
                if not parts:
                    parts = [
                        p
                        for p in msg.walk()
                        if p.get_content_type() == "text/html"
                        and p.get_content_disposition() != "attachment"
                    ]
                body = "\n".join(p.get_content() for p in parts)
                messages.append(
                    (
                        body,
                        f"imap:{cfg['host']}:{cfg['username']}:{cfg.get('mailbox', 'INBOX')}:{validity!r}:{uid!r}",
                    )
                )
        return messages
    finally:
        client.logout()


def imessage_messages(cfg, field, since):
    chat = field.get("chat_id")
    senders = field.get("senders", [])
    if not chat or not senders:
        raise Unavailable("Choose the verification sender and Messages conversation first")
    output = private_command(
        [
            cfg.get("binary", "imsg"),
            "history",
            "--chat-id",
            str(int(chat)),
            "--participants",
            ",".join(senders),
            "--start",
            since.isoformat(),
            "--limit",
            "30",
            "--json",
        ]
    )
    rows = [json.loads(line) for line in output.splitlines() if line.strip()]
    return [
        (r["text"], "imessage:" + str(r["guid"]))
        for r in rows
        if r.get("sender") in senders
        and not r.get("is_from_me")
        and datetime.fromisoformat(r["created_at"]) >= since
    ]


async def voice_call(cfg, method, arguments):
    if method not in {"gv_check_login", "gv_list_sms", "gv_read_sms"}:
        raise Unavailable("Google Voice tool is not read-only")
    command = cfg.get("command", [])
    if not command:
        raise Unavailable("Install and configure the Google Voice MCP first")
    # Suppress upstream diagnostics locally, not just in the MCP response.
    with open(os.devnull, "w") as errors:  # noqa: ASYNC230 - opening the null device cannot block on IO
        async with asyncio.timeout(40):
            async with (
                stdio_client(
                    StdioServerParameters(
                        command=command[0], args=command[1:], env=cfg.get("env", {})
                    ),
                    errlog=errors,
                ) as (reader, writer),
                ClientSession(reader, writer) as session,
            ):
                await session.initialize()
                result = await session.call_tool(method, arguments)
                if result.isError:
                    raise Unavailable("Google Voice needs a signed-in browser")
                return "\n".join(c.text for c in result.content if c.type == "text")


def googlevoice_messages(cfg, field, since):
    conversation = field.get("conversation_id")
    senders = field.get("senders", [])
    if not conversation or not senders:
        raise Unavailable("Choose the verification sender and Google Voice conversation first")
    rows = json.loads(
        asyncio.run(voice_call(cfg, "gv_read_sms", {"conversation_id": conversation, "limit": 30}))
    )
    messages = []
    for row in rows:
        # Human/relative timestamps are insufficient evidence of freshness.
        if row.get("sender") not in senders or row.get("isInbound") is not True:
            continue
        try:
            when = datetime.fromisoformat(row["timestamp"])
            if when.tzinfo is None or when < since or when > datetime.now(UTC):
                continue
        except ValueError, KeyError:
            raise Unavailable(
                "Google Voice did not supply a trustworthy message timestamp"
            ) from None
        messages.append(
            (row["text"], f"googlevoice:{conversation}:{row['timestamp']}:{row['sender']}")
        )
    return messages


def extract(messages, field):
    if field.get("kind") == "link":
        pattern = re.compile(r'https://[^\s<>"\']+')
    else:
        pattern = re.compile(field.get("pattern", r"(?<!\d)(\d{6})(?!\d)"))
        if pattern.groups != 1:
            raise Unavailable("The verification-code pattern needs exactly one capture group")
    matches = []
    for body, identity in messages:
        for match in pattern.finditer(body):
            value = match[0] if field.get("kind") == "link" else match[1]
            if field.get("kind") == "link" and origin(value) not in field.get("origins", []):
                continue
            if (value, identity) not in matches:
                matches.append((value, identity))
    if not matches:
        raise Pending("No fresh matching verification message yet")
    if len(matches) != 1:
        raise Unavailable("More than one verification code or link matches; human review is needed")
    return matches[0]


def resolve(config, field, since):
    source = field.get("source")
    if source == 'browser_message':
        return field['value'], field['message_id']
    if source == 'signup':
        from ..onboarding import resolve as resolve_signup
        return resolve_signup(field)
    if source == 'cloaked':
        from ..cloaked import messages
        return extract(messages(config.get('cloaked', {}), field, since), field)
    cfg = config.get(source, {})
    if source == "onepassword":
        return onepassword(cfg, field)
    readers = {
        "imap": imap_messages,
        "imessage": imessage_messages,
        "googlevoice": googlevoice_messages,
    }
    if source not in readers:
        raise Unavailable("Unknown credential source")
    if source == "imap":
        cfg = {**cfg, "onepassword": config.get("onepassword", {})}
    return extract(readers[source](cfg, field, since), field)


def status(config, name):
    if name == 'twocaptcha':
        from ..twocaptcha import connection_status
        return connection_status(config)
    cfg = config.get(name, {})
    try:
        if name == 'cloaked':
            from ..cloaked import call
            call(config, 'status')
        elif name == "onepassword":
            account_args = ["--account", cfg["account"]] if cfg.get("account") else []
            if not cfg.get("vault"):
                return "Choose a 1Password vault"
            # App-integrated reads work even when `op whoami` reports no CLI session.
            private_command(["op", "vault", "get", cfg["vault"], "--format=json"] + account_args)
        elif name == "imap":
            imap_connection({**cfg, "onepassword": config.get("onepassword", {})}).logout()
        elif name == "imessage":
            binary = cfg.get("binary", "imsg")
            if not shutil.which(binary):
                return "Install imsg first"
            private_command([binary, "chats", "--limit", "1", "--json"])
        elif name == "googlevoice":
            # Do not let the upstream MCP launch an unconfigured browser as a status probe.
            if not cfg.get("enabled"):
                return "Connect an isolated signed-in Google Voice browser"
            result = asyncio.run(voice_call(cfg, "gv_check_login", {}))
            if not result.startswith("Logged in to Google Voice."):
                return "Sign in to Google Voice"
        else:
            return "Unknown connector"
        return "Connected"
    except Exception:  # noqa: BLE001 - provider errors may contain private data
        return {
            'cloaked': 'Cloaked browser unavailable; connect a dedicated browser signed into my.cloaked.com',
            "onepassword": "Unlock/sign in to 1Password CLI",
            "imessage": "Messages access unavailable; check Full Disk Access",
            "googlevoice": "Google Voice browser unavailable",
            "imap": "Mailbox unavailable; check its connection and credentials",
        }.get(name, "Unavailable")
