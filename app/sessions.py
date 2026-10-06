"""Plain UI for the same session requests accepted by the HTTP API."""
import json
from hashlib import sha256
from uuid import UUID

from plain import forms
from plain.http import NotFoundError404, RedirectResponse, Response
from plain.templates.views import FormView

from .core import health, network
from .core import session_broker as broker
from .core.models import Check, CheckRun, Persona, Provider, SessionRequest
from .core.providers import adapter
from .core.session_conditions import validate
from .views import Base


class SessionRequestForm(forms.Form):
    document = forms.JSONField()
    key = forms.TextField(max_length=200)

    def clean_document(self):
        try:
            return validate(self.cleaned_data['document'])
        except (ValueError, TypeError, KeyError) as exc:
            raise forms.ValidationError(str(exc)) from None


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
            providers.append({'id': str(p.uid), 'name': p.name, 'fields': runtime.network_fields,
                              'unavailable': unavailable, 'config': p.config})
        document = self.request.form_data.get('document') if self.request.method == 'POST' else None
        try:
            initial = json.loads(document) if document else {'require_all': [], 'prefer': [], 'timeout': 60, 'lifetime': 1800}
            validate(initial)
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
        return {**super().get_template_context(), 'nav': 'runs', 'session_request': row, 'connection': state, 'evidence': evidence,
                'state_version': self.state_version,
                'ips': [network.label(ip['ip'], ip['geo']) for ip in state['ips']],
                'status_label': labels[row.status], 'pending': row.status not in broker.TERMINAL,
                'tone': 'success' if row.status == 'ready' else 'failed' if row.status == 'failed' else 'neutral',
                'personas_by_uid': {str(p.uid): p.name for p in Persona.query.all()},
                'providers_by_uid': {str(p.uid): p.name for p in Provider.query.all()}}

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
