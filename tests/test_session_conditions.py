"""Condition language contracts; no substituted browser or database results."""
import pytest

from app.core.session_conditions import validate


def test_defaults_never_request_a_broken_session():
    spec = validate({})
    assert spec['recheck'] is False
    assert spec['allow_unhealthy'] is False
    assert spec['timeout'] == 0


def test_fresh_checks_cannot_be_requested_without_passing():
    with pytest.raises(ValueError):
        validate({'recheck': True, 'allow_unhealthy': True})
    with pytest.raises(ValueError):
        validate({'require_all': [{'type': 'task', 'site': 'x.com', 'status': 'failed'}]})


@pytest.mark.parametrize('spec', [
    {'require_al': []}, {'timeout': True}, {'timeout': -2},
    {'require_all': [{'type': 'ip', 'country': 'usa'}]},
    {'require_all': [{'type': 'ip', 'site': 'x.com', 'country': 'US'}]},
    {'require_all': [{'type': 'ip', 'source': 'last_successful_session', 'country': 'US'}]},
    {'require_all': [{'type': 'ip', 'max_age': 60, 'country': 'US'}]},
    {'require_all': [{'type': 'task', 'site': 'x.com', 'max_age': -1}]},
    {'require_all': [{'type': 'task', 'site': None}]},
    {'require_all': [{'type': 'task', 'site': ['x.com']}]},
    {'require_all': [{'type': 'provider', 'id': 'missing', 'typo': True}]},
    {'require_all': [{'require_any': []}]},
])
def test_invalid_conditions_fail_explicitly(spec):
    with pytest.raises(ValueError):
        validate(spec)


def test_nested_conditions_and_ordered_preferences():
    spec = {'require_all': [{'require_any': [
        {'type': 'provider', 'kind': 'local'}, {'type': 'provider', 'kind': 'browserbase'},
    ]}, {'type': 'task', 'site': 'news.ycombinator.com', 'tasks': '*', 'max_age': 1200}],
        'prefer': [{'type': 'ip', 'country': c}
                   for c in ('US', 'CA', 'MX')], 'recheck': True, 'timeout': 50}
    result = validate(spec)
    assert result['prefer'] == spec['prefer']
    assert result['require_all'][1]['status'] == 'healthy'


def test_task_quorum_is_explicit_and_positive():
    task = {'type': 'task', 'site': 'x.com', 'tasks': ['Sign in', 'Feed', 'Search', 'Profile'], 'max_age': 1200}
    assert validate({'require_all': [{**task, 'min_passed': 3}]})['require_all'][0]['min_passed'] == 3
    for count in (0, -1, True, 1.5, '3', 5):
        with pytest.raises(ValueError):
            validate({'require_all': [{**task, 'min_passed': count}]})
    with pytest.raises(ValueError):
        validate({'require_all': [{**task, 'tasks': ['Sign in', 'Sign in'], 'min_passed': 2}]})


def test_quorum_and_preferred_tasks_use_real_check_history():
    from uuid import uuid4

    from app.core.models import Check
    from app.core.session_conditions import evaluate
    task = Check.query.filter(provider__kind='cdp', account__site__domain='news.ycombinator.com', mode='check').first()
    assert task
    required = {'type': 'task', 'site': task.account.site.domain, 'tasks': [str(task.uid)], 'max_age': 86400}
    missing = str(uuid4())
    # An unconfigured task is not a passing result, but an explicit quorum can tolerate it.
    partial = {**required, 'tasks': [str(task.uid), missing], 'min_passed': 1}
    def assess(condition, preferences=None):
        return evaluate(validate({'require_all': [condition], 'prefer': preferences or []}), task.account.persona, task.provider)
    assert assess(partial)[0], 'Establish real HN health with test_session_handoff first'
    failed = assess({**partial, 'min_passed': 2})
    assert not failed[0]
    assert failed[2][0]['passed'] == 1 and failed[2][0]['required'] == 2
    assert not assess({k: v for k, v in partial.items() if k != 'min_passed'})[0]
    assert not assess({**partial, 'max_age': 0})[0]
    # Requesting the same check by two aliases never counts it twice.
    assert not assess({**partial, 'tasks': [str(task.uid), task.name], 'min_passed': 2})[0]
    assert not assess({**partial, 'tasks': [missing]})[0]
    optional = {**required, 'max_age': 0}
    assert assess(required, [optional])[:2] == (True, (False,))
    preparing = validate({'require_all': [required], 'prefer': [optional], 'recheck': True})
    assert evaluate(preparing, task.account.persona, task.provider, preparing=True)[:2] == (True, (False,))
    assert assess(partial, [required])[:2] == (True, (True,))
    # Preferences still see configured checks outside the required selector.
    selected = assess(partial, [{'type': 'task', 'site': 'x.com', 'tasks': '*', 'max_age': 0}])[3]
    assert any(c.account.site.domain == 'x.com' for c in selected)
