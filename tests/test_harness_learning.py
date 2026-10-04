"""Evidence from the real agent's failed helper, self-repair, and two replays."""
import hashlib
import os

from app.core.models import CheckRun, CheckRunStep, CheckType, Run
from app.core.services import restore_agent_helpers


def test_agent_repairs_and_replays_its_versioned_helper(tmp_path):
    run = Run.query.get(id=int(os.environ.get('ACCOUNT_CHECKER_LEARNING_RUN', '77')))
    result = CheckRun.query.get(run=run, check_id=9)
    steps = list(CheckRunStep.query.filter(check_run=result).order_by('position'))
    attempts = [s for s in steps if s.inputs.get('helper_code')]
    assert any(s.outputs['exit_code'] != 0 for s in attempts)
    final = attempts[-2:]
    assert len(final) == 2 and all(s.outputs['exit_code'] == 0 for s in final)
    assert final[0].inputs['helper_code'] == final[1].inputs['helper_code']
    assert final[0].step != final[1].step
    for step in final:
        code = step.inputs['helper_code']
        assert hashlib.sha256(code.encode()).hexdigest() == step.outputs['helper_digest']
        version = CheckType.query.get(uid=step.inputs['helper_revision'])
        assert version.code == code and version.check_id == result.check_id
        assert "'passed': True" in step.outputs['stdout']
    assert result.passed and result.screenshot
    # Learning can continue after an error without silently adopting that session.
    assert run.issues and not run.promoted
    restore_agent_helpers(result.check_id, tmp_path)
    assert (tmp_path / 'agent_helpers.py').read_text() == final[-1].inputs['helper_code']
