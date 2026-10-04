"""Start a real, disposable Postfix/Dovecot inbox, bound only to loopback."""

import json
import os
import secrets
import subprocess
import time

from plain.runtime import setup

setup()

from app.core.recovery import config, sources
from app.core.storage import cipher, data_root, private_dir

name = "account-checker-mail-test"
image = "ghcr.io/docker-mailserver/docker-mailserver:15.1.0"
cfg = config.load()
if cfg.get("imap") and not cfg["imap"].get("local_test"):
    raise SystemExit("A personal mailbox is configured; leave it unchanged")
root = private_dir(data_root() / "mail-test")
settings = private_dir(root / "config")
password_file = root / "password.enc"
if not password_file.exists():
    password = secrets.token_urlsafe(32)
    with os.fdopen(os.open(password_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as f:
        f.write(cipher().encrypt(password.encode()))
else:
    password = cipher().decrypt(password_file.read_bytes()).decode()
accounts = settings / "postfix-accounts.cf"
if not accounts.exists():
    result = subprocess.run(
        ["docker", "run", "--rm", "-i", "--entrypoint", "openssl", image,
         "passwd", "-6", "-stdin"],
        input=password + "\n", text=True, capture_output=True, check=True,
    )
    with os.fdopen(os.open(accounts, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as f:
        f.write("researcher@account-checker.test|{SHA512-CRYPT}" + result.stdout.strip() + "\n")
(settings / "postfix-main.cf").write_text(
    "smtpd_peername_lookup = no\npostscreen_greet_wait = 1s\npostscreen_dnsbl_sites =\n"
    "smtpd_sender_restrictions = reject_non_fqdn_sender\n"
    "smtpd_recipient_restrictions = reject_unauth_destination\n"
)
existing = subprocess.run(["docker", "inspect", name], capture_output=True, text=True, check=False)
if existing.returncode == 0:
    container = json.loads(existing.stdout)[0]
    assert container["Config"]["Image"] == image
    ports = container["HostConfig"]["PortBindings"]
    assert ports["25/tcp"] == [{"HostIp": "127.0.0.1", "HostPort": "54025"}]
    assert ports["143/tcp"] == [{"HostIp": "127.0.0.1", "HostPort": "54143"}]
    subprocess.run(["docker", "start", name], capture_output=True, check=True)
else:
    command = [
        "docker", "run", "-d", "--name", name, "--hostname", "mail.account-checker.test",
        "-p", "127.0.0.1:54025:25", "-p", "127.0.0.1:54143:143",
        "--mount", f"type=bind,src={settings},dst=/tmp/docker-mailserver",
    ]
    for key in (
        "ENABLE_SPAMASSASSIN", "ENABLE_FAIL2BAN", "ENABLE_UPDATE_CHECK", "ENABLE_OPENDKIM",
        "ENABLE_OPENDMARC", "ENABLE_POLICYD_SPF", "SPOOF_PROTECTION", "ENABLE_CLAMAV", "ENABLE_AMAVIS",
    ):
        command += ["-e", key + "=0"]
    subprocess.run(command + ["-e", "SSL_TYPE=", image], capture_output=True, check=True)
cfg["imap"] = {
    "local_test": True, "host": "127.0.0.1", "port": 54143,
    "username": "researcher@account-checker.test", "mailbox": "INBOX",
}
config.save(cfg)
deadline = time.monotonic() + 60
while sources.status(cfg, "imap") != "Connected":
    if time.monotonic() >= deadline:
        raise SystemExit("Test mailbox did not become ready; inspect its Docker logs")
    time.sleep(1)
print("Real test mailbox ready: SMTP 127.0.0.1:54025, IMAP 127.0.0.1:54143")
