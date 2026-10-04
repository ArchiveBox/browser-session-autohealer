"""Plain task forms shared by read-only checks and session-changing fixes."""
from functools import cached_property
from urllib.parse import urlsplit

from plain import forms
from plain.http import NotFoundError404, RedirectResponse
from plain.postgres import transaction
from plain.postgres.forms import ModelChoiceField
from plain.templates.views import FormView

from .core import services
from .core.models import Account, Check, Provider, TaskRule
from .views import Base


class TaskForm(forms.Form):
    name = forms.TextField(max_length=160)
    mode = forms.ChoiceField(choices=[('check', 'Check · read only'), ('fix', 'Fix · may change session')])
    account = ModelChoiceField(queryset=Account.query.join('persona', 'site'))
    provider = ModelChoiceField(queryset=Provider.query.all())
    url = forms.TextField()
    instruction = forms.TextField()
    interval_seconds = forms.IntegerField(min_value=60)
    enabled = forms.BooleanField(required=False)

    def clean_url(self):
        value = self.cleaned_data['url']
        if urlsplit(value).scheme not in {'http', 'https'} or not urlsplit(value).hostname:
            raise forms.ValidationError('Use an HTTP(S) page URL')
        return value

    def clean_instruction(self):
        return self.cleaned_data['instruction'].replace('\r\n', '\n').replace('\r', '\n')


class TaskEditor(Base, FormView):
    form_class = TaskForm
    template_name = 'task_editor.html'

    @cached_property
    def task(self):
        identifier = self.request.query_params.get('id')
        if not identifier:
            return None
        if not identifier.isdecimal():
            raise NotFoundError404()
        return Check.query.get(id=int(identifier))

    def get_form_kwargs(self):
        task = self.task
        initial = {k: getattr(task, k) for k in ('name', 'mode', 'url', 'instruction', 'interval_seconds', 'enabled')} if task else {
            'mode': self.request.query_params.get('mode', 'check'), 'interval_seconds': 3600, 'enabled': True}
        initial.update(account=task.account.id if task else self.request.query_params.get('account', ''), provider=task.provider.id if task else '')
        return {**super().get_form_kwargs(), 'initial': initial}

    def get_template_context(self):
        return {**super().get_template_context(), 'nav': 'checks', 'task': self.task}

    def form_valid(self, form):
        with transaction.atomic():
            task = self.task
            if task:
                task = Check.query.for_update().get(id=task.id)
                for key, value in form.cleaned_data.items():
                    setattr(task, key, value)
                task.update(fields=list(form.cleaned_data))
            else:
                task = Check.query.create(**form.cleaned_data)
            services.record_definition(task, self.user.email)
        return RedirectResponse(f'/?view=checks&mode={task.mode}&record={task.id}', status_code=303)


class FixesRedirect(Base):
    def get(self):
        return RedirectResponse('/?view=checks&mode=fix', status_code=302)


class RulesView(Base):
    template_name = 'task_rules.html'

    def get_template_context(self):
        from .core.models import Run
        from .core.tasks import PATTERNS
        rules = list(TaskRule.query.join('source__account__persona', 'source__account__site', 'source__provider', 'target').order_by('source__account', 'source__provider', 'id'))
        return {**super().get_template_context(), 'nav': 'checks', 'rules': rules,
                'patterns': PATTERNS, 'dispatches': list(Run.query.exclude(rule=None).join('rule', 'persona', 'provider').order_by('-created_at')[:50])}


class RuleForm(forms.Form):
    source = ModelChoiceField(queryset=Check.query.join('account__persona', 'account__site', 'provider'))
    status = forms.ChoiceField(choices=[('failure', 'Failed'), ('success', 'Passed')])
    state = forms.TextField(required=False)
    target = ModelChoiceField(queryset=Check.query.join('account__persona', 'account__site', 'provider'))
    enabled = forms.BooleanField(required=False)

    def clean(self):
        data = super().clean()
        source, target = data.get('source'), data.get('target')
        if source and target and (source.account.id != target.account.id or source.provider.id != target.provider.id):
            raise forms.ValidationError('Tasks must use the same account and provider')
        if source and target and source.id == target.id:
            raise forms.ValidationError('Choose a different next task')
        return data


class RuleEditor(Base, FormView):
    form_class = RuleForm
    template_name = 'rule_editor.html'

    @cached_property
    def rule(self):
        identifier = self.request.query_params.get('id')
        return TaskRule.query.get(id=int(identifier)) if identifier and identifier.isdecimal() else None

    def get_form_kwargs(self):
        rule = self.rule
        initial = {'source': rule.source.id, 'target': rule.target.id, 'status': rule.status,
                   'state': rule.state, 'enabled': rule.enabled} if rule else {'enabled': True, 'status': 'failure'}
        return {**super().get_form_kwargs(), 'initial': initial}

    def get_template_context(self):
        return {**super().get_template_context(), 'nav': 'checks', 'rule': self.rule}

    def form_valid(self, form):
        rule = self.rule
        if rule:
            for key, value in form.cleaned_data.items():
                setattr(rule, key, value)
            rule.update(fields=list(form.cleaned_data))
        else:
            TaskRule.query.get_or_create(**form.cleaned_data)
        return RedirectResponse('/tasks/rules', status_code=303)
