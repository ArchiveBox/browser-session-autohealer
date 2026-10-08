"""Optional persona setup, selected site skills and resumable progress."""
from uuid import uuid4

from plain.http import NotFoundError404, RedirectResponse

from .core import onboarding
from .core.models import Check, PersonaSetup, Provider
from .core.recovery.config import Unavailable, load
from .views import Base


class SetupWizard(Base):
    template_name = 'persona_setup.html'
    error = ''

    def get_template_context(self):
        ctx = super().get_template_context()
        setup = None
        if self.url_kwargs.get('id'):
            setup = PersonaSetup.query.filter(persona__id=self.url_kwargs['id']).first()
            if not setup:
                raise NotFoundError404()
        data = onboarding.read(setup) if setup else {'facts': {}, 'sites': list(onboarding.SITES),
            'options': {'create_gmail': True, 'cloaked_phone': True, 'cloaked_email': True,
                        'save_onepassword': bool(load().get('onepassword', {}).get('vault'))}}
        values = {**data['facts'], **{key:'on' for key,val in data['options'].items() if val}}
        values['sites'] = ','.join(data['sites'])
        if self.request.method == 'POST':
            values = self.request.form_data
        ctx.update(nav='personas', setup=setup, values=values, error=self.error,
            contacts=data.get('cloaked', {}), progress_sites=data['sites'],
            manual_task=Check.query.filter(id=setup.run.plan[0]['id']).first() if setup and setup.run and setup.run.plan else None,
            manual_credentials=data['accounts'].get(next((site for site in data['sites'] if site not in setup.completed), ''), {}) if setup else {},
            request_key=str(setup.key) if setup else values.get('key') or str(uuid4()),
            facts=onboarding.FACTS, sites=onboarding.SITES, skills=onboarding.SKILLS,
            providers=list(Provider.query.filter(enabled=True).order_by('name')),
            active=bool(setup and setup.run and setup.run.status in onboarding.ACTIVE),
            builder={'sites': {key:{'domain':v['domain'], 'title':v['title'],
                        'skill':onboarding.SKILLS[key], 'requires':onboarding.DEPENDENCIES.get(key, [])}
                        for key,v in onboarding.SITES.items()},
                     'selected': list(dict.fromkeys(key for key in values.get('sites', '').split(',') if key in onboarding.SITES))})
        return ctx

    def get(self):
        response = super().get()
        response.headers['Cache-Control'] = 'no-store'
        return response

    def post(self):
        form = self.request.form_data
        try:
            if self.url_kwargs.get('id'):
                setup = PersonaSetup.query.filter(persona__id=self.url_kwargs['id']).first()
                if not setup:
                    raise NotFoundError404()
                setup = onboarding.update(setup, form)
            else:
                setup = onboarding.create(form, self.user.email)
            if form.get('action') == 'start':
                setup = onboarding.queue(setup.id, int(form.get('provider') or 0), self.user.email)
            return RedirectResponse(f'/personas/{setup.persona.id}/setup', status_code=303)
        except (ValueError, Unavailable) as error:
            self.error = str(error)
            return self.get()
