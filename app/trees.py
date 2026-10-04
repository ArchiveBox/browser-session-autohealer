"""The same expandable record table, projected from each navigation root."""
from urllib.parse import urlsplit

from plain.http import NotFoundError404

from .core import browser_settings, health, usage
from .core.agent_sessions import sessions_for
from .core.models import (
    Account,
    Check,
    CheckRun,
    CheckRunStep,
    CheckType,
    Persona,
    Provider,
    Run,
    Site,
)
from .views import Base

TITLES = {'runs': 'Browser Sessions', 'personas': 'Personas', 'sites': 'Sites',
          'checks': 'Tasks', 'agents': 'AI Sessions', 'types': 'Task Types', 'providers': 'Browser Providers'}
ICONS = {'runs': '◷', 'personas': '◎', 'sites': '▦', 'checks': '✓', 'agents': '✦', 'types': '⌘', 'providers': '▱'}


def cell(text='', sub='', **kwargs):
    return {'text': text, 'sub': sub, 'favicon': '', 'icon': '', 'badge': False, 'href': '', 'subhref': '', **kwargs}


def table(kind, columns, rows):
    return {'tree_kind': kind, 'tree_title': TITLES.get(kind, kind), 'tree_icon': ICONS.get(kind, '›'),
            'tree_columns': columns, 'tree_rows': rows}


def node(kind, identifier, cells, url=None, **kwargs):
    return {'kind': kind, 'id': identifier, 'cells': cells,
            'url': url or f'/tree/{kind}/{identifier}', 'links': [], 'prompt': '', 'check_id': None, 'opened': False, **kwargs}


def evidence(tiles):
    return cell(images=[{'url': t['image'], 'tone': t['tone'], 'label': t['check']['name'], 'href': t['url'],
                        'domain': urlsplit(t['check']['url']).hostname,
                        'at': health.time_label(t['at']), 'missing': 'expected',
                        'symbol': '!' if t['result'] else '◷'} for t in tiles])


def provider_table(providers):
    # One shared ordering across providers; script revisions aren't new columns.
    checks = list(Check.query.join('account__persona', 'account__site', 'provider')
        .order_by('mode', 'account__site__domain', 'name', 'account__id', 'id'))
    objects = {check.id: check for check in checks}
    groups = health.grouped_checks(health.check_rows(checks))
    columns = [{'key': min(group['member_ids']), 'label': group['check']['name']}
               for group in groups]
    rows = []
    for provider in providers:
        shots = []
        for column, group in zip(columns, groups, strict=True):
            tile = next((t for t in group['provider_results']
                         if t['check']['provider']['id'] == provider.id), None)
            check = objects[(tile or group)['check']['id']]
            expected = bool(tile and (tile['result'] or (
                check.mode == 'check' and check.enabled and provider.enabled)))
            label = f'{check.name} · {check.account.site.domain} · {check.account.persona.name}'
            missing_label = ('No screenshot' if tile and tile['result'] else 'Not run') if expected else (
                'Not configured for this provider' if not tile else
                'Fix not triggered' if check.mode == 'fix' else 'Paused')
            shots.append({**column, 'label': label, 'domain': check.account.site.domain, 'check_id': check.id if tile else None,
                'url': tile['image'] if tile else '', 'href': tile['url'] if tile else '',
                'tone': tile['tone'] if tile and tile['result'] else 'attention' if expected else 'neutral',
                'missing': 'expected' if expected else 'irrelevant',
                'missing_label': missing_label,
                'symbol': '!' if expected and tile['result'] and tile['result'].status != 'running'
                          else '◷' if expected else '—'})
        rows.append(node('providers', provider.id, [
            cell(provider.name, color=provider.display_color, icon='▱', href=f'/edit/provider?id={provider.id}'),
            cell(provider.kind), cell('Enabled' if provider.enabled else 'Disabled', badge=True,
                                     tone='success' if provider.enabled else 'neutral'),
            cell(Run.query.filter(provider=provider, status__in=health.ACTIVE).count()),
            cell(Run.query.filter(provider=provider).count()), cell(images=shots, aligned=True)],
            links=[('Settings', f'/edit/provider?id={provider.id}')]))
    return {**table('providers', ['Provider', 'Runtime', 'Status', 'Active', 'Sessions', 'Last results'], rows),
            'tree_result_columns': columns}


def tokens(value):
    return cell(usage=usage.display(value))


