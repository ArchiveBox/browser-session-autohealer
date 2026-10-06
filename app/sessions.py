"""Plain UI for the same session requests accepted by the HTTP API."""
import json
from copy import deepcopy
from hashlib import sha256
from uuid import UUID

from plain import forms
from plain.http import JsonResponse, NotFoundError404, RedirectResponse, Response
from plain.templates.views import FormView

from .core import health, network
from .core import session_broker as broker
from .core.models import Check, CheckRun, Persona, Provider, SessionRequest
from .core.providers import adapter
from .core.session_conditions import candidates, validate
from .views import Base


class SessionRequestForm(forms.Form):
    document = forms.JSONField()
    key = forms.TextField(max_length=200)

    def clean_document(self):
        try:
            return validate(self.cleaned_data['document'])
        except (ValueError, TypeError, KeyError) as exc:
            raise forms.ValidationError(str(exc)) from None


def availability(rows):
    """Present recorded condition evidence, without exposing internal JSON pointers."""
    labels = {'stale': 'Check too old', 'failed': 'Check failed', 'pending': 'Not checked yet',
        'changed': 'Browser or check changed', 'no_checks': 'Checks not configured',
        'not_enough_checks': 'Not enough configured checks', 'not_enough_passing_checks': 'Not enough passing checks',
        'geolocation_unavailable': 'IP location lookup unavailable', 'unknown_location': 'IP location unknown',
        'unknown_ip': 'IP address unknown', 'not_matched': 'Does not match'}
    result_ids = {c['result_id'] for row in rows for issue in row['unmet'] for c in issue.get('checks', []) if c.get('result_id')}
    results = {r.id: r for r in CheckRun.query.filter(id__in=result_ids).join('run')}
    for row in rows:
        for issue in row['unmet']:
            condition = issue.get('condition', {})
            issue['label'] = condition.get('site', 'IP' if condition.get('type') == 'ip' else 'Browser') if isinstance(condition, dict) else 'Checks'
            issue['message'] = labels.get(issue['reason'], issue['reason'])
            issue['value'] = ' · '.join(str(condition[k]) for k in ('country', 'state', 'city', 'ip') if k in condition) if isinstance(condition, dict) else ''
            for check in issue.get('checks', []):
                result = results.get(check.get('result_id'))
                if result:
                    check.update(image=health.result_image(result)[0], url=f'/runs/{result.run.id}/checks/{result.check_id}',
                                 at=health.time_label(result.ended_at), age_limit=f"{check['max_age'] / 60:g} min")
    return rows


class SessionPreview(Base):
    template_name = 'session_availability.html'

    def get(self):
        return Response(status_code=405)

    def post(self):
        try:
            self.spec = validate(self.request.json_data)
        except (ValueError, TypeError, KeyError) as exc:
            return JsonResponse({'error': str(exc)}, status_code=400)
        response = super().get()
        response.headers['Cache-Control'] = 'no-store'
        return response

    def get_template_context(self):
        rows = broker.diagnostics(candidates(self.spec))
        return {**super().get_template_context(), 'availability': availability(rows),
                'recheck': self.spec['recheck'], 'geo_error': network.database_error()}


