"""Plain forms for append-only script revisions and explicitly scoped sessions."""
from functools import cached_property

from plain import forms
from plain.http import NotFoundError404, RedirectResponse
from plain.postgres.forms import ModelChoiceField
from plain.templates.views import FormView

from .core import services
from .core.domain_patterns import matches, normalize
from .core.models import Check, CheckType
from .views import Base, check_account


class CheckTypeForm(forms.Form):
    name = forms.TextField(max_length=160)
    domain = forms.TextField(max_length=2048)
    lang = forms.ChoiceField(choices=[('python', 'Python'), ('js', 'JavaScript')])
    code = forms.TextField(strip=False)
    check = ModelChoiceField(queryset=Check.query.join('account__persona', 'account__site', 'provider'), required=False, empty_label='Unassigned')

    def clean_domain(self):
        return normalize(self.cleaned_data['domain'])

    def clean_code(self):
        # HTML textareas submit CRLF even when the original source uses LF.
        return self.cleaned_data['code'].replace('\r\n', '\n').replace('\r', '\n')

    def clean(self):
        values = super().clean()
        check = values.get('check')
        if check and values.get('domain') and not matches(values['domain'], check.account.site.domain):
            raise forms.ValidationError('Domains must include the linked check site')
        return values


class CheckTypeEditor(Base, FormView):
    template_name = 'check_type_editor.html'
    form_class = CheckTypeForm

    @cached_property
    def revision(self):
        identifier = self.request.query_params.get('id')
        if not identifier:
            return None
        if not identifier.isdecimal():
            raise NotFoundError404()
        revision = CheckType.query.filter(id=int(identifier)).first()
        if not revision:
            raise NotFoundError404()
        return revision

    def get_form_kwargs(self):
        revision = self.revision
        initial = {k: getattr(revision, k) for k in ('name', 'domain', 'lang', 'code')} if revision else {'domain': '*', 'lang': 'python'}
        if revision:
            initial['check'] = revision.check_id
        return {**super().get_form_kwargs(), 'initial': initial}

    def get_template_context(self):
        return {**super().get_template_context(), 'nav': 'types', 'revision': self.revision}

    def form_valid(self, form):
        values = form.cleaned_data
        revision = services.version_check_type(values['check'], name=values['name'],
            domain=values['domain'], lang=values['lang'], code=values['code'],
            author=self.user.email, previous=self.revision)
        return RedirectResponse(f'/?view=types&record={revision.id}', status_code=303)


class SessionForm(forms.Form):
    check = ModelChoiceField(queryset=Check.query.filter(enabled=True, provider__enabled=True).join('account__persona', 'account__site', 'provider'), empty_label='Choose check')


class SessionEditor(Base, FormView):
    template_name = 'session_editor.html'
    form_class = SessionForm

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'initial': {'check': self.request.query_params.get('check', '')}}

    def get_template_context(self):
        return {**super().get_template_context(), 'nav': 'agents' if self.request.query_params.get('ai') else 'runs'}

    def form_valid(self, form):
        check = form.cleaned_data['check']
        run = check_account(check.account, self.user.email, check.provider, check.id)[0]
        return RedirectResponse(f'/?view=runs&run={run.id}&check={check.id}', status_code=303)