def type_evidence(revisions):
    """Real executions of this exact source, including earlier metadata revisions."""
    revisions = list(revisions)
    wanted = {(r.check_id, r.digest) for r in revisions}
    found = {}
    steps = CheckRunStep.query.filter(check_type__digest__in=[r.digest for r in revisions],
        check_run__status__in=['success', 'failure']).join('check_type', 'check_run__run').order_by('-check_run__ended_at', '-id')
    for step in steps:
        key = (step.check_type.check_id, step.check_type.digest)
        if key not in wanted or key in found:
            continue
        result = step.check_run
        image = health.result_image(result)[0]
        if image:
            found[key] = {'url': image, 'tone': 'success' if result.passed else 'failed',
                'domain': next((p['domain'] for p in result.run.plan if p['id'] == result.check_id), ''),
                'label': f'Session #{result.run.id}', 'href': f'/runs/{result.run.id}/checks/{result.check_id}',
                'at': health.time_label(result.ended_at), 'revision': step.check_type.id}
    return {r.id: found.get((r.check_id, r.digest)) for r in revisions}


def check_table(checks, assignments=False):
    checks = list(checks)
    objects = {c.id: c for c in checks}
    totals = usage.grouped('check', objects)
    tiles = health.check_rows(checks)
    if not assignments:
        tiles = health.grouped_checks(tiles)
    rows = []
    for tile in tiles:
        c = objects[tile['check']['id']]
        providers = tile.get('provider_results', [tile])
        passed = sum(p['tone'] == 'success' for p in providers)
        status = tile['label'] if len(providers) == 1 else f'{passed}/{len(providers)} passed'
        status_tone = 'success' if passed == len(providers) else 'attention'
        if c.mode == 'fix':
            tested = [p for p in providers if p['at']]
            status = 'Available' if not tested else f'{passed}/{len(tested)} passed'
            status_tone = 'neutral' if not tested else 'success' if passed == len(tested) else 'attention'
        rows.append(node('executions' if assignments else 'checks', c.id, [
            cell(c.name, c.account.site.domain + (' · Fix' if c.mode == 'fix' else ' · Check'), icon='⚒' if c.mode == 'fix' else '✓'), cell(c.account.persona.name, c.account.site.domain, href=f'/edit/persona?id={c.account.persona.id}', subhref=f'/edit/site?id={c.account.site.id}'),
            cell(c.provider.name if assignments else ' · '.join(p['check']['provider']['name'] for p in providers), links=[(p['check']['provider']['name'], f"/edit/provider?id={p['check']['provider']['id']}") for p in providers]),
            cell(status, badge=True, tone=status_tone),
            cell(health.time_label(tile['at'])), tokens(usage.total([totals.get(p['check']['id']) for p in providers])), evidence([tile])],
            links=[('Settings', f'/edit/check?id={c.id}'), ('Integrations', f'/integrations?scope=check:{c.id}')],
            check_id=c.id if assignments and c.enabled and c.provider.enabled else None,
            prompt=c.instruction))
    return table('checks', ['Task / domain', 'Persona / site', 'Provider', 'Status', 'Checked', 'Tokens', 'Screenshot'], rows)


def account_table(accounts, persona_first=False):
    accounts = list(accounts)
    totals = usage.grouped('account', [(a.persona.id, a.site.domain) for a in accounts])
    checks = list(Check.query.filter(account__id__in=[a.id for a in accounts], mode='check').join('account', 'provider'))
    tiles = health.grouped_checks(health.check_rows(checks))
    rows = []
    for a in accounts:
        related = [t for t in tiles if t['check']['account_id'] == a.id]
        counts = health.check_counts(related)
        rows.append(node('accounts', a.id, [
            cell(a.persona.name if persona_first else a.site.domain, a.username,
                 icon='◎' if persona_first else '▦', favicon='' if persona_first else a.site.domain, href=f'/edit/persona?id={a.persona.id}' if persona_first else f'/edit/site?id={a.site.id}'),
            cell(a.site.domain if persona_first else a.persona.name, href=f'/edit/site?id={a.site.id}' if persona_first else f'/edit/persona?id={a.persona.id}'),
            cell(f"{counts['passed']}/{counts['total']}", 'Passed', badge=True,
                 tone='success' if counts['total'] and counts['passed'] == counts['total'] else 'attention' if counts['failed'] else 'neutral'),
            cell(len(related), 'Checks'), tokens(totals.get((a.persona.id, a.site.domain))), evidence(related)],
            links=[('Settings', f'/edit/account?id={a.id}'), ('Integrations', f'/integrations?scope=site:{a.id}')]))
    return table('personas' if persona_first else 'sites',
                 ['Persona' if persona_first else 'Site', 'Site' if persona_first else 'Persona', 'Status', 'Checks', 'Tokens', 'Screenshots'], rows)


