"""Acceptance against this installation's real, retained browser-check history.

No records or browser artifacts are fabricated. Trigger probes target existing
facts inside transactions that always roll back, including when a trigger fails
its assertion. Historical run IDs are the same retained acceptance collection
used by test_live.py and test_health_ui.py.
"""

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
from plain.postgres import get_connection, transaction
from psycopg import errors, sql

from app.core.models import Check, CheckEvent, CheckRun, CheckRunStep, CheckType, Run
from app.core.services import check_projection


@pytest.mark.parametrize("model", [CheckEvent, CheckType, CheckRunStep])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_existing_history_facts_reject_update_and_delete(model, operation):
    row = model.query.order_by("id").first()
    assert row is not None, "Real retained history is required"
    before = model.query.filter(id=row.id).values().get()
    table = sql.Identifier(model.model_options.db_table)
    statement = (
        sql.SQL("UPDATE {} SET id = id WHERE id = %s").format(table)
        if operation == "UPDATE"
        else sql.SQL("DELETE FROM {} WHERE id = %s").format(table)
    )
    with (
        pytest.raises(errors.RaiseException, match="Check history is append-only"),
        transaction.atomic(),
    ):
        with get_connection().cursor() as cursor:
            cursor.execute(statement, [row.id])
        # If the trigger is absent, raising here still rolls back the probe.
        pytest.fail(f"{model.__name__} accepted {operation} of retained history")
    after = model.query.filter(id=row.id).values().get()
    assert bool(after == before), "The trigger probe changed a retained fact"


def test_retained_source_inputs_and_outputs_match_actual_artifact_files():
    steps = list(CheckRunStep.query.order_by("id"))
    assert steps, "Real browser-harness executions must already be retained"
    versions = {row.id: row for row in CheckType.query.all()}
    events = {event.payload["id"]: event for event in CheckEvent.query.filter(kind="step_finished")}
    # Actual execution links, rather than code digests alone, preserve repeated runs.
    relations = {
        row["id"]: row
        for row in CheckRunStep.query.values("id", "check_type__id", "check_run__uid")
    }
    saw_successful_program = False
    for step in steps:
        source, output = Path(step.source_path), Path(step.output_path)
        assert source.is_file() and output.is_file(), f"Missing artifacts for step {step.uid}"
        version = versions[relations[step.id]["check_type__id"]]
        actual_output = json.loads(output.read_text())
        assert bool(version.code.encode() == source.read_bytes()), f"Source differs: {step.uid}"
        assert bool(step.inputs["code"] == version.code), f"Input differs: {step.uid}"
        assert bool(step.outputs == actual_output), f"Output differs: {step.uid}"
        assert step.inputs["purpose"] == actual_output["purpose"]
        assert version.lang == "python"
        expected_digest = hashlib.sha256(b"python\0" + source.read_bytes()).hexdigest()
        assert version.digest == expected_digest
        event = events[str(step.uid)]
        assert event.check_run_uid == relations[step.id]["check_run__uid"]
        assert event.payload["check_type_id"] == str(version.uid)
        assert bool(event.payload["inputs"] == step.inputs), f"Event input differs: {step.uid}"
        assert bool(event.payload["outputs"] == actual_output), f"Event output differs: {step.uid}"
        assert event.payload["source_path"] == str(source)
        assert event.payload["output_path"] == str(output)
        assert step.started_at and step.ended_at and step.started_at <= step.ended_at
        saw_successful_program |= actual_output.get("exit_code") == 0
    assert saw_successful_program, "Acceptance requires successfully executed real browser code"


def test_as_of_projection_does_not_invent_pre_adoption_history():
    adoptions = list(CheckEvent.query.filter(kind="definition_observed").order_by("id"))
    assert adoptions, "Existing checks must have recorded adoption facts"
    for adoption in adoptions:
        first = (
            CheckEvent.query.filter(check_uid=adoption.check_uid)
            .order_by("occurred_at", "id")
            .first()
        )
        before = check_projection(adoption.check_uid, first.occurred_at - timedelta(microseconds=1))
        assert before == {
            "definition": None,
            "runs": {},
            "steps": {},
            "versions": {},
            "current_type": None,
        }
        at_adoption = check_projection(adoption.check_uid, adoption.occurred_at)
        assert bool(at_adoption["definition"] == adoption.payload)
        assert adoption.payload["provenance"] == "existing definition observed during migration"
        # Old executions may be known now, but their earlier timestamps are not
        # evidence that this event log already existed at those earlier times.
        historical = (
            CheckEvent.query.filter(check_uid=adoption.check_uid, kind="execution_observed")
            .order_by("occurred_at", "id")
            .first()
        )
        assert historical is not None
        at_execution = check_projection(adoption.check_uid, historical.occurred_at)
        assert bool(at_execution["runs"][str(historical.check_run_uid)] == historical.payload)
        just_before = check_projection(
            adoption.check_uid, historical.occurred_at - timedelta(microseconds=1)
        )
        assert str(historical.check_run_uid) not in just_before["runs"]


