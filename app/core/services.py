"""The checkout/check-in contract shared by UI, CLI, and the worker."""

import hashlib
import json
import re
import socket
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from plain.postgres import transaction

from . import storage
from .models import (
    Account,
    Check,
    CheckEvent,
    Checkpoint,
    CheckRun,
    CheckRunStep,
    CheckType,
    Leader,
    LeaderChange,
    Persona,
    Provider,
    Run,
)


def now():
    return datetime.now(UTC)


def make_checkpoint(
    persona, state, *, parent="", branch="", message, actor, source=None, coverage=None, native=None
):
    coverage = coverage or {
        "cookies": "complete",
        "settings": "complete",
        "origins": "partial",
        "native": bool(native),
    }
    manifest = {
        "persona_id": persona.id,
        "parent": parent,
        "branch": branch,
        "message": message,
        "actor": actor,
        "source": source or {},
        "coverage": coverage,
        "nonce": uuid.uuid4().hex,
        "created_at": now().isoformat(),
    }
    digest = storage.write_object(manifest, state, native)
    changes = storage.compare(parent, digest) if parent else []
    summary = {
        "cookies": len(state.get("cookies", [])),
        "settings": len(state.get("settings", {})),
        "origins": len(state.get("origins", [])),
        "changes": {},
    }
    for item in changes:
        key = f"{item['category']}_{item['kind']}"
        summary["changes"][key] = summary["changes"].get(key, 0) + 1
    return Checkpoint.query.create(
        persona=persona,
        digest=digest,
        parent=parent,
        branch=branch,
        message=message,
        actor=actor,
        source=source or {},
        coverage=coverage,
        summary=summary,
    )


def create_persona(name, config=None, description="", actor="local"):
    if not name.strip():
        raise ValueError("A persona needs a name")
    persona = Persona.query.create(name=name.strip(), config=config or {}, description=description)
    cp = make_checkpoint(
        persona,
        {"cookies": [], "settings": persona.config, "origins": []},
        message="Persona created",
        actor=actor,
    )
    Leader.query.create(persona=persona, checkpoint=cp)
    LeaderChange.query.create(persona=persona, checkpoint=cp, kind='initial', actor=actor)
    return persona


def leader_for(persona):
    return Leader.query.get(persona=persona)


def import_state(persona, state, actor, source, native=None, coverage=None):
    base = leader_for(persona)
    cp = make_checkpoint(
        persona,
        state,
        parent=base.checkpoint.digest,
        message="Profile imported",
        actor=actor,
        source=source,
        native=native,
        coverage=coverage,
    )
    with transaction.atomic():
        Persona.query.for_update().get(id=persona.id)
        leader = leader_for(persona)
        # Imports bootstrap only an unverified base; preserve verified leaders.
        if not leader.verified:
            LeaderChange.query.create(persona=persona, checkpoint=cp, previous=leader.checkpoint, kind='import', actor=actor)
            leader.checkpoint = cp
            leader.update(fields=["checkpoint"])
    return cp


def check_plan(check):
    return {
        "id": check.id,
        "uid": str(check.uid),
        "name": check.name,
        "url": check.url,
        "domain": check.account.site.domain,
        "instruction": check.instruction,
        "mode": check.mode,
        "username": check.account.username,
    }


