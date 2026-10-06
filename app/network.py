"""Adapter-defined network overrides, scoped to one persona/provider pair."""
from copy import deepcopy

from plain.http import RedirectResponse
from plain.postgres import transaction

from .core.models import Persona, PersonaProviderConfig, Provider
from .core.providers import adapter
from .views import Base


class NetworkEditor(Base):
    template_name = 'network.html'
    error = ''

    def selection(self):
        params = self.request.query_params
        persona = Persona.query.filter(id=int(params['persona'])).first() if params.get('persona') else None
        provider = Provider.query.filter(id=int(params['provider'])).first() if params.get('provider') else None
        binding = PersonaProviderConfig.query.filter(persona=persona, provider=provider).first() if persona and provider else None
        return persona, provider, binding

    def get_template_context(self):
        persona, provider, binding = self.selection()
        fields = []
        if provider:
            for key, label in adapter(provider).network_fields.items():
                def read(config, key=key):
                    for part in key.split('.'):
                        config = config.get(part, '') if isinstance(config, dict) else ''
                    return config
                fields.append({'key': key, 'label': label, 'value': self.request.form_data.get(key, '')
                               if self.request.method == 'POST' else read(binding.config) if binding else '',
                               'default': read(provider.config)})
        return {**super().get_template_context(), 'nav': 'providers', 'persona': persona, 'provider': provider,
                'personas': list(Persona.query.order_by('name')), 'providers': list(Provider.query.order_by('name')),
                'fields': fields, 'error': self.error}

    def post(self):
        persona, provider, binding = self.selection()
        try:
            if not persona or not provider:
                raise ValueError('Choose a persona and provider')
            config = deepcopy(binding.config) if binding else {}
            for key in adapter(provider).network_fields:
                value = self.request.form_data.get(key, '').strip()
                root, _, nested = key.partition('.')
                config.pop(root, None)
                if value:
                    config[root] = {nested: value} if nested else value
            adapter(provider).validate_config({**provider.config, **config})
            with transaction.atomic():
                row, _ = PersonaProviderConfig.query.get_or_create(persona=persona, provider=provider, defaults={'config': config})
                row.config = config
                row.update(fields=['config'])
            return RedirectResponse(f'/edit/network?persona={persona.id}&provider={provider.id}&saved=1', status_code=303)
        except ValueError as exc:
            self.error = str(exc)
            return self.get()