def test_recovered_older_source_does_not_replace_the_current_source_version():
    recovered = list(CheckEvent.query.filter(kind="type_observed").order_by("occurred_at", "id"))
    assert recovered, "Acceptance requires the genuinely recovered older source artifacts"
    compared = 0
    for event in recovered:
        before = check_projection(event.check_uid, event.occurred_at - timedelta(microseconds=1))
        if before["current_type"] is None:
            continue
        after = check_projection(event.check_uid, event.occurred_at)
        assert after["current_type"]["id"] == before["current_type"]["id"]
        assert event.payload["id"] in after["versions"]
        assert event.payload["provenance"] == "retained historical source"
        compared += 1
    assert compared, "No recovered historical source was compared with an existing current version"


@pytest.mark.parametrize(
    ("run_id", "expected"),
    [
        (10, [(1, True, "accessible"), (2, False, "human_required"), (3, True, "accessible")]),
        (12, [(4, False, "login_required")]),
        (16, [(1, True, "accessible"), (2, True, "accessible"), (3, True, "accessible")]),
        (17, [(1, True, "accessible"), (2, True, "accessible"), (3, True, "accessible")]),
        (18, [(1, True, "accessible"), (2, True, "accessible"), (3, True, "accessible")]),
        (20, [(1, True, "accessible"), (2, True, "accessible"), (3, True, "accessible")]),
    ],
)
def test_real_historical_outcomes_survive_adoption(run_id, expected):
    executions = list(CheckRun.query.filter(run__id=run_id).order_by("check_id"))
    assert [(r.check_id, r.passed, r.state) for r in executions] == expected
    checks = {c.id: c for c in Check.query.filter(id__in=[r.check_id for r in executions])}
    for execution in executions:
        assert execution.status == ("success" if execution.passed else "failure")
        assert execution.ended_at == execution.created_at
        assert "original result recorded time" in execution.timing_source
        projected = check_projection(checks[execution.check_id].uid)["runs"][str(execution.uid)]
        assert projected["passed"] == execution.passed
        assert projected["state"] == execution.state
        assert projected["status"] == execution.status
        assert projected["ended_at"] == execution.ended_at.isoformat()
        assert Path(execution.raw_output).is_file()
    if run_id == 20:
        # Passing individual checks must not rewrite the cookie-preservation failure.
        checkout = Run.query.get(id=run_id)
        assert checkout.status == "failed" and not checkout.promoted
        assert any(
            "cookies outside the checked site" in issue["message"] for issue in checkout.issues
        )


@pytest.mark.parametrize("run_id", [23, 24])
def test_new_real_executions_replay_from_running_to_terminal(run_id):
    executions = list(CheckRun.query.filter(run__id=run_id))
    assert len(executions) == (3 if run_id == 23 else 1)
    for execution in executions:
        events = list(CheckEvent.query.filter(check_run_uid=execution.uid).order_by("id"))
        start = next(e for e in events if e.kind == "execution_started")
        finish = next(e for e in events if e.kind == "execution_finished")
        assert start.payload["status"] == "running"
        assert finish.payload["status"] == ("success" if run_id == 23 else "failure")
        assert start.occurred_at < finish.occurred_at
        assert execution.started_at < execution.ended_at
        assert execution.timing_source == "worker clock"
        for event in (start, finish):
            assert event.payload["inputs"]["condition"]["instruction"]
            assert event.payload["inputs"]["inference"]["model"] == "openai/gpt-6.1-sol"
            projected = check_projection(event.check_uid, event.occurred_at)["runs"][
                str(execution.uid)
            ]
            assert projected == event.payload
        assert (
            str(execution.uid)
            not in check_projection(start.check_uid, start.occurred_at - timedelta(microseconds=1))[
                "runs"
            ]
        )
        assert Path(execution.screenshot).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
