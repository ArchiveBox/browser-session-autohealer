"""Session pages reflect retained real fixes and their in-session verification."""

import pytest

from app.core.models import CheckRun, Run


@pytest.mark.parametrize("run_id", [81, 82, 83])
def test_session_shows_recorded_task_results_and_disposition(app_session, run_id):
    run = Run.query.get(id=run_id)
    results = list(CheckRun.query.filter(run=run))
    assert results and run.checked_in_at
    response = app_session.get(f"/runs/{run_id}")
    assert response.status_code == 200
    assert "This browser still needs help" not in response.text
    assert f"Session #{run_id}</h1>" in response.text
    assert 'class="session-facts"' in response.text
    assert run.provider.name in response.text
    if not run.promoted:
        assert ">Discarded</span>" in response.text
    for result in results:
        assert f'id="check-{result.check_id}"' in response.text
        if result.screenshot:
            assert f'/evidence/{run_id}/{result.evidence["screenshot"]}' in response.text
        assert (">Passed</span>" if result.passed else ">Not passed</span>") in response.text


def test_successful_recovery_verification_has_its_own_focused_result(app_session):
    run = Run.query.get(id=82)
    assert run.status == "success" and run.promoted and not run.issues
    results = list(CheckRun.query.filter(run=run))
    assert {r.check_id for r in results} == {19, 20}
    assert all(r.passed for r in results)
    response = app_session.get("/runs/82/checks/19")
    assert response.status_code == 200
    assert 'data-access-summary="accessible"' in response.text
    assert 'id="check-19"' in response.text
    assert 'id="check-20"' not in response.text
    assert response.text.count('class="agent-session"') == 1
    assert "This browser still needs help" not in response.text
