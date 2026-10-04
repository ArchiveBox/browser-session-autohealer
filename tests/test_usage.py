"""Token sums from fresh real OpenCode transcripts and the Django cache."""
import json

from app.core import storage, usage
from app.core.models import CheckRun, Persona, Run


def test_opencode_totals_and_django_cache():
    results = list(CheckRun.query.filter(status='success').join('run'))
    assert results
    usage.cache.clear()
    for result in results:
        path = storage.data_root() / 'runs' / str(result.run.id) / f'transcript-{result.check_id}.json'
        messages = json.loads(path.read_text())
        expected = sum(m['info']['tokens']['total'] for m in messages if m['info']['role'] == 'assistant')
        assert expected > 0
        assert usage.for_run(result.run, result.check_id) == expected
        stat = path.stat()
        key = f'{result.run.id}:{path.name}:{stat.st_mtime_ns}:{stat.st_size}'
        assert usage.cache.get(key) == expected
        expected_session = sum(usage.count(json.loads(p.read_text())) for p in path.parent.glob('transcript-*.json'))
        assert usage.for_run(result.run) == expected_session


def test_persona_and_site_sums_count_each_session_once():
    for persona in Persona.query.all():
        runs = list(Run.query.filter(persona=persona))
        expected = sum(usage.for_run(r) or 0 for r in runs)
        assert usage.grouped('persona', [persona.id])[persona.id] == expected
        domains = {r.scope for r in runs}
        site_totals = usage.grouped('site', domains)
        for domain in domains:
            assert site_totals[domain] == sum(usage.for_run(r) or 0 for r in Run.query.filter(scope=domain))


def test_usage_is_visible_in_every_requested_view(app_session):
    run = Run.query.filter(status='success').order_by('-id').first()
    assert run
    for url in ('/?view=runs', '/?view=personas', '/?view=sites', '/?view=checks', '/?view=agents', f'/runs/{run.id}/tree'):
        response = app_session.get(url)
        assert response.status_code == 200, url
        assert 'Tokens' in response.text, url
        assert 'data-tokens=' in response.text, url
        assert 'Unknown' not in response.text
