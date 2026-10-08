"""Optional, resumable account setup using the ordinary fix/check browser lifecycle."""
import fcntl
import json
import re
import secrets
from contextlib import contextmanager
from datetime import date
from uuid import UUID

from plain.postgres import transaction

from . import services, storage
from .models import Account, Check, PersonaSetup, Provider, Site, TaskRule
from .recovery.config import (
    Unavailable,
    effective_binding,
    effective_integration,
    load,
    scope_chain,
)

FACTS = (
    ('first_name', 'First name', 'given-name'), ('last_name', 'Last name', 'family-name'),
    ('username', 'Username', 'username'), ('dob', 'Date of birth', 'bday'),
    ('address1', 'Address 1', 'address-line1'), ('address2', 'Address 2', 'address-line2'),
    ('city', 'City', 'address-level2'), ('state', 'State / province', 'address-level1'),
    ('country', 'Country', 'country-name'), ('zip', 'ZIP / postal code', 'postal-code'),
    ('email', 'Email', 'email'), ('phone', 'Phone number', 'tel'),
)
OPTIONS = ('create_gmail', 'cloaked_email', 'cloaked_phone', 'save_onepassword')
AUTOFILL_PURPOSES = [key for key, _, _ in FACTS] + ['dob_day', 'dob_month', 'dob_month_name', 'dob_year']
SITES = {
    'gmail': {'domain': 'google.com', 'title': 'Gmail',
              'url': 'https://accounts.google.com/signup', 'check_url': 'https://mail.google.com/',
              'origins': ['https://accounts.google.com', 'https://mail.google.com',
                          'https://myaccount.google.com', 'https://www.youtube.com', 'https://studio.youtube.com']},
    'youtube': {'domain': 'youtube.com', 'title': 'YouTube',
        'url': 'https://www.youtube.com/', 'check_url': 'https://www.youtube.com/account',
        'origins': ['https://www.youtube.com', 'https://studio.youtube.com', 'https://accounts.google.com']},
    'facebook': {'domain': 'facebook.com', 'title': 'Facebook',
                 'url': 'https://www.facebook.com/r.php', 'check_url': 'https://www.facebook.com/',
                 'origins': ['https://www.facebook.com', 'https://facebook.com', 'https://accounts.google.com']},
    'instagram': {'domain': 'instagram.com', 'title': 'Instagram',
        'url': 'https://www.instagram.com/', 'check_url': 'https://www.instagram.com/',
        'origins': ['https://www.instagram.com', 'https://www.facebook.com', 'https://facebook.com']},
    'x': {'domain': 'x.com', 'title': 'X',
        'url': 'https://x.com/i/flow/signup', 'check_url': 'https://x.com/home',
        'origins': ['https://x.com', 'https://twitter.com', 'https://accounts.google.com']},
    'tiktok': {'domain': 'tiktok.com', 'title': 'TikTok',
        'url': 'https://www.tiktok.com/signup', 'check_url': 'https://www.tiktok.com/',
        'origins': ['https://www.tiktok.com', 'https://accounts.google.com']},
    'reddit': {'domain': 'reddit.com', 'title': 'Reddit',
        'url': 'https://www.reddit.com/register/', 'check_url': 'https://www.reddit.com/',
        'origins': ['https://www.reddit.com', 'https://reddit.com', 'https://accounts.google.com']},
}
SKILLS = {
    'gmail': 'Create Gmail or sign into the supplied existing account as selected. Set the Cloaked inbox as recovery email and the Cloaked phone as recovery phone; verify both. Confirm Gmail inbox access.',
    'youtube': 'Reuse Google sign-in. Complete first-use YouTube setup and create the researcher channel with the supplied name/handle. Confirm the channel exists. Do not upload or publish content.',
    'facebook': 'Create Facebook using the Gmail address and shared phone. Complete email/phone verification and required profile setup. Confirm the signed-in account.',
    'instagram': 'Use Facebook login when offered; otherwise use Gmail and the shared phone. Finish Instagram profile setup and verify the signed-in profile.',
    'x': 'Use Google login when offered; otherwise sign up with Gmail and the shared phone. Finish required account setup and verify the signed-in profile.',
    'tiktok': 'Use Google login when offered; otherwise sign up with Gmail and the shared phone. Finish required account setup and verify the signed-in profile.',
    'reddit': 'Use Google login when offered; otherwise sign up with Gmail. Set the researcher username and finish required account setup. Verify the signed-in profile.',
}
DEPENDENCIES = {key: (['gmail', 'facebook'] if key == 'instagram' else ['gmail']) for key in SITES if key != 'gmail'}
MAIL_DOMAINS = {'gmail': ['google.com'], 'youtube': ['google.com', 'youtube.com'],
                'facebook': ['facebookmail.com', 'facebook.com'],
                'instagram': ['instagram.com', 'facebookmail.com'],
                'x': ['x.com', 'twitter.com'], 'tiktok': ['tiktok.com'],
                'reddit': ['reddit.com', 'redditmail.com']}