@transaction.atomic()
def checkout(
    persona_id, provider_id, scope, actor, check_ids=None, external=False, base_digest=None
):
    from .inference import config as inference_config

    persona = Persona.query.for_update().get(id=persona_id)
    provider = Provider.query.get(id=provider_id)
    base = (
        Checkpoint.query.get(persona=persona, digest=base_digest)
        if base_digest
        else leader_for(persona).checkpoint
    )
    base_event = LeaderChange.query.filter(persona=persona, checkpoint=base).order_by('-created_at').first()
    if not provider.enabled:
        raise ValueError("Provider is disabled")
    settings = dict(persona.config)
    if provider.site_scope is not None:
        from .site_scope import matches, normalize_sites
        allowed = normalize_sites(provider.site_scope)
        configured = normalize_sites(settings.get('siteScope'))
        sites = [s for s in dict.fromkeys([*(configured or []), *allowed])
                 if matches(s, configured) and matches(s, allowed)]
        settings['siteScope'] = normalize_sites(sites)
        if scope != '*' and not matches(scope, sites):
            raise ValueError('This site is not enabled for this provider')
        if base.coverage.get('cookies') == 'native-only':
            raise ValueError('Import portable site data before using a restricted provider')
    checks = list(Check.query.filter(account__persona=persona, provider=provider, enabled=True))
    if not external and (scope == "*" or check_ids is None or len(check_ids) > 1):
        raise ValueError("Choose one check on one site for this persona")
    if scope != "*":
        if not Account.query.filter(persona=persona, site__domain=scope).exists():
            raise ValueError("Scope must be an account belonging to this persona")
        checks = [c for c in checks if c.account.site.domain == scope]
    required_check_ids = [c.id for c in checks if c.mode == "check"]
    if check_ids is not None:
        selected = [c for c in checks if c.id in check_ids]
        if len(selected) != len(set(check_ids)):
            raise ValueError("Check does not belong to the declared scope")
        checks = selected
    plan = [check_plan(c) for c in checks]
    if not external:
        for check in checks:
            check.next_due = now() + timedelta(seconds=check.interval_seconds)
            check.update(fields=["next_due"])
            CheckEvent.query.create(
                check_uid=check.uid,
                kind="schedule_changed",
                actor=actor,
                payload={"next_due": check.next_due.isoformat()},
            )
    runtime_fix = {}
    if len(checks) == 1 and checks[0].mode == "fix":
        from .tasks import fix_binding
        runtime_fix = {"recovery": {"account_id": checks[0].account.id, "check_id": checks[0].id,
            "task": plan[0], "binding": fix_binding(checks[0])}}
    return Run.query.create(
        persona=persona,
        provider=provider,
        base=base,
        scope=scope,
        actor=actor,
        plan=plan,
        status="running" if external else "queued",
        started_at=now() if external else None,
        runtime={
            'base_leader_event_id': base_event.id if base_event else None,
            'base_requested': bool(base_digest),
            **runtime_fix,
            "settings": settings,
            "provider_site_scope": provider.site_scope,
            "provider_config": provider.config,
            "provider_kind": provider.kind,
            "external": external,
            "inference": inference_config(),
            "account_scopes": [a.site.domain for a in Account.query.filter(persona=persona)],
            "required_check_ids": required_check_ids,
        },
    )


def record_issue(run_id, message):
    with transaction.atomic():
        run = Run.query.for_update().get(id=run_id)
        if run.checked_in_at:
            raise ValueError("Run is already checked in")
        run.issues = [*run.issues, {"at": now().isoformat(), "message": message}]
        run.update(fields=["issues"])


def record_definition(check, actor, *, observed=False):
    """Call in the same transaction as the definition write."""
    return CheckEvent.query.create(
        check_uid=check.uid,
        kind="definition_observed" if observed else "definition_saved",
        actor=actor,
        payload={
            "id": str(check.uid),
            "internal_id": check.id,
            "name": check.name,
            "instruction": check.instruction,
            "url": check.url,
            "enabled": check.enabled,
            "mode": check.mode,
            "pattern": check.pattern,
            "source_task_id": check.source_task.id if check.source_task else None,
            "interval_seconds": check.interval_seconds,
            "next_due": check.next_due.isoformat() if check.next_due else None,
            "account_id": check.account.id,
            "provider_id": check.provider.id,
            "provenance": "existing definition observed during migration"
            if observed
            else "definition saved",
        },
    )


def check_projection(check_uid, as_of=None):
    """Derive known state at a log boundary; pre-adoption history is never invented."""
    events = CheckEvent.query.filter(check_uid=check_uid)
    if as_of is not None:
        events = events.filter(occurred_at__lte=as_of)
    result = {"definition": None, "runs": {}, "steps": {}, "versions": {}, "current_type": None}
    for event in events.order_by("occurred_at", "id"):
        if event.kind.startswith("definition_"):
            result["definition"] = event.payload
        elif event.kind.startswith("schedule_"):
            result["definition"] = {
                **(result["definition"] or {"id": str(check_uid)}),
                "next_due": event.payload["next_due"],
            }
        elif event.kind in {"type_created", "type_observed"}:
            revision = {
                **event.payload,
                "observed_at": event.occurred_at.isoformat(),
                "historical": event.kind == "type_observed",
            }
            result["versions"][revision["id"]] = revision
            if event.kind == "type_created" or result["current_type"] is None:
                if result["current_type"]:
                    result["current_type"]["effective_until"] = event.occurred_at.isoformat()
                revision["effective_from"] = event.occurred_at.isoformat()
                revision["effective_until"] = None
                result["current_type"] = revision
        elif event.kind == "step_finished":
            result["steps"][event.payload["id"]] = {
                **event.payload,
                "check_run_id": str(event.check_run_uid),
            }
            revision = result["versions"].get(event.payload["check_type_id"])
            source_time = event.payload.get("started_at")
            if revision and source_time:
                revision["source_started_at"] = source_time
                current = result["current_type"]
                # An observed archival revision becomes current only if its
                # actual source fact is newer than what is already known.
                if (
                    revision["historical"]
                    and current
                    and revision is not current
                    and source_time > current.get("source_started_at", current["created_at"])
                ):
                    current["effective_until"] = event.occurred_at.isoformat()
                    revision["effective_from"] = event.occurred_at.isoformat()
                    revision["effective_until"] = None
                    result["current_type"] = revision
        elif event.check_run_uid:
            result["runs"][str(event.check_run_uid)] = event.payload
    return result


