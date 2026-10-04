"""Exercise the actual session evidence and configuration UI with retained real data."""
from app.core.models import Run


def test_agents_live_under_sessions(app_session):
    response = app_session.get('/agents?run=53&check=12', follow_redirects=True)
    assert response.status_code == 200
    assert '<h1>Browser Sessions</h1>' in response.text
    nav = response.text.split('<nav aria-label="Main navigation">')[1].split('</nav>')[0]
    assert 'Agent activity' not in nav
    assert 'Integrations' in nav and 'Login help' not in nav
    assert 'data-load="/runs/53/tree?check=12"' in response.text


def test_session_tree_uses_frozen_data_and_scoped_evidence(app_session):
    response = app_session.get('/runs/53/tree?check=12')
    assert response.status_code == 200
    for label in ('Cookies', 'Local storage', 'IndexedDB', 'Session storage', 'OPFS'):
        assert label in response.text
    assert 'data-site="linkedin.com"' in response.text
    assert 'data-check="12"' in response.text
    assert 'Not captured' in response.text
    activity = app_session.get('/runs/53/checks/12/activity')
    assert activity.status_code == 200
    assert 'type="range"' in activity.text
    assert '/runs/53/checks/12/conversation' in activity.text
    conversation = app_session.get('/runs/53/checks/12/conversation')
    assert 'title="OpenCode conversation"' in conversation.text
    assert '/evidence/53/check-12.png' in activity.text
    assert '/evidence/53/check-10.png' not in activity.text
    assert app_session.get('/runs/53/checks/9/activity').status_code == 404
    assert Run.query.get(id=53).status == 'failed'


def test_integrations_has_scope_matrix(app_session):
    response = app_session.get('/integrations')
    assert response.status_code == 200
    assert '<h1>Integrations</h1>' in response.text
    assert 'aria-label="Integration permissions"' in response.text
    for key in ('shared', 'persona:4', 'site:7', 'site:8', 'check:9', 'check:12'):
        assert f'data-scope="{key}"' in response.text
    for name in ('1Password', 'Email', 'Google Voice', 'Messages on this Mac'):
        assert name in response.text
    assert 'Restore login' not in response.text
    old = app_session.get('/login-help?account=8', follow_redirects=True)
    assert old.status_code == 200 and '<h1>Integrations</h1>' in old.text


def test_all_sidebar_views_use_collapsed_record_trees(app_session):
    expected = {'runs':'Browser Sessions', 'personas':'Personas', 'sites':'Sites',
                'checks':'Checks', 'agents':'AI Sessions', 'types':'Check Types', 'providers':'Browser Providers'}
    for view, title in expected.items():
        response = app_session.get('/?view=' + view)
        assert response.status_code == 200, view
        assert f'<h1>{title}</h1>' in response.text
        assert f'data-tree="{view}"' in response.text
        assert 'class="record-node"' in response.text
        assert '<iframe' not in response.text
        from html.parser import HTMLParser
        class OpenRecords(HTMLParser):
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                assert not (tag == 'details' and attrs.get('class') == 'record-node' and 'open' in attrs)
        OpenRecords().feed(response.text)
    for path in ('/tree/personas/4', '/tree/accounts/7', '/tree/accounts/8', '/tree/checks/9',
                 '/tree/executions/9', '/tree/providers/1', '/tree/providers/3'):
        assert app_session.get(path).status_code == 200, path
    assert app_session.get('/assets/session_tree.js', follow_redirects=True).status_code == 200
    assert app_session.get('/assets/session_tree.css', follow_redirects=True).status_code == 200


def test_type_only_lists_executions_of_that_revision(app_session):
    from app.core.models import CheckRunStep, CheckType
    revision = CheckType.query.order_by('-created_at').first()
    runs = {s.check_run.run.id for s in CheckRunStep.query.filter(check_type=revision).join('check_run__run')}
    response = app_session.get(f'/tree/types/{revision.id}')
    assert response.status_code == 200
    for run in runs:
        assert f'/runs/{run}/checks/{revision.check_id}/activity' in response.text
    for run in Run.query.exclude(id__in=list(runs)):
        assert f'/runs/{run.id}/checks/{revision.check_id}/activity' not in response.text


def test_provider_and_type_rows_show_real_linked_evidence(app_session):
    for view in ('providers', 'types'):
        response = app_session.get('/?view=' + view)
        assert response.status_code == 200
        assert '/evidence/55/check-9.png' in response.text
        assert '/runs/55/checks/9' in response.text
        assert 'Oct 4, 12:38 AM PDT' in response.text
    from app.core.models import CheckType
    current = CheckType.query.filter(check_id=9).order_by('-created_at').first()
    response = app_session.get(f'/tree/types/{current.id}')
    assert '/evidence/55/check-9.png' in response.text
    assert 'Captured in' in response.text


def test_integration_overrides_save_and_control_recovery(app_session):
    from copy import deepcopy

    import pytest

    from app.core.models import Check
    from app.core.recovery import config
    from app.core.recovery.broker import Broker
    from app.core.storage import data_root

    original = deepcopy(config.load())
    count = Run.query.count()
    check = Check.query.get(id=9)
    other = Check.query.get(id=10)
    headers = {'Origin': 'http://127.0.0.1:8421'}
    def post(**data):
        response = app_session.post('/integrations', data=data, headers=headers)
        assert response.status_code == 303, response.text
    def effective(c):
        cfg = config.load()
        return config.effective_integration(cfg, 'imap', config.scope_chain(c.account, c), c.account)[0]
    try:
        post(action='save_scope', scope='persona:4', integration='imap', pattern=r'(?<!\d)(\d{6})(?!\d)')
        post(action='toggle', scope='shared', integration='imap', mode='true')
        assert effective(check)['enabled'] and effective(other)['enabled']
        assert effective(check)['pattern'] == r'(?<!\d)(\d{6})(?!\d)'
        post(action='toggle', scope='site:7', integration='imap', mode='false')
        assert not effective(check)['enabled'] and not effective(other)['enabled']
        post(action='toggle', scope='check:9', integration='imap', mode='true')
        assert effective(check)['enabled'] and not effective(other)['enabled']
        binding = config.effective_binding(config.load(), check.account, check)
        assert binding['fields']['email_code']['pattern'] == r'(?<!\d)(\d{6})(?!\d)'
        post(action='toggle', scope='check:9', integration='imap', mode='inherit')
        assert not effective(check)['enabled']
        binding = config.effective_binding(config.load(), check.account, check)
        assert 'email_code' not in binding['fields']
        broker = Broker(config.load(), binding, data_root())
        with pytest.raises(config.Unavailable, match='No credential source'):
            broker.request('email_code')
        assert Run.query.count() == count
    finally:
        config.save(original)
    assert config.load() == original