def session_table(runs, selected='', check=''):
    rows = []
    for session in health.session_rows(runs):
        r = session['run']
        rows.append(node('runs', r.id, [cell(r.persona.name, f'#{r.id}', icon='◷', href=f'/edit/persona?id={r.persona.id}'),
            cell(r.provider.name, color=r.provider.display_color, href=f'/edit/provider?id={r.provider.id}'), cell(session['source']),
            cell(session['disposition'], badge=True, tone=session['tone']),
            cell(health.time_label(r.started_at) if r.started_at else 'Queued'),
            cell(health.time_label(r.finished_at) if r.finished_at else '—', session['duration']),
            tokens(usage.for_run(r)),
            cell(images=[{'url': c['image'], 'tone': 'success' if c['result'].passed else 'failed',
                          'label': c['domain'], 'domain': c['domain'], 'href': c['url'], 'at': health.time_label(c['result'].ended_at)} for c in session['checks'] if c['image']])],
            url=f'/runs/{r.id}/tree' + (f'?check={check}' if str(r.id) == selected and check else ''),
            opened=str(r.id) == selected))
    return table('runs', ['Session / persona', 'Provider', 'Started by', 'Changes', 'Started', 'Ended', 'Tokens', 'Screenshots'], rows)


def executions_table(checks, observations=None):
    checks = list(checks)
    names = {c.id: c.name for c in checks}
    rows = []
    observations = observations if observations is not None else CheckRun.query.filter(check_id__in=list(names)).join('run__provider', 'run__persona').order_by('-created_at')[:100]
    for o in observations:
        image = health.result_image(o)[0]
        label, tone = health.LABELS.get(o.state, health.LABELS['needs_review'])
        rows.append(node('agents', f'{o.run.id}-{o.check_id}', [
            cell(f'#{o.run.id}', names[o.check_id], icon='◷'), cell(o.run.provider.name, href=f'/edit/provider?id={o.run.provider.id}'),
            cell(label, badge=True, tone=tone), cell(health.time_label(o.started_at)),
            cell(health.time_label(o.ended_at)),
            tokens(usage.for_run(o.run, o.check_id)),
            cell(images=[{'url': image, 'tone': tone, 'label': names[o.check_id],
                         'domain': next((p['domain'] for p in o.run.plan if p['id'] == o.check_id), ''),
                         'href': f'/runs/{o.run.id}/checks/{o.check_id}', 'at': health.time_label(o.ended_at)}] if image else [])],
            url=f'/runs/{o.run.id}/checks/{o.check_id}/activity'))
    return table('runs', ['Session', 'Provider', 'Status', 'Started', 'Ended', 'Tokens', 'Screenshot'], rows)