def reconcile_check_schedules():
    """Record schedules observed now; earlier unlogged changes remain unknown."""
    for check_id in list(Check.query.values_list("id", flat=True)):
        with transaction.atomic():
            check = Check.query.for_update().get(id=check_id)
            value = check.next_due.isoformat() if check.next_due else None
            definition = check_projection(check.uid)["definition"] or {}
            if "next_due" not in definition or definition["next_due"] != value:
                CheckEvent.query.create(
                    check_uid=check.uid,
                    kind="schedule_observed",
                    actor="history import",
                    payload={
                        "next_due": value,
                        "provenance": "current schedule observed during log adoption",
                    },
                )


def check_artifacts(run_id, plan):
    work = storage.data_root() / "runs" / str(run_id)
    return work, work / "agent-workspace", work / f"transcript-{plan['id']}.json"


def record_check_run(check_run, kind, actor):
    check = Check.query.get(id=check_run.check_id)
    run = check_run.run
    condition = next((plan for plan in run.plan if plan["id"] == check_run.check_id), None)
    return CheckEvent.query.create(
        check_uid=check.uid,
        check_run_uid=check_run.uid,
        kind=kind,
        actor=actor,
        payload={
            "id": str(check_run.uid),
            "check_id": str(check.uid),
            "checkout_id": check_run.run.id,
            "inputs": {
                "condition": condition,
                "provider_kind": run.runtime.get("provider_kind"),
                "provider_config": run.runtime.get("provider_config"),
                "inference": run.runtime.get("inference"),
                "browser_settings": run.runtime.get("settings"),
                "base_checkpoint": run.base.digest,
                "scope": run.scope,
                "frozen_at": run.created_at.isoformat(),
                "provenance": "frozen checkout plan and runtime; browser state is addressed by checkpoint digest",
            },
            "started_at": check_run.started_at.isoformat() if check_run.started_at else None,
            "ended_at": check_run.ended_at.isoformat() if check_run.ended_at else None,
            "timing_source": check_run.timing_source,
            "status": check_run.status,
            "state": check_run.state,
            "passed": check_run.passed,
            "artifact_dir": check_run.artifact_dir,
            "screenshot": check_run.screenshot,
            "raw_output": check_run.raw_output,
            "summary": check_run.summary,
            "error": check_run.error,
            "evidence": check_run.evidence,
            "classification": check_run.classification,
        },
    )


def start_check(run_id, plan):
    """Record the condition execution before opening its browser tab or agent."""
    work, _, _ = check_artifacts(run_id, plan)
    with transaction.atomic():
        run = Run.query.for_update().get(id=run_id)
        if run.checked_in_at or run.status != "running" or plan not in run.plan:
            raise ValueError("Cannot start an undeclared check on an inactive run")
        check_run = CheckRun.query.create(
            run=run,
            check_id=plan["id"],
            started_at=now(),
            timing_source="worker clock",
            status="running",
            artifact_dir=str(work),
            evidence={"title": plan["name"]},
        )
        record_check_run(check_run, "execution_started", run.actor)
    return check_run


def reconcile_check_inputs():
    """Append missing frozen-input observations without rewriting earlier facts."""
    for check_run in list(CheckRun.query.all()):
        last = (
            CheckEvent.query.filter(
                check_run_uid=check_run.uid,
                kind__in=[
                    "execution_started",
                    "execution_finished",
                    "execution_observed",
                    "execution_inputs_observed",
                ],
            )
            .order_by("-id")
            .first()
        )
        if last and "inputs" not in last.payload:
            record_check_run(check_run, "execution_inputs_observed", "history import")


