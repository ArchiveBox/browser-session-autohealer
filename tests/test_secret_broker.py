"""Real Postfix/Dovecot delivery and the actual MCP contract; no provider stand-ins."""

import hmac
import json
import secrets
import smtplib
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import pytest

from app.core.recovery.broker import Broker
from app.core.recovery.config import Unavailable, load
from app.core.recovery.sources import imap_connection, resolve


def deliver(subject, body, sender="security@account-checker.test"):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "researcher@account-checker.test"
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP("127.0.0.1", 54025, timeout=10) as smtp:
        assert not smtp.send_message(msg)


def mailbox_case():
    subject = "Account checker verification " + secrets.token_hex(8)
    field = {
        "source": "imap",
        "recipient": "researcher@account-checker.test",
        "senders": ["security@account-checker.test"],
        "subject": subject,
    }
    since = datetime.now(UTC) - timedelta(seconds=2)
    return subject, field, since


def test_real_imap_delivery_and_no_seen_flag():
    subject, field, since = mailbox_case()
    code = str(secrets.randbelow(900000) + 100000)
    deliver(subject, f"Your verification code is {code}.")
    value, identity = resolve(load(), field, since)
    assert hmac.compare_digest(value, code), "The received code did not match the delivered code"
    assert identity.startswith("imap:")
    client = imap_connection(load()["imap"])
    try:
        status, rows = client.uid("search", None, "SUBJECT", '"' + subject + '"', "SEEN")
        assert status == "OK" and rows == [b""]
    finally:
        client.logout()


def test_placeholder_contract_and_single_use(tmp_path):
    subject, field, since = mailbox_case()
    code = str(secrets.randbelow(900000) + 100000)
    deliver(subject, f"Your verification code is {code}.")
    broker = Broker(
        load(),
        {"origins": ["https://account-checker.test"], "fields": {"email_code": field}},
        tmp_path,
        since=since,
    )
    placeholder = broker.request("email_code")
    assert placeholder["placeholder"].startswith("{{secret:")
    assert code not in json.dumps(placeholder)
    value = broker.consume(placeholder["placeholder"], "https://account-checker.test")
    assert hmac.compare_digest(value, code), "Placeholder resolved to the wrong code"
    with pytest.raises(Unavailable, match="expired or already used"):
        broker.consume(placeholder["placeholder"], "https://account-checker.test")
    assert code not in (tmp_path / "secret-events.jsonl").read_text()
    # New broker processes cannot consume the same message again.
    second = Broker(load(), broker.binding, tmp_path, since=since)
    another = second.request("email_code")["placeholder"]
    with pytest.raises(Unavailable, match="already used"):
        second.consume(another, "https://account-checker.test")


def test_wrong_origin_and_ambiguous_messages_are_rejected(tmp_path):
    subject, field, since = mailbox_case()
    deliver(subject, "Your verification code is 619582.")
    deliver(subject, "Your verification code is 814362.")
    broker = Broker(
        load(),
        {"origins": ["https://account-checker.test"], "fields": {"email_code": field}},
        tmp_path,
        since=since,
    )
    placeholder = broker.request("email_code")["placeholder"]
    with pytest.raises(Unavailable, match="destination"):
        broker.consume(placeholder, "https://account-checker.test.attacker.invalid")
    with pytest.raises(Unavailable, match="More than one"):
        broker.consume(placeholder, "https://account-checker.test")


def test_wrong_sender_stale_mail_and_unapproved_link_are_rejected():
    subject, field, since = mailbox_case()
    deliver(subject, "Your verification code is 981264.", "unrelated@account-checker.test")
    with pytest.raises(Unavailable, match="No fresh"):
        resolve(load(), field, since)
    deliver(subject, "Your verification code is 719348.")
    with pytest.raises(Unavailable, match="No fresh"):
        resolve(load(), field, datetime.now(UTC) + timedelta(seconds=2))
    link_field = {**field, "kind": "link", "origins": ["https://account-checker.test"]}
    subject2, _, since2 = mailbox_case()
    link_field["subject"] = subject2
    deliver(subject2, "Verify: https://account-checker.test.attacker.invalid/?token=private")
    with pytest.raises(Unavailable, match="No fresh"):
        resolve(load(), link_field, since2)