def root_table(kind, params):
    try:
        page = max(1, int(params.get('page', '1')))
    except ValueError:
        page = 1
    offset = (page - 1) * 100
    if kind == 'runs':
        query = Run.query.join('persona', 'provider').order_by('-created_at')
        result = session_table(query[offset:offset+100], params.get('run', ''), params.get('check', ''))
    elif kind == 'personas':
        query = Persona.query.order_by('name')
        personas = list(query[offset:offset+100])
        totals = usage.grouped('persona', [p.id for p in personas])
        accounts = list(Account.query.filter(persona__id__in=[p.id for p in personas]).join('site'))
        checks = list(Check.query.filter(account__persona__id__in=[p.id for p in personas], mode='check').join('account__persona', 'provider'))
        tiles = health.grouped_checks(health.check_rows(checks))
        rows = []
        for p in personas:
            ids = {a.id for a in accounts if a.persona.id == p.id}
            related = [t for t in tiles if t['check']['account_id'] in ids]
            counts = health.check_counts(related)
            rows.append(node('personas', p.id, [cell(p.name, icon='◎', href=f'/edit/persona?id={p.id}'), cell(len(ids), 'Sites'),
                cell(f"{counts['passed']}/{counts['total']}", 'Passed', badge=True,
                     tone='success' if counts['total'] and counts['passed'] == counts['total'] else 'attention' if counts['failed'] else 'neutral'),
                cell(' · '.join(str(p.config[k]) for k in ('locale', 'timezone', 'platform') if p.config.get(k))), tokens(totals.get(p.id)), evidence(related)],
                links=[('Lineage', f'/personas/{p.id}/lineage'), ('Settings', f'/personas/{p.id}'), ('Integrations', f'/integrations?scope=persona:{p.id}')]))
        result = table(kind, ['Persona', 'Sites', 'Status', 'Settings', 'Tokens', 'Screenshots'], rows)
    elif kind == 'sites':
        query = Site.query.order_by('domain')
        sites = list(query[offset:offset+100])
        totals = usage.grouped('site', [s.domain for s in sites])
        accounts = list(Account.query.filter(site__id__in=[s.id for s in sites]))
        rows = []
        for s in sites:
            checks = health.grouped_checks(health.check_rows(Check.query.filter(account__site=s, mode='check').join('account', 'provider')))
            rows.append(node('sites', s.id, [cell(s.domain, favicon=s.domain, href=f'/edit/site?id={s.id}'),
                cell(sum(a.site.id == s.id for a in accounts)), cell(len(checks)), tokens(totals.get(s.domain)), evidence(checks)],
                links=[('Settings', f'/edit/site?id={s.id}')]))
        result = table(kind, ['Site', 'Personas', 'Checks', 'Tokens', 'Screenshots'], rows)
    elif kind == 'checks':
        query = Check.query.join('account__persona', 'account__site', 'provider').order_by('name')
        if params.get('mode') in {'check', 'fix'}:
            query = query.filter(mode=params['mode'])
        result = check_table(query[offset:offset+100])
        for row in result['tree_rows']:
            row['opened'] = str(row['id']) == params.get('record')
    elif kind == 'providers':
        query = Provider.query.order_by('name')
        result = provider_table(query[offset:offset+100])
    elif kind == 'types':
        previous_ids = list(CheckType.query.exclude(previous=None).values_list('previous', flat=True))
        query = CheckType.query.exclude(id__in=previous_ids).order_by('-created_at')
        if params.get('domain'):
            from .core.domain_patterns import matches
            matching = [r['id'] for r in list(query.values('id', 'domain')) if matches(r['domain'], params['domain'])]
            query = query.filter(id__in=matching)
        revisions = list(query[offset:offset+100])
        shots = type_evidence(revisions)
        check_names = dict(Check.query.filter(id__in=[c.check_id for c in revisions if c.check_id]).values_list('id', 'name'))
        result = table(kind, ['Type / domains', 'Language', 'Check', 'Author', 'Created', 'Last result'], [
            node(kind, c.id, [cell(c.name, c.domain, icon='⌘'), cell(c.lang),
                             cell(check_names.get(c.check_id, '—'), href=f'/edit/check?id={c.check_id}' if c.check_id else ''), cell(c.author),
                             cell(health.time_label(c.created_at)), cell(images=[shots[c.id]] if shots[c.id] else [], empty='Not run')],
                             links=[('Edit', f'/edit/check-type?id={c.id}')], opened=str(c.id)==params.get('record')) for c in revisions])
    elif kind == 'agents':
        query = Run.query.join('persona', 'provider').order_by('-created_at')
        rows = []
        for run in query[offset:offset+100]:
            for agent in reversed(sessions_for(run)):
                o = agent['observation']
                image = health.result_image(o)[0] if o else ''
                rows.append(node(kind, agent['session_id'], [cell(agent['title'], f'#{run.id}', icon='✦'),
                    cell(run.persona.name, run.provider.name, href=f'/edit/persona?id={run.persona.id}', subhref=f'/edit/provider?id={run.provider.id}'), cell(agent['status_label'], badge=True, tone='neutral'),
                    cell(agent['model']), cell(health.time_label(o.started_at if o else agent['created_at'])),
                    cell(health.time_label(o.ended_at) if o else '—'),
                    tokens(usage.for_run(run, agent['check_id'])),
                    cell(images=[{'url': image, 'tone': 'success' if o.passed else 'failed', 'label': agent['title'],
                                 'domain': next((p['domain'] for p in run.plan if p['id'] == o.check_id), ''),
                                 'href': f'/runs/{run.id}/checks/{o.check_id}', 'at': health.time_label(o.ended_at)}] if image else [])],
                    url=f"/runs/{run.id}/checks/{agent['check_id']}/activity"))
        result = table(kind, ['AI session', 'Persona / provider', 'Status', 'Model', 'Started', 'Ended', 'Tokens', 'Screenshot'], rows)
    else:
        raise NotFoundError404()
    return {**result, 'page': page, 'has_next': query.count() > offset + 100}


