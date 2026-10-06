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
        'prefer': [{'type': 'ip', 'source': 'last_successful_session', 'country': c}
                   for c in ('US', 'CA', 'MX')], 'recheck': True, 'timeout': 50}
    result = validate(spec)
    assert result['prefer'] == spec['prefer']
    assert result['require_all'][1]['status'] == 'healthy'
