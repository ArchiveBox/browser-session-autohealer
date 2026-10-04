import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from plain.runtime import settings

from ..storage import cipher, data_root, private_dir


class Unavailable(Exception):
    """Only fixed, application-authored messages may cross the MCP boundary."""


def config_path():
    return Path(settings.APP_CONFIG_DIR) / "recovery.json"


def load():
    path = config_path()
    return json.loads(path.read_text()) if path.exists() else {"accounts": {}}


def save(value):
    path = config_path()
    temporary = path.with_suffix(".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(value, f, indent=2)
    os.replace(temporary, path)


def local_test_password():
    """Only the loopback test mailbox uses a locally encrypted credential."""
    return cipher().decrypt((data_root() / "mail-test/password.enc").read_bytes()).decode()


def origin(url):
    parsed = urlsplit(url)
    if parsed.username or parsed.password or not parsed.hostname:
        raise Unavailable("Invalid login destination")
    local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not local_http:
        raise Unavailable("Login destinations must use HTTPS")
    port = parsed.port
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    default_port = 80 if local_http else 443
    return f"{parsed.scheme}://{host}" + (f":{port}" if port and port != default_port else "")


def audit(directory, action, status, source=""):
    """No tool arguments, messages, references, URLs, exceptions, or secret hashes."""
    from datetime import UTC, datetime

    path = private_dir(directory) / "secret-events.jsonl"
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600), "a") as f:
        f.write(
            json.dumps(
                {
                    "at": datetime.now(UTC).isoformat(),
                    "action": action,
                    "status": status,
                    "source": source,
                }
            )
            + "\n"
        )


CONNECTORS = {
    'onepassword': ('1Password', 'Passwords & authenticator codes', '◈'),
    'imap': ('Email', 'Verification codes & sign-in links', '✉'),
    'googlevoice': ('Google Voice', 'Verification text messages', '☎'),
    'imessage': ('Messages on this Mac', 'iMessage & forwarded SMS', '▣'),
}


def scope_chain(account=None, check=None, persona=None):
    chain = ['shared']
    persona = account.persona if account else persona
    if persona:
        chain.append(f'persona:{persona.id}')
    if account:
        chain.append(f'site:{account.id}')
    if check:
        chain.append(f'check:{check.id}')
    return chain


def effective_integration(cfg, key, chain, account=None):
    legacy = cfg.get('accounts', {}).get(str(account.id), {}) if account else {}
    result = {}
    inherited_from = 'Default'
    for scope in chain:
        if account and scope == f'site:{account.id}':
            for purpose, field in legacy.get('fields', {}).items():
                if field.get('source') != key:
                    continue
                if key == 'onepassword':
                    result[purpose + '_ref'] = field.get('reference', '')
                else:
                    result.update({k: v for k, v in field.items() if k != 'source'})
                result.setdefault('enabled', True)
                inherited_from = scope
        values = cfg.get('integration_scopes', {}).get(scope, {}).get(key, {})
        result.update(values)
        if 'enabled' in values:
            inherited_from = scope
    result.setdefault('enabled', False)
    return result, inherited_from


def effective_binding(cfg, account, check=None):
    """Resolve permission and reference inheritance once, before opening a browser."""
    from copy import deepcopy

    chain = scope_chain(account, check)
    binding = deepcopy(cfg.get('accounts', {}).get(str(account.id), {}))
    fields = binding.setdefault('fields', {})
    for scope in chain:
        binding.update(cfg.get('integration_scopes', {}).get(scope, {}).get('login', {}))
    for key in CONNECTORS:
        options, _ = effective_integration(cfg, key, chain, account)
        if not options['enabled']:
            fields = {p: f for p, f in fields.items() if f.get('source') != key}
            continue
        if key == 'onepassword':
            for purpose in ('username', 'password', 'otp'):
                if options.get(purpose + '_ref'):
                    fields[purpose] = {'source': key, 'reference': options[purpose + '_ref']}
        else:
            purpose = ('email_link' if options.get('kind') == 'link' else 'email_code') if key == 'imap' else 'sms_code'
            current = fields.get(purpose, {})
            current = current if current.get('source') == key else {}
            message = {k: v for k, v in options.items() if k != 'enabled'}
            if message:
                fields[purpose] = {**current, **message, 'source': key}
    binding['fields'] = fields
    return binding
