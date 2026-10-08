"""Real HTTP form, database and encrypted-storage acceptance for persona setup."""
import os
import re
from uuid import uuid4

import httpx
import pytest

from app.core.models import Persona

ORIGIN = os.environ.get('ACCOUNT_CHECKER_TEST_ORIGIN', 'http://127.0.0.1:8421')


def test_short_autofill_facts_do_not_corrupt_browser_identifiers():
    from app.core.recovery.redaction import redact
    observed = '3-17 textbox Year value="1990"; xpath=/html[1]/div[3]/input[1]; Day value="3"'
    masked = redact(observed, {'fact:dob_day':'3', 'fact:dob_year':'1990'})
    assert masked == '3-17 textbox Year value="[secret redacted]"; xpath=/html[1]/div[3]/input[1]; Day value="[secret redacted]"'
    assert redact('https://example.com/code/1990', {'otp':'1990'}) == 'https://example.com/code/[secret redacted]'
    assert redact('path/1990', {'fact:dob_year':'1990', 'otp':'1990'}) == 'path/[secret redacted]'


def test_optional_setup_wizard_is_authenticated(app_session):
    assert httpx.get(ORIGIN + '/personas/setup').status_code == 302
    page = app_session.get('/personas/setup')
    assert page.status_code == 200
    for name in ('first_name', 'last_name', 'username', 'dob', 'address1', 'address2',
                 'city', 'state', 'country', 'zip', 'email', 'phone'):
        assert f'name="{name}"' in page.text
    assert 'Create a new Gmail account' in page.text
    assert 'Create a phone number with Cloaked' in page.text
    assert 'Create an email alias with Cloaked' in page.text


def test_blank_setup_is_saved_once_without_signup(app_session):
    page = app_session.get('/personas/setup')
    key = re.search(r'name="key" value="([^"]+)"', page.text)[1]
    data = {'key': key, 'action': 'save'}
    first = app_session.post('/personas/setup', data=data, headers={'Origin': ORIGIN})
    assert first.status_code == 303
    second = app_session.post('/personas/setup', data=data, headers={'Origin': ORIGIN})
    assert second.status_code == 303 and second.headers['location'] == first.headers['location']
    from app.core.models import PersonaSetup
    setup = PersonaSetup.query.get(key=key)
    assert setup.status == 'draft' and setup.run is None
    assert Persona.query.filter(id=setup.persona.id).count() == 1
    detail = app_session.get(first.headers['location'])
    assert detail.status_code == 200 and 'Start account setup' in detail.text


@pytest.mark.parametrize('values,error', [
    ({'dob': '1990-02-30'}, 'valid date'),
    ({'email': 'bad-address'}, 'valid email'),
    ({'sites': 'facebook,gmail'}, 'Gmail first'),
    ({'sites': 'gmail,instagram'}, 'Facebook before Instagram'),
    ({'sites': 'gmail,gmail'}, 'at most once'),
    ({'sites': 'unknown'}, 'at most once'),
])
def test_invalid_details_do_not_create_personas(app_session, values, error):
    from app.core.models import PersonaSetup
    key = str(uuid4())
    response = app_session.post('/personas/setup', data={'key': key, **values}, headers={'Origin': ORIGIN})
    assert response.status_code == 200 and error in response.text
    assert not PersonaSetup.query.filter(key=key).exists()


def test_private_facts_and_selected_skills_round_trip(app_session):
    from app.core.models import PersonaSetup
    from app.core.onboarding import read
    key = str(uuid4())
    facts = {'first_name': '<script>research</script>', 'dob': '1990-08-03',
             'email': f'{key}@example.com', 'sites': 'gmail,youtube,reddit', 'create_gmail': 'on'}
    response = app_session.post('/personas/setup', data={'key':key, **facts}, headers={'Origin': ORIGIN})
    assert response.status_code == 303
    setup = PersonaSetup.query.get(key=key)
    assert read(setup)['facts']['email'] == facts['email']
    assert read(setup)['sites'] == ['gmail','youtube','reddit']
    assert facts['email'] not in setup.private_data
    assert setup.persona.config == {}
    page = app_session.get(response.headers['location'])
    assert page.status_code == 200 and 'no-store' in page.headers['cache-control'].split(', ')
    assert '&lt;script&gt;research&lt;/script&gt;' in page.text
    assert '<script>research</script>' not in page.text
    assert '+ reddit.com' in page.text and '+ youtube.com' in page.text
    for resource in re.findall(r'(?:src|href)="(/assets/persona_setup[^\"]+)"', page.text):
        assert app_session.get(resource).status_code == 200


def test_new_cloaked_connection_validation_does_not_overwrite_settings(app_session):
    from app.core.recovery.config import load
    before = load().get('cloaked')
    response = app_session.post('/integrations', data={'action':'connection', 'integration':'cloaked',
        'cdp_url':'http://example.com:9222'}, headers={'Origin': ORIGIN})
    assert response.status_code == 200 and 'HTTPS or WSS' in response.text
    assert load().get('cloaked') == before


def test_invalid_site_edit_keeps_saved_chain_and_renders_error(app_session):
    from app.core.models import PersonaSetup
    from app.core.onboarding import read
    key = str(uuid4())
    response = app_session.post('/personas/setup', data={'key':key, 'sites':'gmail,reddit'}, headers={'Origin':ORIGIN})
    assert response.status_code == 303
    invalid = app_session.post(response.headers['location'], data={'sites':'unknown'}, headers={'Origin':ORIGIN})
    assert invalid.status_code == 200 and 'at most once' in invalid.text
    assert read(PersonaSetup.query.get(key=key))['sites'] == ['gmail','reddit']
