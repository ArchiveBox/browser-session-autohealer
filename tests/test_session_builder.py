"""Exercise the browser-session forms through the running application's HTTP routes."""
import json
import re
from uuid import uuid4

import httpx
import pytest

from app.core.models import Run, SessionRequest

ORIGIN = 'http://127.0.0.1:8421'


def test_builder_and_task_editor_are_distinct(app_session):
    page = app_session.get('/edit/session')
    assert page.status_code == 200
    for label in ('Choose requirements', 'Site access', 'Your session', 'Advanced conditions',
                  'Require all', 'Prefer', 'Recheck before use', 'Lifetime', 'API request', 'Create session'):
        assert label in page.text
    builder = json.loads(re.search(r'<script type="application/json" id="builder-data">(.*?)</script>', page.text, re.DOTALL)[1])
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
    before = Run.query.count()
    response = app_session.post('/edit/session', data={'key': str(uuid4()), 'document': json.dumps(document)},
                                headers={'Origin': ORIGIN})
    assert response.status_code == 200
    assert 'class="error"' in response.text
    assert Run.query.count() == before


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
