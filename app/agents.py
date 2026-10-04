"""Session-scoped evidence and the existing embedded OpenCode conversation."""
from urllib.parse import urlencode, urlsplit

from plain.http import NotFoundError404, RedirectResponse

from .core import health, storage, usage
from .core.agent_sessions import sessions_for
from .core.models import Account, CheckRun, Run
from .core.opencode_proxy import _PROXY_PREFIX
from .views import Base, browser_steps


class AgentSessionsView(Base):
    def get(self):
        params = {'view': 'runs' if self.request.query_params.get('run') else 'agents'}
        for key in ('run', 'check'):
            value = self.request.query_params.get(key)
            if value and (value.isdecimal() or key == 'check' and value == 'recovery'):
                params[key] = value
        return RedirectResponse('/?' + urlencode(params), status_code=302)


def storage_count(origins, key):
    # Missing capture is unknown, never a zero-count assertion.
    values = [item.get(key) for item in origins]
    if not values or any(not isinstance(value, (list, dict)) for value in values):
        return None
    return sum(len(value) for value in values)


class SessionTreeView(Base):
    template_name = 'session_tree.html'

    def get_template_context(self):
        ctx = super().get_template_context()
        run = Run.query.get(id=self.url_kwargs['id'])
        digest = run.tip or run.base.digest
        state = storage.read_state(digest)
        settings = run.runtime.get('settings', state.get('settings', {}))
        domains = {p['domain'] for p in run.plan}
        if run.runtime.get('recovery'):
            domains.add(run.scope)
        def domain_for(host):
            host = host.lstrip('.').lower()
            return next((d for d in sorted(domains, key=len, reverse=True)
                         if host == d or host.endswith('.' + d)), host)
        sites = {domain: {'domain': domain, 'cookies': 0, 'origins': [], 'checks': []}
                 for domain in sorted(domains)}
        for account in Account.query.filter(persona=run.persona, site__domain__in=list(domains)).join('site'):
            sites[account.site.domain]['edit_url'] = f'/edit/site?id={account.site.id}'
        for cookie in state.get('cookies', []):
            domain = domain_for(cookie.get('domain', ''))
            if domain in sites:
                sites[domain]['cookies'] += 1
        for item in state.get('origins', []):
            domain = domain_for(urlsplit(item.get('origin', '')).hostname or '')
            if domain in sites:
                sites[domain]['origins'].append(item)
        observations = {str(o.check_id): o for o in CheckRun.query.filter(run=run).order_by('created_at')}
        sessions = {s['check_id']: s for s in sessions_for(run, observations.values())}
        plans = list(run.plan)
        if run.runtime.get('recovery') and not any(p.get('mode') == 'fix' for p in plans):
            plans.append({'id': 'recovery', 'domain': run.scope, 'name': 'Restore sign-in',
                          'instruction': 'Restore access using the configured sign-in details.'})
        selected = self.request.query_params.get('check', '')
        for plan in plans:
            identifier = str(plan['id'])
            result = observations.get(identifier)
            agent = sessions.get(identifier)
            sites[plan['domain']]['checks'].append({
                'id': identifier, 'name': plan['name'], 'prompt': plan['instruction'],
                'result': result, 'agent': agent,
                'model': (agent or {}).get('model', run.runtime.get('inference', {}).get('model', '')),
                'started': result.started_at if result else (agent or {}).get('created_at'),
                'ended': result.ended_at if result else None,
                'label': health.LABELS.get(result.state, ('Needs review',))[0] if result else (agent or {}).get('status_label', 'Not started'),
                'tone': 'success' if result and result.passed else 'failed' if result else 'neutral',
                'selected': identifier == selected,
                'usage': usage.for_run(run, identifier),
            })
        for site in sites.values():
            for key, field in [('local', 'localStorage'), ('session', 'sessionStorage'),
                               ('indexed', 'indexedDB'), ('opfs', 'opfs')]:
                site[key] = storage_count(site['origins'], field)
            site['selected'] = any(check['selected'] for check in site['checks'])
            site['usage'] = usage.display(usage.total([check['usage'] for check in site['checks']]))
            for check in site['checks']:
                check['usage'] = usage.display(check['usage'])
        viewport = settings.get('viewport', {})
        summary = [settings.get('locale'), settings.get('timezone'), settings.get('platform')]
        if viewport.get('width') and viewport.get('height'):
            summary.append(f"{viewport['width']} × {viewport['height']}")
        ctx.update(run=run, session_usage=usage.display(usage.for_run(run)), sites=sorted(sites.values(), key=lambda s: (not bool(s['checks']), s['domain'])),
                   settings_summary=' · '.join(str(v) for v in summary if v) or 'No settings recorded',
                   state_path=str(storage.object_path(digest) / 'state.enc'),
                   state_label='Returned browser data' if run.tip else 'Starting browser data',
                   selected_check=selected, persona_site_count=Account.query.filter(persona=run.persona).count(),
                   cookie_count=len(state.get('cookies', [])), setting_count=len(settings))
        return ctx


class CheckActivityView(Base):
    template_name = 'check_activity.html'

    def get_template_context(self):
        ctx = super().get_template_context()
        run = Run.query.get(id=self.url_kwargs['id'])
        identifier = str(self.url_kwargs['check_id'])
        if not any(str(plan['id']) == identifier for plan in run.plan) and not (
            identifier == 'recovery' and run.runtime.get('recovery')
        ):
            raise NotFoundError404()
        result = CheckRun.query.filter(run=run, check_id=int(identifier)).order_by('-created_at').first() if identifier.isdecimal() else None
        plan = next((p for p in run.plan if str(p['id']) == identifier), {})
        agent = next((s for s in sessions_for(run) if s['check_id'] == identifier), None)
        frames = [{'url': f"/evidence/{run.id}/{step['screenshot']}", 'label': f'Browser step {i + 1}'}
                  for i, step in enumerate(browser_steps(run, identifier))]
        if result:
            image = health.result_image(result)[0]
            if image:
                frames.append({'url': image, 'label': 'Final result · ' + health.time_label(result.ended_at or result.created_at)})
        ctx.update(run=run, check_id=identifier, plan=plan, result=result, frames=frames, agent=agent,
                   workdir=agent['directory'] if agent else '',
                   recent_session_id=agent['session_id'] if agent else '', proxy_prefix=_PROXY_PREFIX)
        return ctx


class ConversationView(CheckActivityView):
    template_name = 'conversation.html'
