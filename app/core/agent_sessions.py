"""Discover retained OpenCode sessions without waiting on the inference server."""

import json
from datetime import UTC, datetime
from pathlib import Path

from .models import CheckRun
from .opencode_proxy import _project_route
from .storage import data_root


def read_json(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except OSError, ValueError:
        # The worker can be writing the next artifact while the page refreshes.
        return {}


def sessions_for(run, observations=None):
    """Return real session links and compact browser activity, including live checks."""
    observations = list(CheckRun.query.filter(run=run) if observations is None else observations)
    work = data_root() / "runs" / str(run.id)
    plans = {str(plan["id"]): plan for plan in run.plan}
    observed = {str(observation.check_id): observation for observation in observations}
    retained = {}
    for path in work.glob("agent-*.json"):
        metadata = read_json(path)
        if metadata.get("session_id"):
            retained[path.stem.removeprefix("agent-")] = metadata
    for check_id, observation in observed.items():
        if observation.classification.get("session_id"):
            retained.setdefault(check_id, observation.classification)

    context = read_json(work / "harness-context.json")
    completed_steps = {
        program["step"]
        for observation in observations
        for program in observation.evidence.get("programs", [])
    }
    results = []
    for check_id, metadata in retained.items():
        observation = observed.get(check_id)
        recovery = check_id == "recovery" and run.runtime.get("recovery")
        current = str(context.get("check_id")) == check_id
        active = run.status in {"queued", "starting", "running", "finishing"}
        status = (
            "completed"
            if (observation and observation.ended_at) or (recovery and run.checked_in_at)
            else "running"
            if active and current
            else ("waiting" if active else "interrupted")
        )
        programs = list(observation.evidence.get("programs", [])) if observation else []
        if (not observation or not observation.ended_at) and (current or recovery):
            for path in sorted(
                (work / "agent-workspace").glob("*.json"), key=lambda p: p.stat().st_mtime
            ):
                program = read_json(path)
                if program.get("step") and program["step"] not in completed_steps:
                    programs.append(
                        {key: program.get(key) for key in ("step", "purpose", "exit_code")}
                    )
        # Early classifier sessions used the server's shared working directory.
        directory = metadata.get("directory") or str(data_root() / "opencode" / "work")
        session_id = metadata["session_id"]
        path = work / f"agent-{check_id}.json"
        created_at = (
            datetime.fromtimestamp(path.stat().st_mtime, UTC)
            if path.exists()
            else run.created_at
        )
        activity = (
            programs[-1].get("purpose", "Browser program completed")
            if programs
            else "Agent session opened"
        )
        if status == "running":
            pending = [
                p
                for p in (work / "agent-workspace").glob("*.py")
                if not p.with_suffix(".json").exists()
            ]
            if pending:
                activity = "Running a browser program"
            elif programs:
                activity = "Reviewing browser evidence"
            else:
                activity = "Inspecting the page"
        results.append(
            {
                "check_id": check_id,
                "title": ("Restore login" if recovery else plans.get(check_id, {}).get("name"))
                or (observation.evidence.get("title") if observation else None)
                or f"Check {check_id}",
                "session_id": session_id,
                "url": f"/agents?run={run.id}&check={check_id}",
                "frame_url": _project_route(Path(directory), session_id),
                "directory": directory,
                "model": metadata.get("model", ""),
                "status": status,
                "status_label": {
                    "running": "Checking",
                    "completed": "Finished",
                    "interrupted": "Stopped",
                    "waiting": "Session opened",
                }[status],
                "activity": activity,
                "program_count": len(programs),
                "programs": programs,
                "created_at": created_at,
                "observation": observation,
            }
        )
    return sorted(results, key=lambda session: session["created_at"])