def version_check_type(
    check=None, *, name, lang, code, author, domain=None, previous=None, check_run=None, source_path="", observed=False
):
    """Append a hand-authored or agent-authored source revision and its event."""
    from .domain_patterns import matches, normalize

    domain = normalize(domain or (check.account.site.domain if check else '*'))
    if check and not matches(domain, check.account.site.domain):
        raise ValueError('The domain pattern must match the linked check site')
    digest = hashlib.sha256(lang.encode() + b"\0" + code.encode()).hexdigest()
    with transaction.atomic():
        if check:
            Check.query.for_update().get(id=check.id)
            previous = previous or CheckType.query.filter(check_id=check.id).order_by("-id").first()
        if previous and previous.check_id == (check.id if check else None) and previous.digest == digest and previous.domain == domain and previous.name == name:
            return previous
        version = CheckType.query.create(
            check_id=check.id if check else None,
            domain=domain,
            previous=previous,
            digest=digest,
            name=name,
            lang=lang,
            code=code,
            author=author,
        )
        if not check:
            return version
        CheckEvent.query.create(
            check_uid=check.uid,
            check_run_uid=check_run.uid if check_run else None,
            kind="type_observed" if observed else "type_created",
            actor=author,
            payload={
                "id": str(version.uid),
                "name": name,
                "domain": domain,
                "lang": lang,
                "code": code,
                "digest": digest,
                "author": author,
                "previous_id": str(previous.uid) if previous else None,
                "created_at": version.created_at.isoformat(),
                "source_path": source_path,
                "provenance": "retained historical source"
                if observed
                else "source version recorded",
            },
        )
    return version


def restore_agent_helpers(check_id, artifacts):
    """Seed the agent's candidate from its existing immutable execution history."""
    previous = CheckRunStep.query.filter(check_run__check_id=check_id,
        inputs__helper_revision__isnull=False).exclude(inputs__helper_code='').order_by('-id').first()
    if previous:
        (artifacts / 'agent_helpers.py').write_text(previous.inputs['helper_code'])


def retain_programs(check_run, plan, programs, *, observed=False):
    """Append exact program versions and invocations; read files outside DB transactions."""
    _, artifacts, _ = check_artifacts(check_run.run.id, plan)
    check = Check.query.get(id=plan["id"])
    actor = "access:" + check_run.classification.get("session_id", "unknown-session")
    retained = []
    for position, program in enumerate(programs):
        entry = dict(program)
        step = str(program.get("step", ""))
        if not re.fullmatch(r"[0-9a-f]{32}", step):
            retained.append(entry)
            continue
        source, output = artifacts / f"{step}.py", artifacts / f"{step}.json"
        if not source.is_file() or not output.is_file():
            retained.append(entry)
            continue
        code = source.read_text()
        outputs = json.loads(output.read_text())
        helper_source = artifacts / f'{step}.helpers.py'
        helper_code = helper_source.read_text() if helper_source.is_file() else ''
        started_at = datetime.fromtimestamp(source.stat().st_mtime, UTC)
        ended_at = datetime.fromtimestamp(output.stat().st_mtime, UTC)
        with transaction.atomic():
            # One condition's code versions form one append-only chain.
            Check.query.for_update().get(id=check.id)
            existing = CheckRunStep.query.filter(check_run=check_run, step=step).first()
            if existing:
                entry["check_type_uid"] = str(existing.check_type.uid)
                entry["check_run_step_uid"] = str(existing.uid)
                retained.append(entry)
                continue
            helper_version = version_check_type(
                check, name=plan['name'] + ' · browser helpers', lang='python',
                code=helper_code, author=actor, check_run=check_run,
                source_path=str(helper_source), observed=observed,
            ) if helper_code else None
            version = version_check_type(
                check,
                name=program.get("purpose") or plan["name"],
                lang="python",
                code=code,
                author=actor,
                check_run=check_run,
                source_path=str(source),
                observed=observed,
            )
            execution = CheckRunStep.query.create(
                check_run=check_run,
                check_type=version,
                position=position,
                step=step,
                started_at=started_at,
                ended_at=ended_at,
                inputs={
                    "code": code,
                    "helper_code": helper_code,
                    "helper_revision": str(helper_version.uid) if helper_version else None,
                    "purpose": outputs.get("purpose", program.get("purpose", "")),
                },
                outputs=outputs,
                source_path=str(source),
                output_path=str(output),
            )
            CheckEvent.query.create(
                check_uid=check.uid,
                check_run_uid=check_run.uid,
                kind="step_finished",
                actor=actor,
                payload={
                    "id": str(execution.uid),
                    "check_type_id": str(version.uid),
                    "position": position,
                    "step": step,
                    "started_at": started_at.isoformat(),
                    "ended_at": ended_at.isoformat(),
                    "timing_source": execution.timing_source,
                    "inputs": execution.inputs,
                    "outputs": outputs,
                    "source_path": str(source),
                    "output_path": str(output),
                },
            )
        entry["check_type_uid"] = str(version.uid)
        entry["check_run_step_uid"] = str(execution.uid)
        retained.append(entry)
    return retained


