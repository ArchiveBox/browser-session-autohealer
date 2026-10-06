"""Contract checks against the real HTTP server and collection database."""
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from plain.runtime import settings

from app.core.models import Check, Run, SessionRequest
from app.core.session_conditions import candidates, validate


@pytest.fixture
def api():
    token = (Path(settings.APP_CONFIG_DIR) / 'api-token').read_text().strip()
    with httpx.Client(base_url='http://127.0.0.1:8421', headers={'Authorization': 'Bearer ' + token}) as client:
        yield client


def test_unmet_conditions_never_launch_or_leak_a_connection(api):
    before = Run.query.count()
    response = api.post('/api/sessions', json={'require_all': [{'type': 'persona', 'id': str(uuid4())}]})
    assert response.status_code == 409
    assert response.headers['content-type'].startswith('application/problem+json')
    result = response.json()['request']
    assert result['detail']['code'] == 'conditions_unmet'
    assert all(candidate['unmet'] for candidate in result['detail']['candidates'])
    assert 'cdp_url' not in result and 'session_id' not in result
    assert Run.query.count() == before


def test_invalid_recheck_and_missing_request(api):
    assert api.post('/api/sessions', json={'recheck': True, 'allow_unhealthy': True}).status_code == 400
    assert api.post('/api/sessions', json={'prefer': None}).status_code == 400
    assert api.post('/api/sessions', json={'require_all': [{'type': 'task', 'site': None}]}).status_code == 400
    assert api.get('/api/sessions/' + str(uuid4())).status_code == 404
    response = api.post('/api/sessions', json={'recheck': True, 'timeout': 0})
    assert response.status_code == 409
    assert response.json()['request']['detail']['code'] == 'recheck_requires_wait'


def test_unknown_ip_history_cannot_satisfy_a_negative_location(api):
    # No real session visited this unconfigured site. NOT must preserve unknown.
    response = api.post('/api/sessions', json={'require_all': [{'not': {
        'type': 'ip', 'site': 'unconfigured.example', 'source': 'last_successful_session', 'country': 'US'}}]})
    assert response.status_code == 409
    assert all(any(u['reason'] == 'unknown_ip' for u in c['unmet']) or
               any(u['pointer'] == '/provider_options' for u in c['unmet'])
               for c in response.json()['request']['detail']['candidates'])


def test_deadline_and_durable_cancellation(api):
    missing = [{'type': 'persona', 'id': str(uuid4())}]
    response = api.post('/api/sessions', json={'require_all': missing, 'timeout': 1})
    assert response.status_code == 409
    result = response.json()['request']
    assert result['detail']['code'] == 'deadline_exceeded'
    assert SessionRequest.query.get(uid=result['id']).run is None
    response = api.post('/api/sessions', json={'require_all': missing, 'timeout': -1})
    assert response.status_code == 202
    url = response.json()['url']
    response = api.post(url, json={})
    assert response.status_code == 200 and response.json()['status'] == 'cancelled'
    assert api.post(url, json={}).json()['status'] == 'cancelled'


def test_cached_health_age_and_explicit_or_use_real_results():
    task = Check.query.filter(provider__kind='cdp', account__site__domain='news.ycombinator.com', mode='check').first()
    assert task
    identify = [{'type': 'persona', 'id': str(task.account.persona.uid)}, {'type': 'provider', 'id': str(task.provider.uid)}]
    fresh = {'type': 'task', 'site': task.account.site.domain, 'tasks': [str(task.uid)], 'max_age': 3600}
    rows = candidates(validate({'require_all': [*identify, fresh]}))
    assert rows[0]['eligible'], 'Run the real CDP handoff test first to establish current health'
    assert rows[0]['provider'].id == task.provider.id
    rows = candidates(validate({'require_all': [*identify, {**fresh, 'max_age': 0}]}))
    assert not any(r['eligible'] for r in rows)
    rows = candidates(validate({'require_all': [*identify, {'require_any': [
        {'type': 'task', 'site': 'unconfigured.example'}, fresh]}]}))
    assert rows[0]['eligible']


def test_network_editor_persists_inherited_config(app_session):
    task = Check.query.filter(provider__kind='browserbase', mode='check').first()
    url = f'/edit/network?persona={task.account.persona.id}&provider={task.provider.id}'
    response = app_session.get(url)
    assert response.status_code == 200 and 'proxy_country' in response.text
    # Submit the actual existing values unchanged through the user-facing editor.
    from app.core.models import PersonaProviderConfig
    row = PersonaProviderConfig.query.filter(persona=task.account.persona, provider=task.provider).first()
    config = row.config if row else {}
    response = app_session.post(url, data={k: config.get(k, '') for k in ('proxy_country', 'proxy_state', 'proxy_city')},
        headers={'Origin': 'http://127.0.0.1:8421'})
    assert response.status_code == 303
    row = PersonaProviderConfig.query.get(persona=task.account.persona, provider=task.provider)
    assert row.config == config
    assert app_session.get('/?view=ips').status_code == 200
