"""Shared connector setup and inherited persona/site/check configuration."""
import shlex
from datetime import UTC, datetime
from urllib.parse import urlencode

from plain.http import NotFoundError404, RedirectResponse

from .core.models import Account, AppConfig, Check, Persona
from .core.recovery import config, sources
from .core.recovery.runner import queue
from .views import Base

CONNECTORS = config.CONNECTORS
OPTIONS = {
    'cloaked': [('senders', 'Allowed verification senders'), ('subject', 'Subject contains'),
                ('pattern', 'Code pattern')],
    'twocaptcha': [('retry_count', 'Retries on solver error'), ('retry_delay', 'Retry delay (seconds)'),
                   ('auto_submit', 'Submit forms after solving')],
    'onepassword': [('username_ref', 'Username reference'), ('password_ref', 'Password reference'),
                    ('otp_ref', 'Authenticator code reference')],
    'imap': [('senders', 'Allowed senders'), ('recipient', 'Recipient email'), ('subject', 'Subject contains'),
             ('kind', 'Code or link'), ('pattern', 'Code pattern'), ('origins', 'Allowed sign-in link origins')],
    'googlevoice': [('senders', 'Allowed phone numbers'), ('conversation_id', 'Conversation ID'), ('pattern', 'Code pattern')],
    'imessage': [('senders', 'Allowed phone numbers'), ('chat_id', 'Conversation ID'), ('pattern', 'Code pattern')],
}
CONNECTION_FIELDS = {
    'cloaked': [('cdp_url', 'Dedicated signed-in Cloaked browser CDP URL')],
    'twocaptcha': [('api_key', '2Captcha API key')],
    'onepassword': [('vault', 'Vault name or ID'), ('account', '1Password account')],
    'imap': [('host', 'IMAP server'), ('port', 'Port'), ('username', 'Mailbox login'),
             ('password_ref', 'Mailbox password reference'), ('mailbox', 'Mailbox folder'),
             ('authserv_id', 'Mail authentication server ID')],
    'googlevoice': [('command', 'MCP command')],
    'imessage': [('binary', 'Messages command')],
}


def scope_context(key):
    if key == 'shared':
        return {'key': key, 'label': 'Shared defaults', 'chain': ['shared'], 'account': None, 'depth': 0}
    kind, _, identifier = key.partition(':')
    if not identifier.isdecimal():
        raise NotFoundError404()
    if kind == 'persona':
        persona = Persona.query.filter(id=int(identifier)).first()
        if persona:
            return {'key': key, 'label': persona.name, 'chain': config.scope_chain(persona=persona), 'account': None, 'depth': 1}
    elif kind == 'site':
        account = Account.query.filter(id=int(identifier)).join('persona', 'site').first()
        if account:
            return {'key': key, 'label': account.site.domain, 'subtitle': account.username,
                    'chain': config.scope_chain(account), 'account': account, 'depth': 2}
    elif kind == 'check':
        check = Check.query.filter(id=int(identifier)).join('account__persona', 'account__site', 'provider').first()
        if check:
            return {'key': key, 'label': check.name, 'subtitle': check.provider.name,
                    'chain': config.scope_chain(check.account, check), 'account': check.account, 'depth': 3}
    raise NotFoundError404()


class LoginHelp(Base):
    def get(self):
        account = self.request.query_params.get('account', '')
        return RedirectResponse('/integrations' + ('?' + urlencode({'scope': f'site:{account}'}) if account.isdecimal() else ''), status_code=302)

    def post(self):
        return Integrations.post(self)