def observe(run_id, plan, result, classification=None, *, check_run=None):
    passed = (
        result.get("passed") is True
        and (classification or {}).get("state", "accessible") == "accessible"
    )
    work, artifacts, transcript = check_artifacts(run_id, plan)
    filename = result.get("screenshot", "")
    screenshot = ""
    if filename and Path(filename).name == filename:
        screenshot = next(
            (str(p) for p in (artifacts / filename, work / filename) if p.is_file()), ""
        )
    raw_output = str(transcript) if transcript.is_file() else ""
    # Validate the lifecycle before retaining artifacts, outside the write transaction.
    run = Run.query.get(id=run_id)
    if run.checked_in_at or run.status not in ("running", "finishing") or plan not in run.plan:
        raise ValueError("Cannot record an undeclared check on an inactive run")
    if check_run is None:
        # External result submissions do not establish an observed start time.
        check_run = CheckRun.query.create(run=run, check_id=plan["id"])
    check_run.classification = classification or {}
    programs = retain_programs(check_run, plan, result.get("programs", []))
    with transaction.atomic():
        run = Run.query.for_update().get(id=run_id)
        if run.checked_in_at:
            raise ValueError("Run ended while its check evidence was being retained")
        check_run = CheckRun.query.for_update().get(id=check_run.id, run=run, check_id=plan["id"])
        if check_run.status != "running":
            raise ValueError("Check execution already has a terminal result")
        check_run.passed = passed
        check_run.state = (
            "accessible" if passed else (classification or {}).get("state", "human_required")
        )
        check_run.evidence = {**result, "programs": programs}
        check_run.classification = classification or {}
        check_run.status = "success" if passed else "failure"
        check_run.ended_at = now()
        check_run.artifact_dir = str(work)
        check_run.screenshot = screenshot
        check_run.raw_output = raw_output
        check_run.summary = result.get("reason") or (classification or {}).get("reason", "")
        check_run.error = "\n".join(str(issue) for issue in result.get("issues", []))
        check_run.update()
        record_check_run(check_run, "execution_finished", run.actor)
        if not passed:
            run.issues = [
                *run.issues,
                {"at": now().isoformat(), "message": f"{plan['name']}: access check failed"},
            ]
            run.update(fields=["issues"])
    return check_run


def attempt_programs(check_run, plan):
    """Attribute artifacts to a recorded check ID or a bounded session interval."""
    work, artifacts, _ = check_artifacts(check_run.run.id, plan)
    lower = check_run.started_at.timestamp() if check_run.started_at else None
    later = [
        p.stat().st_mtime
        for p in work.glob("agent-*.json")
        if p.name != f"agent-{plan['id']}.json" and lower is not None and p.stat().st_mtime > lower
    ]
    upper = min(later) if later else float("inf")
    programs = []
    for path in sorted(artifacts.glob("*.json"), key=lambda p: p.stat().st_mtime):
        program = json.loads(path.read_text())
        if program.get("step") and (
            program.get("check_id") == plan["id"]
            or (
                program.get("check_id") is None
                and lower is not None
                and lower <= path.stat().st_mtime < upper
            )
        ):
            programs.append(
                {k: program[k] for k in ("step", "purpose", "exit_code") if k in program}
            )
    return programs