class SessionEditor(Base, FormView):
    template_name = 'session_builder.html'
    form_class = SessionRequestForm

    def get_template_context(self):
        from uuid import uuid4
        ctx = super().get_template_context()
        providers = []
        for p in Provider.query.filter(enabled=True).order_by('name'):
            runtime = adapter(p)
            try:
                runtime.validate_handoff(p.config)
                unavailable = ''
            except ValueError as exc:
                unavailable = str(exc)
            providers.append({'id': str(p.uid), 'name': p.name, 'unavailable': unavailable})
        document = self.request.form_data.get('document') if self.request.method == 'POST' else None
        source = None
        if self.request.method == 'GET' and self.request.query_params.get('request'):
            try:
                source = SessionRequest.query.filter(uid=UUID(self.request.query_params['request'])).first()
            except ValueError:
                raise NotFoundError404() from None
            if source is None:
                raise NotFoundError404()
        try:
            initial = json.loads(document) if document else deepcopy(source.spec) if source else {'timeout': 60, 'lifetime': 1800, 'actor': 'UI'}
            if source and self.request.query_params.get('recheck') == '1':
                initial.update(recheck=True, allow_unhealthy=False, timeout=initial['timeout'] or 60)
            initial = validate(initial)
        except (ValueError, TypeError, KeyError):
            initial = {'require_all': [], 'prefer': []}
        checks = list(Check.query.filter(enabled=True, mode='check').join('account__site', 'account__persona', 'provider'))
        latest = {r.check_id: r for r in CheckRun.query.filter(check_id__in=[c.id for c in checks])
                  .join('run').order_by('check_id', '-created_at', '-id').distinct('check_id')}
        tasks = []
        for check in checks:
            result = latest.get(check.id)
            tasks.append({'name': check.name, 'site': check.account.site.domain,
                'persona': str(check.account.persona.uid), 'provider': str(check.provider.uid),
                'image': health.result_image(result)[0] if result else '',
                'result_url': f'/runs/{result.run.id}/checks/{check.id}' if result else '',
                'status': result.status if result else '',
                'at': result.ended_at.isoformat() if result and result.ended_at else ''})
        return {**ctx, 'nav': 'runs', 'builder': {
            'personas': [{'id': str(p.uid), 'name': p.name} for p in Persona.query.order_by('name')],
            'providers': providers,
            'tasks': tasks,
            'initial': initial}, 'request_key': self.request.form_data.get('key') or str(uuid4())}

    def form_valid(self, form):
        try:
            row = broker.create(form.cleaned_data['document'], form.cleaned_data['key'])
        except ValueError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        return RedirectResponse(f'/sessions/{row.uid}', status_code=303)


class SessionView(Base):
    template_name = 'session_request.html'

    def row(self):
        try:
            uid = UUID(self.url_kwargs['uid'])
        except ValueError:
            raise NotFoundError404() from None
        row = SessionRequest.query.filter(uid=uid).first()
        if row is None:
            raise NotFoundError404()
        return broker.expire(row)

    def get_template_context(self):
        row = self.row()
        state = broker.state(row)
        self.state_version = sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:16]
        evidence = []
        if row.run:
            for task in row.run.plan:
                query = CheckRun.query.filter(check_id=task['id']).order_by('-created_at', '-id')
                latest = query.filter(run=row.run).first() or query.filter(ended_at__lte=row.run.started_at).first()
                if latest:
                    evidence.append({'name': task['name'], 'image': health.result_image(latest)[0],
                        'url': f'/runs/{latest.run.id}/checks/{latest.check_id}', 'status': latest.status,
                        'at': latest.ended_at, 'cached': latest.run.id != row.run.id})
        labels = {'queued': 'Waiting', 'preparing': 'Preparing', 'ready': 'Ready', 'releasing': 'Releasing',
                  'finalizing': 'Checking in', 'closed': 'Closed', 'failed': 'Unavailable', 'cancelled': 'Cancelled'}
        rows = broker.diagnostics(candidates(row.spec)) if not row.run else [{
            'persona': row.run.persona.name, 'provider': row.run.provider.name,
            'eligible': False, 'unmet': deepcopy(row.detail.get('unmet', []))}] if row.detail.get('unmet') else []
        return {**super().get_template_context(), 'nav': 'runs', 'session_request': row, 'connection': state, 'evidence': evidence,
                'availability': availability(rows), 'recheck': row.spec['recheck'], 'geo_error': network.database_error(),
                'state_version': self.state_version,
                'ips': [network.label(ip['ip'], ip['geo']) for ip in state['ips']],
                'status_label': labels[row.status], 'pending': row.status not in broker.TERMINAL,
                'tone': 'success' if row.status == 'ready' else 'failed' if row.status == 'failed' else 'neutral'}

    def get(self):
        response = super().get()
        response.headers['Cache-Control'] = 'no-store'
        return response

    def post(self):
        row = self.row()
        result = self.request.form_data.get('result')
        broker.release(row.id, success={'success': True, 'failure': False}.get(result),
                       message=self.request.form_data.get('message', ''))
        return RedirectResponse(f'/sessions/{row.uid}', status_code=303)


class SessionStatus(SessionView):
    template_name = 'session_status.html'

    def get(self):
        response = super().get()
        if self.request.query_params.get('version') == self.state_version:
            return Response(status_code=204, headers={'Cache-Control': 'no-store'})
        return response