class Integrations(Base):
    template_name = 'integrations.html'

    def get_template_context(self):
        ctx = super().get_template_context()
        cfg = config.load()
        saved = AppConfig.query.filter(key='recovery_status').first()
        health = dict(saved.value) if saved else {}
        for key in list(health):
            if key.endswith('_tested_at') and health[key]:
                health[key] = datetime.fromisoformat(health[key])
        selected = scope_context(self.request.query_params.get('scope', 'shared'))
        connector = self.request.query_params.get('integration', 'onepassword')
        if connector not in CONNECTORS:
            raise NotFoundError404()
        rows = [scope_context('shared')]
        accounts = list(Account.query.join('persona', 'site').order_by('site__domain'))
        checks = list(Check.query.join('account', 'provider').order_by('name', 'provider__name'))
        for persona in Persona.query.order_by('name'):
            rows.append(scope_context(f'persona:{persona.id}'))
            for account in (a for a in accounts if a.persona.id == persona.id):
                rows.append(scope_context(f'site:{account.id}'))
                rows.extend(scope_context(f'check:{c.id}') for c in checks if c.account.id == account.id)
        for row in rows:
            row['integrations'] = []
            for key, (name, _, _) in CONNECTORS.items():
                effective, origin = config.effective_integration(cfg, key, row['chain'], row['account'])
                parent, _ = config.effective_integration(cfg, key, row['chain'][:-1], row['account'])
                override = cfg.get('integration_scopes', {}).get(row['key'], {}).get(key, {})
                row['integrations'].append({'key': key, 'name': name, 'enabled': effective['enabled'],
                    'mode': str(override['enabled']).lower() if 'enabled' in override else 'inherit',
                    'parent_enabled': parent['enabled'], 'origin': origin, 'configured': len(override) - int('enabled' in override)})
        own = cfg.get('integration_scopes', {}).get(selected['key'], {})
        effective, _ = config.effective_integration(cfg, connector, selected['chain'], selected['account'])
        fields = []
        for key, label in OPTIONS[connector]:
            value, inherited = own.get(connector, {}).get(key, ''), effective.get(key, '')
            if type(value) is bool:
                value = str(value).lower()
            if type(inherited) is bool:
                inherited = str(inherited).lower()
            fields.append({'key': key, 'label': label, 'value': ', '.join(value) if isinstance(value, list) else value,
                           'inherited': ', '.join(inherited) if isinstance(inherited, list) else inherited})
        login = own.get('login', {})
        binding = config.effective_binding(cfg, selected['account']) if selected['account'] else {}
        from .core.providers import ADAPTERS
        from .core.twocaptcha import NATIVE_SOLVERS, SUPPORT, has_key, provider_enabled
        provider_modes = cfg.get('twocaptcha', {}).get('providers', {})
        ctx.update(twocaptcha_key_set=has_key(cfg),
                   twocaptcha_support=[{'kind': k, 'name': ADAPTERS[k].label, 'detail': v,
                       'native': k in NATIVE_SOLVERS, 'enabled': provider_enabled(cfg, k),
                       'mode': str(provider_modes[k]).lower() if k in provider_modes else 'inherit'}
                       for k, v in SUPPORT.items()],
                   nav='integrations', rows=rows, connectors=CONNECTORS, connection_fields=CONNECTION_FIELDS,
                   selected=selected, connector=connector, fields=fields, login=login, binding=binding,
                   setup=self.request.query_params.get('setup') == '1',
                   connection=self.request.query_params.get('connection', ''),
                   cfg=cfg, health=health, error=getattr(self, 'error', ''),
                   saved=self.request.query_params.get('saved') == '1',
                   connection_rows=[{'key': key, 'name': name, 'description': desc, 'icon': icon,
                       'status': health.get(key, 'Not tested'), 'connected': health.get(key) == 'Connected',
                       'fields': [{'key': k, 'label': label, 'value': shlex.join(cfg.get(key, {}).get(k, []))
                                   if k == 'command' else cfg.get(key, {}).get(k, '')} for k, label in CONNECTION_FIELDS[key]]}
                       for key, (name, desc, icon) in CONNECTORS.items()])
        return ctx

    def post(self):
        form = self.request.form_data
        cfg = config.load()
        scope = form.get('scope', 'shared')
        scope_context(scope)
        key = form.get('integration', 'onepassword')
        if key not in CONNECTORS:
            raise NotFoundError404()
        try:
            action = form.get('action')
            if action == 'open_cloaked' and key == 'cloaked':
                from .core.cloaked import open_browser
                open_browser()
            elif action == 'provider':
                from .core.twocaptcha import SUPPORT
                kind, mode = form.get('provider_kind'), form.get('mode')
                if key != 'twocaptcha' or kind not in SUPPORT or kind == 'cdp':
                    raise ValueError('Choose a provider that supports 2Captcha')
                values = cfg.setdefault('twocaptcha', {}).setdefault('providers', {})
                if mode == 'inherit':
                    values.pop(kind, None)
                elif mode in ('true', 'false'):
                    values[kind] = mode == 'true'
                else:
                    raise ValueError('Choose Default, On or Off')
                config.save(cfg)
            elif action == 'toggle':
                values = cfg.setdefault('integration_scopes', {}).setdefault(scope, {}).setdefault(key, {})
                mode = form.get('mode')
                if mode == 'inherit':
                    values.pop('enabled', None)
                elif mode in ('true', 'false'):
                    values['enabled'] = mode == 'true'
                else:
                    raise ValueError('Choose Inherit, On or Off')
                config.save(cfg)
            elif action == 'save_scope':
                target = cfg.setdefault('integration_scopes', {}).setdefault(scope, {})
                options = target.setdefault(key, {})
                for field, _ in OPTIONS[key]:
                    value = form.get(field, '').strip()
                    if not value:
                        options.pop(field, None)
                        continue
                    if key == 'twocaptcha':
                        from .core.twocaptcha import parse_option
                        options[field] = parse_option(field, value)
                        continue
                    if field.endswith('_ref') and not value.startswith('op://'):
                        raise ValueError('Use a 1Password reference, not a password or code')
                    if field in ('senders', 'origins'):
                        value = [v.strip() for v in value.split(',') if v.strip()]
                        if field == 'origins':
                            value = [config.origin(v) for v in value]
                    if field == 'chat_id':
                        value = int(value)
                    if field == 'kind' and value not in ('code', 'link'):
                        raise ValueError('Message type must be code or link')
                    if field == 'pattern':
                        import re
                        try:
                            groups = re.compile(value).groups
                        except re.error as error:
                            raise ValueError('Invalid code pattern') from error
                        if groups != 1:
                            raise ValueError('The code pattern must have one capture group')
                    options[field] = value
                start = form.get('start_url', '').strip()
                origins = [config.origin(v.strip()) for v in form.get('login_origins', '').split(',') if v.strip()]
                if start:
                    origins = list(dict.fromkeys([config.origin(start), *origins]))
                if key != 'twocaptcha':
                    target['login'] = {**({'start_url': start} if start else {}), **({'origins': origins} if origins else {})}
                config.save(cfg)
            elif action == 'connection':
                connection = dict(cfg.get(key, {}))
                for field, _ in CONNECTION_FIELDS[key]:
                    value = form.get(field, '').strip()
                    if key == 'twocaptcha':
                        from .core.twocaptcha import save_key
                        save_key(connection, value, clear=form.get('clear_api_key') == 'true')
                        continue
                    if field == 'password_ref' and value and not value.startswith('op://'):
                        raise ValueError('Use a 1Password reference for the mailbox password')
                    if field == 'port':
                        value = int(value or '993')
                        if not 1 <= value <= 65535:
                            raise ValueError('Choose a valid port')
                    if field == 'command':
                        value = shlex.split(value)
                    connection[field] = value
                if key == 'imap' and connection.get('local_test') and connection.get('host') not in {'127.0.0.1', '::1'}:
                    connection.pop('local_test')
                if key == 'googlevoice':
                    connection['enabled'] = bool(connection.get('command'))
                if key == 'cloaked':
                    from .core.cloaked import validate_connection
                    validate_connection(connection)
                cfg[key] = connection
                config.save(cfg)
                # A changed connection is untested until explicitly tested again.
                row = AppConfig.query.filter(key='recovery_status').first()
                if row:
                    row.value = {**row.value, key: 'Not tested'}
                    row.update(fields=['value'])
            elif action == 'test':
                outcome = sources.status(cfg, key)
                row, _ = AppConfig.query.get_or_create(key='recovery_status', defaults={'value': {}})
                row.value = {**row.value, key: outcome, f'{key}_tested_at': datetime.now(UTC).isoformat()}
                row.update(fields=['value'])
            elif action == 'recover':
                check = Check.query.get(id=int(form['check_id']))
                run = queue(check.account, check.provider.id, self.user.email, check_id=check.id)
                return RedirectResponse(f'/runs/{run.id}', status_code=303)
            else:
                raise ValueError('Unknown integration action')
        except (ValueError, config.Unavailable) as error:
            self.error = str(error)
            return self.get()
        return RedirectResponse('/integrations?' + urlencode({'scope': scope, 'integration': key, 'saved': '1'}), status_code=303)
