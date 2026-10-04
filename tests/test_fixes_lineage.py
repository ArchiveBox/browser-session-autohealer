"""Actual session forks, reference changes, task forms and rule configuration."""
from itertools import pairwise

from app.core.models import Check, CheckRun, Leader, LeaderChange, Persona, Run, TaskRule
from app.lineage import graph


def test_fixes_share_task_forms_and_rules(app_session):
    response = app_session.get('/fixes', follow_redirects=True)
    assert response.status_code == 200
    assert '<h1>Tasks</h1>' in response.text
    fixes = list(Check.query.filter(mode='fix'))
    assert fixes
    for fix in fixes:
        response = app_session.get(f'/edit/check?id={fix.id}')
        assert response.status_code == 200
        assert fix.instruction in response.text
        assert 'value="fix" selected' in response.text
        assert TaskRule.query.filter(source=fix.source_task, target=fix, state='login_required').exists()
        assert TaskRule.query.filter(source=fix, target=fix.source_task, status='success').exists()
    response = app_session.get('/tasks/rules')
    assert response.status_code == 200
    assert 'login_required' in response.text


def test_lineage_uses_recorded_reference_history(app_session):
    for persona in Persona.query.all():
        data = graph(persona)
        changes = list(LeaderChange.query.filter(persona=persona).order_by('created_at', 'id'))
        assert len(changes) >= 3
        points = {e['key']: e for e in data['events']}
        assert changes[-1].checkpoint == Leader.query.get(persona=persona).checkpoint
        main = [e for e in data['edges'] if e['kind'] == 'main']
        assert len(main) == len(changes) - 1
        for previous, change in pairwise(changes):
            assert change.previous == previous.checkpoint
            assert any(e['from'] == f'leader-{previous.id}' and e['to'] == f'leader-{change.id}' for e in main)
        response = app_session.get(f'/personas/{persona.id}/lineage')
        assert response.status_code == 200
        for run in Run.query.filter(persona=persona):
            fork = next(e for e in data['edges'] if e['kind'] == 'fork' and e['run'] == run.id)
            prior = [c for c in changes if c.checkpoint == run.base and c.created_at <= run.created_at]
            assert fork['from'] == f'leader-{prior[-1].id}'
            assert fork['to'] == f'start-{run.id}'
            assert points[fork['from']]['lane'] == 0
            branch = [e for e in data['edges'] if e['kind'] == 'session' and e['run'] == run.id]
            if run.checked_in_at:
                assert all(points[e['to']]['at'] <= run.checked_in_at for e in branch)
            if run.promoted:
                adopted = next(c for c in changes if c.run == run)
                assert branch[-1]['to'] == f'leader-{adopted.id}'
            assert f'data-run="{run.id}"' in response.text
        for result in CheckRun.query.filter(run__persona=persona):
            assert f'/runs/{result.run.id}/checks/{result.check_id}' in response.text
        assert 'diff-removed' in response.text


def test_real_fix_and_verification_share_one_session():
    from app.core.tasks import dispatch

    run = Run.query.filter(runtime__recovery__isnull=False, status='success').order_by('-id').first()
    assert run and run.promoted
    results = list(CheckRun.query.filter(run=run).order_by('created_at'))
    assert [Check.query.get(id=r.check_id).mode for r in results] == ['fix', 'check']
    assert all(r.passed and r.screenshot for r in results)
    assert run.runtime['completed_rules']
    before = Run.query.count()
    assert dispatch(run) == dispatch(run) == []
    assert Run.query.count() == before
    session = next(s for s in graph(run.persona)['sessions'] if s['run'] == run)
    assert len(session['tasks']) == 2 and session['adopted']


def test_real_interactive_session_checks_final_state_and_is_adopted(app_session):
    from datetime import datetime

    run = Run.query.filter(runtime__interactive=True).order_by('-id').first()
    assert run and run.checked_in_at and run.promoted, run.promotion_reason
    closed = datetime.fromisoformat(run.runtime['interaction_ended_at'])
    results = list(CheckRun.query.filter(run=run))
    assert len(results) == len(run.plan)
    assert all(r.passed and r.screenshot and closed <= r.started_at < r.ended_at <= run.finished_at for r in results)
    assert not run.runtime['interactive_ready']
    assert app_session.get(f'/runs/{run.id}/browser-status').json()['ready'] is False
    response = app_session.post(f'/runs/{run.id}/browser', json={'operation': 'select'},
                                headers={'X-Account-Checker': 'browser-control'})
    assert response.status_code == 409
    data = graph(run.persona)
    session = next(s for s in data['sessions'] if s['run'] == run)
    assert session['adopted'] and not session['active']
    change = LeaderChange.query.get(run=run)
    final_edge = [e for e in data['edges'] if e.get('run') == run.id][-1]
    assert final_edge['to'] == f'leader-{change.id}'
