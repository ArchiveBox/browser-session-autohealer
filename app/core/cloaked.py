"""Cloaked dashboard connector; no undocumented provisioning API or copied master cookies."""
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from . import storage
from .recovery.config import Unavailable


class CloakedUnavailable(Unavailable):
    def __init__(self, phase, creation_started):
        detail = f' ({phase})' if phase in {'connect', 'navigate', 'inspect', 'action'} else ''
        super().__init__('Cloaked needs attention' + detail + '; inspect its dedicated browser')
        self.creation_started = creation_started


def open_browser():
    """Open an app-owned, persistent local browser for the shared Cloaked account."""
    import httpx

    from . import services
    from .models import Provider, Run
    from .onboarding import lock
    from .providers import adapter, browser_command
    from .recovery.config import load, save
    with lock('cloaked-browser'):
        config = load()
        connector = config.setdefault('cloaked', {})
        if connector.get('run_id'):
            run = Run.query.get(id=connector['run_id'])
            try:
                httpx.get(run.runtime['cdp'].split('/devtools/', 1)[0].replace('ws://', 'http://') + '/json/version', timeout=2).raise_for_status()
                return run
            except (httpx.HTTPError, KeyError):
                raise Unavailable('The saved Cloaked browser has stopped. Reconnect it using a signed-in CDP browser.') from None
        provider = next((p for p in Provider.query.filter(kind='local', enabled=True)
                         if p.config.get('runtime') != 'docker'), None)
        if not provider:
            raise Unavailable('Configure a local host browser provider first')
        persona = services.create_persona('Cloaked connector', actor='cloaked:connect')
        run = services.checkout(persona.id, provider.id, '*', 'cloaked:connect', check_ids=[], external=True)
        run.runtime['provider_config'] = {**provider.config, 'headless': False}
        run.runtime['integration'] = 'cloaked'
        run.update(fields=['runtime'])
        try:
            run.runtime.update(adapter(provider).launch(run))
            run.update(fields=['runtime'])
            target = browser_command('prepare', run, state={'cookies': [], 'origins': []})
            browser_command('navigate', run, target=target['targetId'], url='https://my.cloaked.com/')
        except Exception:  # noqa: BLE001 - never expose browser authentication diagnostics
            services.record_issue(run.id, 'Could not open the Cloaked connector browser')
            raise Unavailable('Could not open the dedicated Cloaked browser') from None
        connector.update(cdp_url=run.runtime['cdp'], run_id=run.id)
        save(config)
        return run


def validate_connection(cfg):
    url = urlsplit(cfg.get('cdp_url', ''))
    if url.scheme not in {'http', 'https', 'ws', 'wss'} or not url.hostname:
        raise ValueError('Connect a dedicated signed-in Cloaked browser using its CDP URL')
    if url.scheme in {'http', 'ws'} and url.hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise ValueError('Remote Cloaked browser connections require HTTPS or WSS')


def call(config, action, **values):
    from .inference import config as inference_config
    from .onboarding import lock

    cfg = config.get('cloaked', {})
    try:
        validate_connection(cfg)
    except ValueError as error:
        raise Unavailable(str(error)) from None
    payload = {'action': action, 'cdp': cfg['cdp_url'],
               'apiKey': os.environ.get('OPENAI_API_KEY'),
               'model': inference_config()['model'], **values}
    # A single dedicated browser serves independent identities sequentially.
    # The research browser never receives this connection or Cloaked cookies.
    with lock('cloaked-browser'):
        result = subprocess.run(['node', str(Path(__file__).with_name('cloaked.mjs'))],
            input=json.dumps(payload), text=True, capture_output=True, timeout=240, check=False)
    lines = [line.removeprefix('CLOAKED_RESULT=') for line in result.stdout.splitlines()
             if line.startswith('CLOAKED_RESULT=')]
    if result.returncode or len(lines) != 1:
        raise Unavailable('Cloaked browser unavailable; check the dedicated connection')
    document = json.loads(lines[0])
    if document.get('status') != 'ok':
        raise CloakedUnavailable(document.get('phase'), document.get('creation_started'))
    return document