def fail_check(check_run, plan, message):
    """Retain a failed attempt even without a structured agent verdict."""
    work, artifacts, _ = check_artifacts(check_run.run.id, plan)
    metadata_path = work / f"agent-{plan['id']}.json"
    metadata = json.loads(metadata_path.read_text()) if metadata_path.is_file() else {}
    programs = attempt_programs(check_run, plan)
    screenshot = f"check-{plan['id']}.png"
    if not (artifacts / screenshot).is_file():
        screenshot = "failure.png" if (work / "failure.png").is_file() else ""
    return observe(
        check_run.run.id,
        plan,
        {
            "passed": False,
            "state": "human_required",
            "reason": message,
            "title": plan["name"],
            "programs": programs,
            "screenshot": screenshot,
            "issues": [message],
            "checks": [],
        },
        {**metadata, "state": "human_required", "reason": message},
        check_run=check_run,
    )


def backfill_check_history(models_registry=None, schema_editor=None):
    """Adopt retained source facts; migration-time observations are not past edits."""
    for check in list(Check.query.all()):
        if not CheckEvent.query.filter(check_uid=check.uid, kind="definition_observed").exists():
            record_definition(check, "history import", observed=True)
    # Retain interrupted sessions that never produced the old Observation row.
    for run in list(Run.query.order_by("created_at")):
        if run.status in {"queued", "starting", "running", "finishing"}:
            continue
        work = storage.data_root() / "runs" / str(run.id)
        for plan in run.plan:
            metadata_path = work / f"agent-{plan['id']}.json"
            if (
                not metadata_path.is_file()
                or CheckRun.query.filter(run=run, check_id=plan["id"]).exists()
            ):
                continue
            metadata = json.loads(metadata_path.read_text())
            reason = (
                "; ".join(issue["message"] for issue in run.issues)
                or "The session ended without a structured result"
            )
            CheckRun.query.create(
                run=run,
                check_id=plan["id"],
                status="failure",
                state="human_required",
                started_at=datetime.fromtimestamp(metadata_path.stat().st_mtime, UTC),
                timing_source="agent metadata file timestamp; check end unknown",
                artifact_dir=str(work),
                summary=reason,
                error=reason,
                classification={**metadata, "state": "human_required", "reason": reason},
                evidence={"title": plan["name"], "programs": []},
            )
    for check_run in list(CheckRun.query.order_by("run", "created_at", "id")):
        if CheckEvent.query.filter(
            check_run_uid=check_run.uid, kind__in=["execution_started", "execution_finished"]
        ).exists():
            continue
        plan = next((p for p in check_run.run.plan if p["id"] == check_run.check_id), None)
        if not plan:
            continue
        work, artifacts, transcript = check_artifacts(check_run.run.id, plan)
        retained_screenshot = artifacts / f"check-{plan['id']}.png"
        observed = CheckEvent.query.filter(
            check_run_uid=check_run.uid, kind="execution_observed"
        ).exists()
        recovered = attempt_programs(check_run, plan) if check_run.ended_at is None else []
        if (
            observed
            and len(recovered) <= CheckRunStep.query.filter(check_run=check_run).count()
            and (check_run.screenshot or not retained_screenshot.is_file())
        ):
            continue
        metadata_path = work / f"agent-{plan['id']}.json"
        # Original completed records have a real observation creation time.
        if check_run.status == "running":
            check_run.status = "success" if check_run.passed else "failure"
            check_run.ended_at = check_run.created_at
            check_run.started_at = (
                datetime.fromtimestamp(metadata_path.stat().st_mtime, UTC)
                if metadata_path.is_file()
                else None
            )
            check_run.timing_source = (
                "agent metadata file timestamp / original result recorded time"
                if metadata_path.is_file()
                else "start unknown / original result recorded time"
            )
        check_run.artifact_dir = str(work)
        check_run.raw_output = str(transcript) if transcript.is_file() else ""
        check_run.summary = check_run.evidence.get("reason") or check_run.classification.get(
            "reason", ""
        )
        check_run.error = check_run.error or "\n".join(
            str(x) for x in check_run.evidence.get("issues", [])
        )
        filename = check_run.evidence.get("screenshot", "")
        if not filename and check_run.ended_at is None and retained_screenshot.is_file():
            filename = retained_screenshot.name
            check_run.evidence = {**check_run.evidence, "screenshot": filename}
        if filename and Path(filename).name == filename:
            check_run.screenshot = next(
                (str(p) for p in (artifacts / filename, work / filename) if p.is_file()), ""
            )
        programs = check_run.evidence.get("programs", []) or recovered
        # Source file times establish the actual order, unlike random step IDs.
        programs = sorted(
            programs,
            key=lambda p: (
                (artifacts / (p["step"] + ".py")).stat().st_mtime
                if (artifacts / (p["step"] + ".py")).is_file()
                else 0
            ),
        )
        check_run.evidence = {
            **check_run.evidence,
            "programs": retain_programs(check_run, plan, programs, observed=observed),
        }
        with transaction.atomic():
            check_run.update()
            record_check_run(check_run, "execution_observed", "history import")