ACTIVE = ('queued', 'starting', 'running', 'finishing')


@contextmanager
def lock(key):
    # Serialize local setup/connector operations without holding a DB transaction
    # across browser calls, encrypted-object writes, or password-manager commands.
    with (storage.private_dir(storage.data_root() / 'setup-locks') / str(key)).open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read(setup):
    return json.loads(storage.cipher().decrypt(setup.private_data.encode()))


def encrypt(data):
    return storage.cipher().encrypt(storage.canonical(data)).decode()


def parse(form):
    facts = {key: form.get(key, '').strip() for key, _, _ in FACTS}
    if any(len(value) > 300 for value in facts.values()):
        raise ValueError('Keep autofill fields under 300 characters')
    if facts['dob']:
        try:
            birthday = date.fromisoformat(facts['dob'])
        except ValueError:
            raise ValueError('Use a valid date of birth') from None
        if birthday > services.now().date():
            raise ValueError('Date of birth cannot be in the future')
    if facts['email'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', facts['email']):
        raise ValueError('Enter a valid email address')
    options = {key: form.get(key) == 'on' for key in OPTIONS}
    selected = form.get('sites', ','.join(SITES)).split(',')
    selected = [s for s in selected if s]
    if len(set(selected)) != len(selected) or any(s not in SITES for s in selected):
        raise ValueError('Choose each suggested site at most once')
    for i, site in enumerate(selected):
        if any(dependency not in selected[:i] for dependency in DEPENDENCIES.get(site, [])):
            raise ValueError('Put Gmail first, and Facebook before Instagram')
    return {'facts': facts, 'options': options, 'accounts': {}, 'sites': selected}


def create(form, actor):
    try:
        key = UUID(form.get('key', ''))
    except ValueError:
        raise ValueError('Reload the setup form and try again') from None
    data = parse(form)
    name = form.get('name', '').strip() or f'Researcher {str(key)[:8]}'
    if len(name) > 160:
        raise ValueError('Persona name must be at most 160 characters')
    with lock(key):
        existing = PersonaSetup.query.filter(key=key).first()
        if existing:
            return existing
        private_data = encrypt(data)
        persona = services.create_persona(name, actor=actor)
        if data['options']['cloaked_email'] or data['options']['cloaked_phone']:
            from .recovery.config import save
            cfg = load()
            cfg.setdefault('integration_scopes', {}).setdefault(f'persona:{persona.id}', {})['cloaked'] = {'enabled': True}
            save(cfg)
        return PersonaSetup.query.create(key=key, persona=persona, private_data=private_data)


def steps(data):
    return data['sites']


def binding(task):
    setup = PersonaSetup.query.get(persona=task.account.persona)
    site = task.pattern.removeprefix('signup:')
    data, cfg = read(setup), load()
    result = effective_binding(cfg, task.account, task)
    result.update(start_url=task.url, origins=SITES[site]['origins'],
                  signup={'setup_id': setup.id, 'site': site,
                          'chain': [{'site': key, 'skill': SKILLS[key]} for key in steps(data)]})
    fields = result.setdefault('fields', {})
    purposes = list(AUTOFILL_PURPOSES)
    if site != 'gmail' or data['options']['create_gmail']:
        purposes.append('password')
    for purpose in purposes:
        fields[purpose] = {'source': 'signup', 'setup_id': setup.id, 'site': site, 'purpose': purpose}
    if data['accounts'].get(site, {}).get('totp') or 'otp' not in fields:
        fields['otp'] = {'source': 'signup', 'setup_id': setup.id, 'site': site, 'purpose': 'otp'}
    cloaked, _ = effective_integration(cfg, 'cloaked', scope_chain(task.account, task), task.account)
    if cloaked['enabled']:
        for purpose, channel in [('sms_code', 'sms'), ('email_code', 'email'), ('email_link', 'email')]:
            if data['options']['cloaked_phone' if channel == 'sms' else 'cloaked_email'] and (channel == 'sms' or site == 'gmail'):
                fields[purpose] = {**cloaked, 'source': 'cloaked', 'setup_id': setup.id,
                    'site': site, 'channel': channel, 'kind': 'link' if purpose == 'email_link' else 'code',
                    'origins': SITES[site]['origins']}
    return result


def saved_login(account):
    """Use verified setup credentials for the persona's ordinary recovery tasks."""
    setup = PersonaSetup.query.filter(persona=account.persona).first()
    site = next((key for key, spec in SITES.items() if spec['domain'] == account.site.domain), None)
    if not setup or site not in setup.completed:
        return {}
    data = read(setup)
    saved = data['accounts'].get(site, {})
    if not saved.get('credentials_saved'):
        return {}
    authentication = saved.get('authentication', 'password')
    credential_site = {'google':'gmail', 'facebook':'facebook'}.get(authentication, site)
    from urllib.parse import urlsplit
    domain = SITES[credential_site]['domain']
    origins = [url for url in SITES[credential_site]['origins']
               if urlsplit(url).hostname == domain or urlsplit(url).hostname.endswith('.' + domain)]
    fields = {}
    for purpose in ('username', 'password', 'otp'):
        if data['accounts'][credential_site].get('totp' if purpose == 'otp' else purpose):
            fields[purpose] = {'source':'signup', 'setup_id':setup.id, 'site':credential_site,
                               'purpose':purpose, 'origins':origins}
    return {'fields':fields, 'origins':SITES[site]['origins'], 'authentication':authentication}


def queue(setup_id, provider_id, actor):
    with lock(f'setup-{setup_id}'):
        setup = PersonaSetup.query.get(id=setup_id)
        if setup.status == 'complete' or setup.run and setup.run.status in ACTIVE:
            return setup
        provider = Provider.query.filter(id=provider_id, enabled=True).first()
        if not provider:
            raise ValueError('Choose an enabled browser provider')
        data = read(setup)
        if not steps(data):
            raise ValueError('Add at least one suggested site before starting')
        from .site_scope import matches
        if provider.site_scope is not None and any(
            not matches(SITES[site]['domain'], provider.site_scope) for site in steps(data)
        ):
            raise ValueError('Choose a browser provider that allows all selected signup sites')
        cfg = load()
        cloaked, _ = effective_integration(cfg, 'cloaked', scope_chain(persona=setup.persona))
        if (data['options']['cloaked_email'] or data['options']['cloaked_phone']) and (
            not cloaked['enabled'] or not cfg.get('cloaked', {}).get('cdp_url')
        ):
            raise ValueError('Connect and enable Cloaked for this persona in Integrations first')
        site = next((s for s in steps(data) if s not in setup.completed), None)
        if not site:
            return setup
        spec = SITES[site]
        # Keep passwords stable across interruption/retries. They never enter a
        # browser-settings snapshot, task prompt, account label, or Run JSON.
        account_data = data['accounts'].setdefault(site, {})
        if site != 'gmail' or data['options']['create_gmail']:
            account_data.setdefault('password', secrets.token_urlsafe(30))
        if site == 'gmail' and data['options']['create_gmail']:
            username = data['facts']['username'] or 'researcher' + str(setup.key).replace('-', '')[:14]
            if not re.fullmatch(r'[A-Za-z0-9.]{6,30}', username):
                raise ValueError('For Gmail, choose a username of 6–30 letters, numbers or periods')
            account_data.setdefault('username', username)
            account_data.setdefault('email', username + '@gmail.com')
        elif site == 'gmail':
            account_data['username'] = data['facts']['email']
            account_data['email'] = data['facts']['email']
        setup.private_data = encrypt(data)
        setup.update(fields=['private_data'])
        with transaction.atomic():
            domain, _ = Site.query.get_or_create(domain=spec['domain'])
            account, _ = Account.query.get_or_create(persona=setup.persona, site=domain)
            verify, _ = Check.query.get_or_create(account=account, provider=provider,
                pattern='signup-verify:' + site, defaults={
                    'name': f'Verify {spec["title"]} account', 'mode': 'check', 'enabled': False,
                    'url': spec['check_url'], 'instruction':
                        f'Verify this newly configured {spec["title"]} account is signed in and usable. '
                        'Inspect the signed-in account identity and actual inbox or account home. '
                        'A signup form, challenge, consent screen or disabled account is not success. '
                        'Do not submit signup forms or change anything. Save screenshot evidence.'})
            fix, _ = Check.query.get_or_create(account=account, provider=provider,
                pattern='signup:' + site, defaults={
                    'name': f'Set up {spec["title"]} account', 'mode': 'fix',
                    'url': ('https://accounts.google.com/' if site == 'gmail'
                            and not data['options']['create_gmail'] else spec['url']),
                    'instruction': SKILLS[site]})
            TaskRule.query.get_or_create(source=fix, target=verify, status='success', state='')
            # Verification is invoked inside the setup run, never by the periodic scheduler.
            services.record_definition(fix, actor)
            services.record_definition(verify, actor)
            run = services.checkout(setup.persona.id, provider.id, spec['domain'], actor,
                check_ids=[fix.id], base_digest=((setup.run.tip or setup.run.base.digest)
                    if setup.run and setup.status == 'needs_human' else None))
            setup.run, setup.status = run, 'running'
            setup.update(fields=['run', 'status'])
        return setup


def prepare(setup_id, site):
    """Provision only requested contacts, reusing the exact saved Cloaked identity."""
    with lock(f'setup-{setup_id}'):
        setup = PersonaSetup.query.get(id=setup_id)
        data = read(setup)
        account = data['accounts'][site]
        if not data.get('cloaked', {}).get('inbox_url') and any(data['options'][key] for key in ('cloaked_email', 'cloaked_phone')):
            from .cloaked import ensure_identity
            identity = ensure_identity(load(), setup, site,
                email=data['options']['cloaked_email'], phone=data['options']['cloaked_phone'],
                identity=data.get('cloaked'))
            data['cloaked'] = identity
            # Preserve provisioning immediately, even if a later signup step stops.
        if site != 'gmail':
            account['email'] = data['accounts'].get('gmail', {}).get('email') or data['facts']['email']
            account['username'] = account['email'] or data['facts']['username']
        setup.private_data = encrypt(data)
        setup.update(fields=['private_data'])
        purposes = [key for key in [*AUTOFILL_PURPOSES, 'password'] if value(data, site, key)]
        return {'status': 'ready', 'available_purposes': purposes,
                'create_account': site != 'gmail' or data['options']['create_gmail'],
                'missing_facts': [key for key, _, _ in FACTS if not value(data, site, key)]}


def value(data, site, purpose):
    if purpose.startswith('dob_'):
        birthday = data['facts'].get('dob')
        if not birthday:
            return ''
        birthday = date.fromisoformat(birthday)
        return {'dob_day': str(birthday.day), 'dob_month': str(birthday.month),
                'dob_month_name': birthday.strftime('%B'), 'dob_year': str(birthday.year)}.get(purpose, '')
    account = data['accounts'][site]
    if purpose == 'phone' and data['options']['cloaked_phone']:
        return data.get('cloaked', {}).get('phone', '')
    if purpose == 'email' and site == 'gmail' and data['options']['create_gmail']:
        # Google asks for a recovery address, not its own not-yet-created inbox.
        return data.get('cloaked', {}).get('email') or data['facts']['email']
    return account.get(purpose) or data['facts'].get(purpose, '')


def resolve(field):
    setup = PersonaSetup.query.get(id=field['setup_id'])
    data = read(setup)
    if field['purpose'] == 'otp':
        return totp(data['accounts'][field['site']].get('totp', '')), None
    result = value(data, field['site'], field['purpose'])
    if not result:
        raise Unavailable('This autofill fact is missing; update persona setup to continue')
    return result, None


def totp(secret):
    import base64
    import hashlib
    import hmac
    import struct
    import time
    if not secret:
        raise Unavailable('No authenticator has been enrolled for this account')
    try:
        key = base64.b32decode(secret.upper() + '=' * (-len(secret) % 8))
    except ValueError:
        raise Unavailable('Invalid authenticator secret') from None
    digest = hmac.new(key, struct.pack('>Q', int(time.time()) // 30), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return str((struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7fffffff) % 1000000).zfill(6)


def save_security(setup_id, site, purpose, text):
    if purpose == 'totp':
        text = re.sub(r'\s+', '', text).upper()
        if not re.fullmatch(r'[A-Z2-7]{16,128}', text):
            raise Unavailable('Select only the manual authenticator setup key')
        totp(text)
    elif purpose == 'recovery_codes':
        if not text.strip() or len(text) > 8000:
            raise Unavailable('Select the recovery-code list')
    else:
        raise Unavailable('Unsupported security field')
    with lock(f'setup-{setup_id}'):
        setup = PersonaSetup.query.get(id=setup_id)
        data = read(setup)
        data['accounts'][site][purpose] = text
        setup.private_data = encrypt(data)
        setup.update(fields=['private_data'])


def save_account(setup_id, site, authentication='password'):
    from urllib.parse import quote

    from .recovery.sources import private_command
    with lock(f'setup-{setup_id}'):
        setup = PersonaSetup.query.get(id=setup_id)
        data = read(setup)
        account = data['accounts'][site]
        if authentication not in {'password', 'google', 'facebook'}:
            raise Unavailable('Choose password, google or facebook authentication')
        dependency = {'google': 'gmail', 'facebook': 'facebook'}.get(authentication)
        if dependency and dependency not in setup.completed:
            raise Unavailable('Verify the identity provider account first')
        account['authentication'] = authentication
        if not data['options']['save_onepassword']:
            account['credentials_saved'] = True
            setup.private_data = encrypt(data)
            setup.update(fields=['private_data'])
            return {'status': 'saved', 'storage': 'encrypted persona storage'}
        cfg = load().get('onepassword', {})
        if not cfg.get('vault'):
            raise Unavailable('Choose a 1Password vault in Integrations')
        title = f'Autohealer {setup.key} {SITES[site]["title"]}'
        args = ['--vault', cfg['vault'], '--format=json']
        if cfg.get('account'):
            args += ['--account', cfg['account']]
        items = json.loads(private_command(['op', 'item', 'list', *args]))
        matches = [item for item in items if item['title'] == title]
        if len(matches) > 1:
            raise Unavailable('More than one 1Password item matches this researcher account')
        fields = [{'id': 'username', 'type': 'STRING', 'purpose': 'USERNAME',
                   'value': account.get('email') or account.get('username', '')}]
        if account.get('password') and authentication == 'password':
            fields.append({'id': 'password', 'type': 'CONCEALED', 'purpose': 'PASSWORD', 'value': account['password']})
        if account.get('totp'):
            fields.append({'id': 'totp', 'label': 'one-time password', 'type': 'OTP',
                'value': f'otpauth://totp/{quote(title)}?secret={account["totp"]}&issuer=Autohealer'})
        if account.get('recovery_codes'):
            fields.append({'id': 'recovery_codes', 'label': 'Recovery codes', 'type': 'CONCEALED', 'value': account['recovery_codes']})
        payload = {'title': title, 'category': 'LOGIN', 'fields': fields,
                   'urls': [{'href': SITES[site]['check_url'], 'primary': True}]}
        if dependency:
            payload['fields'].append({'id': 'notesPlain', 'type': 'STRING',
                'purpose': 'NOTES', 'value': 'Sign in using ' + SITES[dependency]['title'] + ' for this persona.'})
        command = ['op', 'item', 'edit', matches[0]['id']] if matches else ['op', 'item', 'create']
        item = json.loads(private_command([*command, *args], stdin=json.dumps(payload)))
        account['onepassword_item'] = item['id']
        account['credentials_saved'] = True
        setup.private_data = encrypt(data)
        setup.update(fields=['private_data'])
        return {'status': 'saved', 'storage': '1Password and encrypted persona storage'}


def browser_email(raw, setup_id, site, since, purpose, subject):
    """Parse Gmail's Show original view locally, requiring authenticated sender and receipt time."""
    import email.policy
    from email.parser import Parser
    from email.utils import getaddresses, parsedate_to_datetime

    from .recovery.sources import extract

    if purpose not in {'email_code', 'email_link'} or not subject.strip() or len(raw) > 1_000_000:
        raise Unavailable('Choose a verification message and its expected subject')
    message = Parser(policy=email.policy.default).parsestr(raw)
    sender = getaddresses(message.get_all('From', []))
    if len(sender) != 1:
        raise Unavailable('Verification sender is ambiguous')
    domain = sender[0][1].lower().rsplit('@', 1)[-1]
    if not any(domain == root or domain.endswith('.' + root) for root in MAIL_DOMAINS[site]):
        raise Unavailable('Verification sender does not belong to this signup site')
    auth = next((str(h) for h in message.get_all('Authentication-Results', [])
                 if str(h).split(';', 1)[0].strip() == 'mx.google.com'), '')
    if not re.search(r'dkim=pass\b[^;]*\bheader\.(?:d|i)=@?' + re.escape(domain) + r'(?:\s|;|$)', auth):
        raise Unavailable('Gmail did not authenticate this verification sender')
    try:
        received = parsedate_to_datetime(str(message.get('X-Received', '')).rsplit(';', 1)[-1].strip())
        if received.tzinfo is None or not since <= received <= services.now():
            raise ValueError
    except (ValueError, TypeError):
        raise Unavailable('Verification message is not fresh for this session') from None
    data = read(PersonaSetup.query.get(id=setup_id))
    recipient = data['accounts']['gmail']['email'].lower()
    recipients = {address.lower() for _,address in getaddresses(message.get_all('To', []) + message.get_all('Delivered-To', []))}
    if recipient not in recipients or subject.casefold() not in str(message.get('Subject', '')).casefold():
        raise Unavailable('Verification recipient or subject does not match')
    parts = [p for p in message.walk() if p.get_content_type() == 'text/plain' and p.get_content_disposition() != 'attachment']
    if not parts:
        parts = [p for p in message.walk() if p.get_content_type() == 'text/html' and p.get_content_disposition() != 'attachment']
    identity = 'gmail:' + storage.fingerprint([recipient, str(message.get('Message-ID', '')), raw])
    return extract([('\n'.join(p.get_content() for p in parts), identity)],
                   {'kind': 'link' if purpose == 'email_link' else 'code', 'origins': SITES[site]['origins']})


def update(setup, form):
    with lock(f'setup-{setup.id}'):
        setup = PersonaSetup.query.get(id=setup.id)
        if setup.run and setup.run.status in ACTIVE:
            raise ValueError('Wait for the active setup session before editing facts')
        previous, fresh = read(setup), parse(form)
        if fresh['sites'][:len(setup.completed)] != setup.completed:
            raise ValueError('Keep completed sites at the start of the chain')
        if setup.run and any(fresh['options'][key] != previous['options'][key] for key in ('cloaked_phone', 'cloaked_email')):
            raise ValueError('Contact creation choices are fixed once setup has started')
        if fresh['options']['create_gmail'] != previous['options']['create_gmail']:
            if 'gmail' in setup.completed:
                raise ValueError('The verified Gmail account cannot be replaced in this persona')
            if setup.run and fresh['options']['create_gmail']:
                raise ValueError('Connect the existing Gmail account before changing the chain')
            if not fresh['options']['create_gmail'] and not fresh['facts']['email']:
                raise ValueError('Enter the existing Gmail address to connect')
            previous['accounts'].pop('gmail', None)
        previous.update(facts=fresh['facts'], sites=fresh['sites'], options=fresh['options'])
        setup.private_data = encrypt(previous)
        if setup.status == 'complete' and setup.completed != fresh['sites']:
            setup.status = 'ready'
        setup.update(fields=['private_data', 'status'])
        return setup


def finished(run):
    setup = PersonaSetup.query.filter(run=run).first()
    if not setup:
        return
    site = run.plan[0]['domain']
    site = next(key for key, spec in SITES.items() if spec['domain'] == site)
    if run.status != 'success' or not run.promoted:
        setup.status = 'needs_human'
        setup.update(fields=['status'])
        return
    with lock(f'setup-{setup.id}'):
        setup = PersonaSetup.query.get(id=setup.id)
        setup.completed = list(dict.fromkeys([*setup.completed, site]))
        setup.status = 'complete' if setup.completed == steps(read(setup)) else 'ready'
        setup.update(fields=['completed', 'status'])
        from datetime import timedelta
        with transaction.atomic():
            for task in Check.query.filter(account__persona=setup.persona, provider=run.provider,
                pattern__in=['signup:' + site, 'signup-verify:' + site]):
                task.enabled = task.mode == 'check'
                task.next_due = services.now() + timedelta(seconds=task.interval_seconds)
                task.update(fields=['enabled', 'next_due'])
                services.record_definition(task, 'persona-setup')
    if setup.status == 'ready':
        queue(setup.id, run.provider.id, 'persona-setup')