def ensure_identity(config, setup, site, *, email, phone, identity=None):
    label = f'Autohealer {setup.key}'
    # Record intent BEFORE external creation. Unknown outcomes may be located
    # by this exact label but never automatically allocate a second identity.
    directory = storage.private_dir(storage.data_root() / 'cloaked')
    path = directory / f'{setup.key}.enc'
    saved = json.loads(storage.cipher().decrypt(path.read_bytes())) if path.exists() else {}
    allow_create = not saved and not identity
    if allow_create:
        path.write_bytes(storage.cipher().encrypt(storage.canonical({'label': label, 'pending': True})))
        path.chmod(0o600)
    try:
        result = call(config, 'identity', label=label, email=email, phone=phone,
                      identity=identity or saved.get('identity'), allow_create=allow_create)['identity']
    except CloakedUnavailable as error:
        if allow_create and error.creation_started is False:
            path.unlink()  # Explicit evidence that no creation was attempted.
        raise
    if result.get('label') != label or urlsplit(result.get('url', '')).netloc != 'my.cloaked.com':
        raise Unavailable('Cloaked did not confirm the exact requested identity')
    inbox = urlsplit(result.get('inbox_url', ''))
    if inbox.scheme != 'https' or inbox.netloc != 'my.cloaked.com' or not re.fullmatch(r'/cloak/[^/]+/inbox', inbox.path):
        raise Unavailable('Cloaked did not confirm the researcher inbox')
    if email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', result.get('email', '')):
        raise Unavailable('Cloaked did not return an email address')
    if phone and not re.fullmatch(r'\+?[\d ()-]{8,25}', result.get('phone', '')):
        raise Unavailable('Cloaked did not return a phone number')
    path.write_bytes(storage.cipher().encrypt(storage.canonical({'label': label, 'identity': result})))
    path.chmod(0o600)
    return result


def messages(cfg, field, since):
    from .models import PersonaSetup
    from .onboarding import MAIL_DOMAINS, SITES, read

    identity = field.get('identity')
    if field.get('setup_id'):
        setup = PersonaSetup.query.get(id=field['setup_id'])
        identity = read(setup).get('cloaked')
    if not identity:
        raise Unavailable('Provision a Cloaked identity before requesting a verification message')
    site = field.get('site')
    if not field.get('senders') and site not in SITES:
        raise Unavailable('Configure allowed Cloaked verification senders in Integrations first')
    channel = field.get('channel', 'sms')
    recipient = identity.get('email' if channel == 'email' else 'phone')
    if not recipient:
        raise Unavailable('The Cloaked identity has no contact for this verification channel')
    result = call({'cloaked': cfg}, 'messages', identity=identity, channel=channel)
    rows = []
    for message in result.get('messages', []):
        sender = message.get('sender', '').lower()
        if field.get('senders'):
            allowed_sender = sender in {s.lower() for s in field['senders']}
        elif channel == 'email':
            from email.utils import parseaddr
            domain = parseaddr(sender)[1].rsplit('@', 1)[-1]
            allowed_sender = any(domain == root or domain.endswith('.' + root) for root in MAIL_DOMAINS[site])
        else:
            names = ['Google'] if site in {'gmail', 'youtube'} else ['Twitter', 'X'] if site == 'x' else [SITES[site]['title']]
            allowed_sender = any(re.search(r'\b' + re.escape(name) + r'\b', message.get('body', ''), re.IGNORECASE) for name in names)
        actual_recipient = message.get('recipient', '')
        same_recipient = (re.sub(r'\D', '', actual_recipient) == re.sub(r'\D', '', recipient)
                          if channel == 'sms' else actual_recipient.lower() == recipient.lower())
        if (message.get('channel') != channel or not allowed_sender
                or not same_recipient or message.get('inbound') is not True):
            continue
        try:
            when = datetime.fromisoformat(message['received_at'])
            if when.tzinfo is None:
                raise ValueError
        except (ValueError, KeyError):
            raise Unavailable('Cloaked did not expose a trustworthy message timestamp') from None
        if when < since or when > datetime.now(UTC):
            continue
        if field.get('subject') and field['subject'].casefold() not in message.get('subject', '').casefold():
            continue
        rows.append((message['body'], 'cloaked:' + storage.fingerprint(
            [identity['label'], channel, recipient, message['sender'], message['received_at'], message['body']])))
    return rows