class RelatedRecords(Base):
    template_name = 'tree_rows.html'

    def get_template_context(self):
        ctx = super().get_template_context()
        detail = {}
        kind, identifier = self.url_kwargs['kind'], self.url_kwargs['id']
        if kind == 'personas':
            persona = Persona.query.get(id=identifier)
            detail = {'detail_title': persona.name, 'detail_settings': browser_settings.inventory(persona.config),
                      'detail_actions': [('Lineage', f'/personas/{persona.id}/lineage'), ('Edit', f'/edit/persona?id={persona.id}'), ('＋ Site', f'/edit/account?persona={persona.id}')],
                      'detail_fields': [{'label': 'Name', 'value': persona.name}, {'label': 'Created', 'value': health.time_label(persona.created_at)},
                                        {'label': 'Description', 'value': persona.description or '—'}]}
            result = account_table(Account.query.filter(persona__id=identifier).join('persona', 'site'))
        elif kind == 'sites':
            site = Site.query.get(id=identifier)
            detail = {'detail_title': site.domain, 'detail_actions': [('Edit', f'/edit/site?id={site.id}'), ('＋ Account', f'/edit/account?domain={site.domain}')],
                      'detail_fields': [{'label': 'Domain', 'value': site.domain}]}
            result = account_table(Account.query.filter(site__id=identifier).join('persona', 'site'), persona_first=True)
        elif kind == 'accounts':
            account = Account.query.get(id=identifier)
            detail = {'detail_title': account.site.domain, 'detail_actions': [('Edit', f'/edit/account?id={account.id}'), ('＋ Check', f'/edit/check?account={account.id}')],
                      'detail_fields': [{'label': 'Persona', 'value': account.persona.name, 'href': f'/edit/persona?id={account.persona.id}'},
                                        {'label': 'Site', 'value': account.site.domain, 'href': f'/edit/site?id={account.site.id}'},
                                        {'label': 'Username', 'value': account.username}]}
            result = check_table(Check.query.filter(account__id=identifier).join('account__persona', 'account__site', 'provider'))
        elif kind in {'checks', 'executions'}:
            check = Check.query.get(id=identifier)
            detail = {'detail_title': check.name, 'detail_actions': [('Edit', f'/edit/check?id={check.id}'), ('▶ Run', f'/edit/session?check={check.id}')],
                      'detail_fields': [{'label': 'Persona', 'value': check.account.persona.name, 'href': f'/edit/persona?id={check.account.persona.id}'},
                                        {'label': 'Site', 'value': check.account.site.domain, 'href': f'/edit/site?id={check.account.site.id}'},
                                        {'label': 'Provider', 'value': check.provider.name, 'href': f'/edit/provider?id={check.provider.id}'},
                                        {'label': 'Mode', 'value': 'Fix · session changes' if check.mode == 'fix' else 'Check · read only'}, {'label': 'Pattern', 'value': check.pattern or 'Custom'}, {'label': 'URL', 'value': check.url}, {'label': 'Interval', 'value': str(check.interval_seconds) + 's'},
                                        {'label': 'Next', 'value': health.time_label(check.next_due)},
                                        {'label': 'Status', 'value': 'Enabled' if check.enabled else 'Paused'},
                                        {'label': 'Prompt', 'value': check.instruction, 'wide': True}]}
            if kind == 'checks':
                related = Check.query.filter(mode=check.mode, account=check.account, name=check.name, url=check.url, instruction=check.instruction).join('account__persona', 'account__site', 'provider')
                result = check_table(related, assignments=True)
            else:
                result = executions_table([check])
        elif kind == 'providers':
            provider = Provider.query.get(id=identifier)
            detail = {'detail_title': provider.name, 'detail_actions': [('Edit', f'/edit/provider?id={provider.id}'), ('＋ Session', '/edit/session')],
                      'detail_fields': [{'label': 'Runtime', 'value': provider.kind}, {'label': 'Status', 'value': 'Enabled' if provider.enabled else 'Disabled'},
                                        *[{'label': key, 'value': value} for key, value in provider.config.items()]]}
            result = session_table(Run.query.filter(provider__id=identifier).join('persona', 'provider').order_by('-created_at')[:100])
        elif kind == 'types':
            c = CheckType.query.get(id=identifier)
            steps = list(CheckRunStep.query.filter(check_type=c).join('check_run__run'))
            result = executions_table(Check.query.filter(id=c.check_id).join('account', 'provider'),
                CheckRun.query.filter(id__in=[step.check_run.id for step in steps]).join('run__provider', 'run__persona'))
            revisions = []
            previous = c.previous
            while previous and len(revisions) < 100:
                revisions.append(previous)
                previous = previous.previous
            result.update(revision=c, revisions=revisions, source_evidence=type_evidence([c])[c.id], linked_check=Check.query.filter(id=c.check_id).first())
        else:
            raise NotFoundError404()
        return {**ctx, **detail, **result}
