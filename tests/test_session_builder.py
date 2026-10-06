"""Exercise the browser-session forms through the running application's HTTP routes."""
import json
import re
from uuid import uuid4

import httpx
import pytest

from app.core.models import Check, Run, SessionRequest

ORIGIN = 'http://127.0.0.1:8421'


def test_builder_and_task_editor_are_distinct(app_session):
    page = app_session.get('/edit/session')
    assert page.status_code == 200
    for label in ('Choose requirements', 'Site access', 'Your session', 'Advanced conditions',
                  'Conditions JSON', 'Provider options JSON', 'Recheck before use', 'Lifetime', 'API request', 'Create session'):
        assert label in page.text
    builder = json.loads(re.search(r'<script type="application/json" id="builder-data">(.*?)</script>', page.text, re.DOTALL)[1])
    assert all(set(provider) == {'id', 'name'} for provider in builder['providers'])
    assert builder['tasks'] and all(t['persona'] and t['provider'] for t in builder['tasks'])
    for task in builder['tasks']:
        if task['image']:
            assert app_session.get(task['image']).status_code == 200
    script = re.search(r'src="(/assets/session_builder(?:\.[a-f0-9]+)?\.js)"', page.text)
    assert script and app_session.get(script[1]).status_code == 200
    assert app_session.get('/edit/task-session').status_code == 200
    assert httpx.get(ORIGIN + '/edit/session').status_code == 302


@pytest.mark.parametrize('document', [[], {'require_all': None}, {'recheck': True, 'allow_unhealthy': True}])
def test_invalid_form_does_not_allocate(app_session, document):
    key = str(uuid4())
    response = app_session.post('/edit/session', data={'key': key, 'document': json.dumps(document)},
                                headers={'Origin': ORIGIN})
    assert response.status_code == 200
    assert 'class="error"' in response.text
    assert not SessionRequest.query.filter(key=key).exists()


def test_unavailable_result_and_duplicate_submission(app_session):
    key = str(uuid4())
    spec = {'require_all': [{'type': 'persona', 'id': str(uuid4())}], 'timeout': 0}
    data = {'key': key, 'document': json.dumps(spec)}
    response = app_session.post('/edit/session', data=data, headers={'Origin': ORIGIN})
    assert response.status_code == 303
    location = response.headers['location']
    row = SessionRequest.query.get(key=key)
    assert row.status == 'failed' and row.run is None
    repeated = app_session.post('/edit/session', data=data, headers={'Origin': ORIGIN})
    assert repeated.headers['location'] == location
    assert SessionRequest.query.filter(key=key).count() == 1
    page = app_session.get(location)
    assert page.status_code == 200 and 'Unavailable' in page.text
    assert 'id="cdp-url"' not in page.text
    fragment = app_session.get(location + '/status')
    assert fragment.status_code == 200 and 'id="session-status"' in fragment.text
    assert '<html' not in fragment.text
    assert 'no-store' in fragment.headers['cache-control'].split(', ')
    version = re.search(r'data-version="([a-f0-9]+)"', fragment.text)[1]
    unchanged = app_session.get(location + '/status', params={'version': version})
    assert unchanged.status_code == 204 and unchanged.content == b''
    assert httpx.get(ORIGIN + location + '/status').status_code == 302
    assert app_session.get('/sessions/not-a-uuid').status_code == 404


def test_availability_and_edit_keep_the_requested_conditions(app_session):
    task = Check.query.filter(provider__kind='anchor', account__site__domain='linkedin.com', mode='check', enabled=True).get()
    spec = {'require_all': [
        {'type': 'persona', 'id': str(task.account.persona.uid)},
        {'type': 'provider', 'id': str(task.provider.uid)},
        {'type': 'task', 'site': task.account.site.domain, 'max_age': 0},
        {'type': 'ip', 'country': 'AO'},
    ], 'timeout': 0, 'actor': str(uuid4())}
    preview = app_session.post('/sessions/preview', json=spec, headers={'Origin': ORIGIN})
    assert preview.status_code == 200
    assert preview.text.count('class="availability-row"') == 1
    assert task.provider.name in preview.text and task.name in preview.text
    assert 'Check too old' in preview.text and 'limit 0 min' in preview.text
    screenshot = re.search(r'<img src="(/evidence/\d+/[^"]+)"', preview.text)
    assert screenshot and app_session.get(screenshot[1]).headers['content-type'].startswith('image/')
    assert 'Browserbase' not in preview.text
    assert not Run.query.filter(actor=spec['actor']).exists()
    assert not SessionRequest.query.filter(spec__actor=spec['actor']).exists()
    assert app_session.post('/sessions/preview', json={'require_all': None}, headers={'Origin': ORIGIN}).status_code == 400
    assert httpx.post(ORIGIN + '/sessions/preview', json=spec).status_code == 302
    assert app_session.get('/sessions/preview').status_code == 405

    response = app_session.post('/edit/session', data={'key': str(uuid4()), 'document': json.dumps(spec)}, headers={'Origin': ORIGIN})
    assert response.status_code == 303
    uid = response.headers['location'].rsplit('/', 1)[1]
    row = SessionRequest.query.get(uid=uid)
    assert row.status == 'failed' and row.run is None
    status = app_session.get(response.headers['location'])
    assert 'Check too old' in status.text and f'/edit/session?request={uid}' in status.text
    for recheck in (False, True):
        editor = app_session.get('/edit/session', params={'request': uid, 'recheck': '1' if recheck else '0'})
        assert editor.status_code == 200
        builder = json.loads(re.search(r'id="builder-data">(.*?)</script>', editor.text, re.DOTALL)[1])
        initial = builder['initial']
        assert initial['require_all'] == row.spec['require_all']
        assert initial['recheck'] is recheck
        assert initial['allow_unhealthy'] is False
        assert initial['timeout'] == (60 if recheck else 0)
        assert all('config' not in provider for provider in builder['providers'])
    assert SessionRequest.query.get(uid=uid).spec == row.spec
    assert not Run.query.filter(actor=spec['actor']).exists()
    assert app_session.get('/edit/session', params={'request': 'not-a-uuid'}).status_code == 404
