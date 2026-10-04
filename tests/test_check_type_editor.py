"""Real forms and immutable revision metadata, using retained HN/LinkedIn source."""
from app.core.domain_patterns import matches, normalize
from app.core.models import Check, CheckType


def test_domain_matching():
    assert normalize(' Google.com, *.GOOGLE.com,google.com ') == 'google.com, *.google.com'
    assert matches('google.com, *.google.com', 'google.com')
    assert matches('google.com, *.google.com', 'accounts.google.com')
    assert not matches('*.google.com', 'google.com')
    assert not matches('google.com, *.google.com', 'google.com.example.org')
    assert not matches('google.com, *.google.com', 'notgoogle.com')


def test_type_forms_and_revision_details(app_session):
    current = CheckType.query.filter(check_id=9).order_by('-created_at').first()
    for path in ['/edit/check-type', f'/edit/check-type?id={current.id}', '/edit/session', '/edit/session?ai=1']:
        response = app_session.get(path)
        assert response.status_code == 200, path
        assert 'method="post"' in response.text
        assert 'Check object (' not in response.text
        assert f'{Check.query.get(id=current.check_id).account.persona.name} · news.ycombinator.com · Local · abx-dl' in response.text
    detail = app_session.get(f'/tree/types/{current.id}')
    assert detail.status_code == 200
    assert current.domain in detail.text
    assert f'/edit/check-type?id={current.id}' in detail.text
    assert '/edit/check?id=9' in detail.text
    assert 'Revisions' in detail.text and 'type-code' in detail.text
    page = app_session.get('/?view=types&domain=news.ycombinator.com')
    assert page.status_code == 200 and current.domain in page.text
    assert 'Signed in and reading LinkedIn' not in page.text


def test_plain_form_rejects_bad_and_mismatched_domains(app_session):
    before = CheckType.query.count()
    current = CheckType.query.filter(check_id=9).order_by('-created_at').first()
    values = {'name':current.name, 'lang':current.lang, 'code':current.code, 'check':9}
    for domain, message in [('https://google.com/path', 'Use domains'), ('linkedin.com', 'Domains must include')]:
        response = app_session.post(f'/edit/check-type?id={current.id}',
            data={**values, 'domain': domain}, headers={'Origin':'http://127.0.0.1:8421'})
        assert response.status_code == 200
        assert message in response.text
        assert CheckType.query.count() == before


def test_create_form_reuses_identical_real_revision(app_session):
    current = CheckType.query.filter(check_id=9).order_by('-created_at').first()
    before = CheckType.query.count()
    values = {key: getattr(current, key) for key in ('name', 'domain', 'lang', 'code')}
    values['code'] = current.code.replace('\n', '\r\n')
    response = app_session.post('/edit/check-type',
        data=values | {'check': 9},
        headers={'Origin': 'http://127.0.0.1:8421'})
    assert response.status_code == 303
    assert response.headers['location'] == f'/?view=types&record={current.id}'
    assert CheckType.query.count() == before


def test_single_persona_leader_and_fk_links(app_session):
    from app.core.models import Leader, Persona, Run
    for persona in Persona.query.all():
        assert Leader.query.filter(persona=persona).count() == 1
        newest = Run.query.filter(persona=persona, promoted=True).order_by('-finished_at').first()
        if newest:
            assert Leader.query.get(persona=persona).checkpoint.digest == newest.tip
    response = app_session.get('/?view=runs')
    assert 'Persona leader' in response.text and 'Site leader' not in response.text
    assert 'news.ycombinator.com' in response.text
    assert '/edit/persona?id=4' in response.text
    assert '/edit/provider?id=3' in response.text
    for path in ['/tree/personas/4', '/tree/accounts/7', '/tree/checks/9', '/tree/providers/3']:
        response = app_session.get(path)
        assert response.status_code == 200
        assert 'record-details' in response.text
        assert 'detail-fields' in response.text


def test_database_enforces_one_leader_per_persona():
    import pytest
    from plain.postgres import get_connection, transaction
    from psycopg.errors import UniqueViolation

    from app.core.models import Leader
    leader = Leader.query.first()
    before = Leader.query.count()
    with pytest.raises(UniqueViolation), transaction.atomic():
        with get_connection().cursor() as cursor:
            cursor.execute('INSERT INTO core_leader (persona_id, checkpoint_id, verified, finished_at) '
                           'SELECT persona_id, checkpoint_id, verified, finished_at FROM core_leader WHERE id = %s',
                           [leader.id])
        pytest.fail('Database accepted a second leader for the same persona')
    assert Leader.query.count() == before
