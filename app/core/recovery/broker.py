import os
import secrets
import time
from datetime import UTC, datetime

from ..storage import fingerprint, private_dir
from . import sources
from .config import Unavailable, audit, origin


class Broker:
    def __init__(self, config, binding, directory, *, since=None):
        self.config, self.binding, self.directory = config, binding, directory
        self.since = since or datetime.now(UTC)
        self.placeholders = {}

    def request(self, purpose):
        field = self.binding.get("fields", {}).get(purpose)
        if not field:
            raise Unavailable("No credential source is configured for this purpose")
        if purpose not in {"username", "password", "otp", "email_code", "sms_code", "email_link"}:
            raise Unavailable("Unsupported credential purpose")
        placeholder = "{{secret:" + secrets.token_hex(16) + "}}"
        self.placeholders[placeholder] = (field, time.monotonic() + 120, purpose)
        audit(self.directory, "placeholder_created", "ready", field["source"])
        return {
            "placeholder": placeholder,
            "purpose": purpose,
            "source": field["source"],
            "expires_in_seconds": 120,
        }

    def purpose(self, placeholder):
        field = self.placeholders.get(placeholder)
        if not field or field[1] < time.monotonic():
            raise Unavailable("Placeholder is expired or already used")
        return field[2]

    def consume(self, placeholder, destination):
        self.purpose(placeholder)
        if origin(destination) not in self.binding["origins"]:
            raise Unavailable("Credential destination is outside the approved login origins")
        field, _, purpose = self.placeholders[placeholder]
        value, message_id = sources.resolve(self.config, field, self.since)
        if purpose in {"otp", "email_code", "sms_code"} and not value.isalnum():
            raise Unavailable("Provider did not return a verification code")
        if message_id:
            # Cross-process single use. HMACs cannot be used to guess low-entropy codes.
            from ..storage import data_root

            used = private_dir(data_root() / "recovery-used-messages")
            key = fingerprint(message_id)
            try:
                fd = os.open(used / key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                raise Unavailable("Verification message was already used") from None
            with os.fdopen(fd, "w") as f:
                f.write(datetime.now(UTC).isoformat())
        del self.placeholders[placeholder]
        audit(self.directory, "secret_delivered_locally", "consumed", field["source"])
        return value
