"""2Captcha acceptance uses the live app, real configuration and vendor extension."""

from app.core.recovery import config


def test_twocaptcha_is_an_integration():
    assert 'twocaptcha' in config.CONNECTORS


def test_integrations_exposes_twocaptcha_settings(app_session):
    response = app_session.get('/integrations?connection=twocaptcha&integration=twocaptcha')
    assert response.status_code == 200
    assert '2Captcha' in response.text
    assert 'name="api_key"' in response.text
    assert 'type="password"' in response.text
    assert 'name="retry_count"' in response.text
    assert 'name="retry_delay"' in response.text
    assert 'name="auto_submit"' in response.text
    assert 'Browserbase' in response.text
    assert 'Kernel' in response.text
    assert 'Anchor' in response.text


def test_twocaptcha_never_becomes_a_message_credential():
    from app.core.models import Account

    account = Account.query.first()
    assert account, 'Create an account before running integration acceptance'
    cfg = {'integration_scopes': {'shared': {'twocaptcha': {
        'enabled': True, 'retry_count': 0, 'auto_submit': False,
    }}}}
    assert config.effective_binding(cfg, account)['fields'] == {}


def test_live_settings_save_preserves_key_and_validates_options(app_session):
    from copy import deepcopy

    from app.core import twocaptcha

    original = deepcopy(config.load())
    assert twocaptcha.has_key(original), 'Configure a real 2Captcha key in Integrations'
    headers = {'Origin': 'http://127.0.0.1:8421'}
    def post(**data):
        return app_session.post('/integrations', data={'integration': 'twocaptcha', **data}, headers=headers)
    try:
        key = twocaptcha.api_key(original)
        assert post(action='connection', api_key=key).status_code == 303
        saved = config.load()
        assert bool(twocaptcha.api_key(saved) == key)
        assert key not in config.config_path().read_text()
        assert key not in app_session.get('/integrations?connection=twocaptcha').text
        assert post(action='connection', api_key='').status_code == 303
        assert bool(twocaptcha.api_key(config.load()) == key)
        assert post(action='connection', api_key='invalid').status_code == 200
        assert bool(twocaptcha.api_key(config.load()) == key)
        assert post(action='save_scope', retry_count='0', retry_delay='7', auto_submit='false').status_code == 303
        options = config.effective_integration(config.load(), 'twocaptcha', ['shared'])[0]
        assert options == {'enabled': True, 'retry_count': 0, 'retry_delay': 7, 'auto_submit': False}
        assert post(action='save_scope', retry_count='11', retry_delay='5', auto_submit='true').status_code == 200
        assert config.effective_integration(config.load(), 'twocaptcha', ['shared'])[0] == options
        assert post(action='toggle', mode='false').status_code == 303
        assert not config.effective_integration(config.load(), 'twocaptcha', ['shared'])[0]['enabled']
        assert post(action='test').status_code == 303
        assert 'Connected' in app_session.get('/integrations?connection=twocaptcha').text
    finally:
        config.save(original)


def test_vendor_archive_contains_extension_without_key():
    import zipfile

    from app.core import twocaptcha

    package = twocaptcha.extension_package()
    key = twocaptcha.api_key(config.load()).encode()
    assert key, 'Configure a real 2Captcha key'
    with zipfile.ZipFile(package['archive']) as archive:
        assert 'manifest.json' in archive.namelist()
        assert 'options/options.html' in archive.namelist()
        for name in archive.namelist():
            assert key not in archive.read(name), 'Secret leaked into vendor upload'


def test_provider_defaults_and_explicit_opt_in_through_ui(app_session):
    from app.core import twocaptcha
    from app.core.models import Persona, Provider, Run

    persona = Persona.query.get(name='2Captcha acceptance')
    cfg = config.load()
    assert cfg.get('twocaptcha', {}).get('providers', {}) == {}, 'Run with default provider preferences'
    headers = {'Origin': 'http://127.0.0.1:8421'}
    def checkout(kind):
        from pathlib import Path

        from plain.runtime import settings

        provider = Provider.query.get(name='2Captcha ' + kind)
        auth = {'Authorization': 'Bearer ' + (Path(settings.APP_CONFIG_DIR) / 'api-token').read_text().strip()}
        response = app_session.post('/api/checkouts', headers=auth, json={
            'persona_id': persona.id, 'provider_id': provider.id, 'scope': '2captcha.com',
            'external': True, 'check_ids': [], 'actor': '2Captcha defaults acceptance'})
        assert response.status_code == 201
        run = Run.query.get(id=response.json()['id'])
        assert app_session.post(f'/api/runs/{run.id}/end', headers=auth, json={}).status_code == 200
        assert app_session.post(f'/api/runs/{run.id}/checkin', headers=auth,
            json={'success': False, 'export_complete': False}).status_code == 200
        return run.runtime['twocaptcha']['enabled']
    assert checkout('local') is True
    for kind in sorted(twocaptcha.NATIVE_SOLVERS):
        assert checkout(kind) is False, kind
    try:
        assert app_session.post('/integrations', headers=headers, data={
            'action': 'provider', 'integration': 'twocaptcha', 'provider_kind': 'kernel', 'mode': 'true'}).status_code == 303
        assert checkout('kernel') is True
        assert checkout('browserbase') is False
    finally:
        assert app_session.post('/integrations', headers=headers, data={
            'action': 'provider', 'integration': 'twocaptcha', 'provider_kind': 'kernel', 'mode': 'inherit'}).status_code == 303
    assert checkout('kernel') is False
