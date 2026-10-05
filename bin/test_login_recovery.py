"""Queue a real local login using a disposable password delivered by SMTP.

Run after configuring the real loopback mail container and the clean Test
researcher persona. Never imports an existing browser or contacts Browserbase.
"""

import json
import secrets
import smtplib
from email.message import EmailMessage

from plain.runtime import setup

setup()

from app.core.models import Account, Checkpoint, Provider
from app.core.recovery import config
from app.core.recovery.runner import queue
from app.core.storage import read_state
from app.users.models import User

account = Account.query.get(persona__name="Test researcher", site__domain="127.0.0.1")
assert account.username == "researcher@account-checker.test"
provider = Provider.query.get(name="Local test browser")
assert provider.kind == "local" and provider.config["runtime"] == "host"
cfg = config.load()
assert cfg["imap"]["local_test"] and cfg["imap"]["host"] == "127.0.0.1"
password = secrets.token_urlsafe(30)
user = User.query.get(email=account.username)
user.password = password
user.update(fields=["password"])
subject = "Browser Session Autohealer test login " + secrets.token_hex(8)
cfg["accounts"][str(account.id)]["fields"]["password"]["subject"] = subject
config.save(cfg)
base = Checkpoint.query.filter(persona=account.persona, parent="").get()
assert read_state(base.digest).get("cookies") == [], "Test must begin without existing cookies"
run = queue(account, provider.id, "Real local email and Stagehand recovery test", base_digest=base.digest)
message = EmailMessage()
message["From"] = "security@account-checker.test"
message["To"] = account.username
message["Subject"] = subject
message.set_content("Login password: " + password + "\nDisposable local test credential.")
with smtplib.SMTP("127.0.0.1", 54025, timeout=10) as smtp:
    assert not smtp.send_message(message)
print(json.dumps({"recovery_run": run.id, "persona": account.persona.id, "mail": "delivered"}))