def checkpoint_run(run_id, state, native=None, coverage=None):
    run = Run.query.get(id=run_id)
    from .site_scope import checkin_state, select_state

    sites = run.runtime.get("settings", {}).get("siteScope")
    if sites is not None:
        state = select_state(state, sites)
        if run.runtime.get('provider_site_scope') is not None:
            state = checkin_state(storage.read_state(run.base.digest), state, sites)
        native = None  # Opaque native databases cannot enforce a per-site boundary.
        coverage = {**(coverage or {}), "native": False, "siteScope": sites}
    if run.checked_in_at:
        raise ValueError("Run is already checked in")
    cp = make_checkpoint(
        run.persona,
        state,
        parent=run.tip or run.base.digest,
        branch=str(run.id),
        message="Browser checkpoint",
        actor=run.actor,
        source={"provider": run.provider.name, "hostname": socket.gethostname(), "run": run.id},
        native=native,
        coverage=coverage,
    )
    with transaction.atomic():
        locked = Run.query.for_update().get(id=run.id)
        if locked.checked_in_at or locked.tip != run.tip:
            raise ValueError("Run changed while its snapshot was being saved; checkpoint retained")
        locked.tip = cp.digest
        locked.update(fields=["tip"])
    return cp


def finish(run_id, *, success, export_complete):
    """Use a server-recorded end time before upload; late uploads cannot win."""
    run = Run.query.get(id=run_id)
    with transaction.atomic():
        Persona.query.for_update().get(id=run.persona.id)
        run = Run.query.for_update().get(id=run_id)
        if run.checked_in_at:
            return run
        if not run.finished_at:
            raise ValueError("Provider must stop the browser before check-in")
        reasons = []
        if not success:
            reasons.append("Run did not succeed")
        if run.issues:
            reasons.append("Run encountered an issue")
        if not export_complete or not run.tip:
            reasons.append("State export is incomplete")
        observations = list(CheckRun.query.filter(run=run))
        if not run.plan or any(
            not any(o.check_id == p["id"] and o.passed for o in observations) for p in run.plan
        ):
            reasons.append("Required checks have not all passed")
        if set(run.runtime.get("required_check_ids", [])) - {p["id"] for p in run.plan}:
            reasons.append("This check does not certify every condition for the site")
        if run.scope == "*" and set(run.runtime.get("account_scopes", [])) - {
            p["domain"] for p in run.plan
        }:
            reasons.append("Some site accounts have no enabled access checks")
        # Any failed observation is permanent, including a failure followed by recovery.
        if any(not o.passed for o in observations):
            reasons.append("A check failed during this run")
        if run.runtime.get("recovery") and not any(
            p.get('mode') == 'check' and any(o.check_id == p['id'] and o.passed for o in observations)
            for p in run.plan
        ):
            reasons.append("A read-only task must verify the fix")
        leader = leader_for(run.persona)
        if not reasons and leader.finished_at and leader.finished_at >= run.finished_at:
            reasons.append("A newer successful run already leads")
        eligible = not reasons
        if eligible:
            cp = Checkpoint.query.get(digest=run.tip, persona=run.persona)
            LeaderChange.query.create(persona=run.persona, checkpoint=cp, previous=leader.checkpoint,
                run=run, kind='adopt', actor=run.actor)
            leader.checkpoint, leader.verified, leader.finished_at = cp, True, run.finished_at
            leader.update(fields=["checkpoint", "verified", "finished_at"])
        run.status = "success" if success and not run.issues and export_complete else "failed"
        run.promoted = eligible
        run.promotion_reason = (
            "Latest successful check-in for this persona" if eligible else "; ".join(reasons)
        )
        run.checked_in_at = now()
        run.update(fields=["status", "promoted", "promotion_reason", "checked_in_at"])
    return run
