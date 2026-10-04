"""Assert real agent/browser recovery evidence; run the local E2E command first."""

import json
import os
from pathlib import Path

from app.core.models import Checkpoint, CheckRun, Leader, Persona, Run
from app.core.recovery.redaction import read
from app.core.storage import data_root, read_state


def test_real_email_login_and_fresh_browser_verification():
    recovery = Run.query.get(id=int(os.environ["ACCOUNT_CHECKER_RECOVERY_RUN"]))
    assert recovery.runtime["provider_kind"] == "local"
    assert recovery.persona.name == "Test researcher"
    assert recovery.checked_in_at and recovery.tip and not recovery.issues
    assert not recovery.promoted  # Submitting credentials alone must never promote.
    assert read_state(recovery.base.digest)["cookies"] == []
    verified = Run.query.filter(runtime__recovery_verification=recovery.id).get()
    assert verified.base.digest == recovery.tip
    assert verified.status == "success" and verified.promoted and not verified.issues
    assert verified.runtime["pid"] != recovery.runtime["pid"]
    checks = list(CheckRun.query.filter(run=verified))
    assert len(checks) == len(verified.plan) > 0
    for check in checks:
        assert check.passed and check.state == "accessible"
        assert Path(check.screenshot).is_file()
        assert check.evidence["programs"]
        assert all(p["exit_code"] == 0 for p in check.evidence["programs"])
    leader = Leader.query.get(persona=recovery.persona)
    assert leader.verified and leader.checkpoint.digest == verified.tip
    state = read_state(verified.tip)
    assert state["cookies"] and all(c["domain"] == "127.0.0.1" for c in state["cookies"])
    assert Persona.query.count() == 1
    assert Checkpoint.query.filter(persona=recovery.persona).count() >= 3


def test_recovery_artifacts_and_inference_hide_known_secret_values():
    run_id = int(os.environ["ACCOUNT_CHECKER_RECOVERY_RUN"])
    directory = data_root() / "runs" / str(run_id)
    values = list(read(directory).values())
    assert values, "Real email retrieval must have delivered a secret locally"
    actions = [json.loads(line) for line in (directory / "recovery-actions.jsonl").read_text().splitlines()]
    submitted = [a["result"] for a in actions if a["tool"] == "act"]
    assert submitted and all(r["status"] == "completed" for r in submitted)
    assert sum(r["privacy"]["inferenceRequests"] for r in submitted) > 0
    assert all(r["privacy"]["knownSecretMatchesAfterRedaction"] == 0 for r in submitted)
    transcript = (directory / "transcript-recovery.json").read_text()
    assert "private_login_act" in transcript and "browser_harness_execute_python" in transcript
    assert "browser_harness_view_screenshot" in transcript
    assert not json.loads((directory / "recovery-context.json").read_text())["active"]
    assert not (directory / "live.jpg").exists(), "Recovery uses masked step images"
    for path in directory.rglob("*"):
        if path.is_file() and "personas" not in path.relative_to(directory).parts:
            contents = path.read_bytes()
            assert not any(v.encode() in contents for v in values), (
                f"A raw secret reached an artifact: {path.relative_to(directory)}"
            )
