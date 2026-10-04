"""Acceptance against repeated real Local X sessions, including every browser step."""

import json

from app.core import storage
from app.core.models import CheckRun, Run


def test_latest_local_x_sessions_complete_without_browser_errors():
    runs = list(Run.query.filter(provider__id=1, scope="x.com").order_by("-id")[:2])
    assert len(runs) == 2
    for run in runs:
        assert run.checked_in_at and run.status == "success", f"Session #{run.id}: {run.status}"
        assert not run.issues, f"Session #{run.id}: {run.issues}"
        results = list(CheckRun.query.filter(run=run))
        assert results and all(r.passed and r.screenshot for r in results)
        work = storage.data_root() / "runs" / str(run.id)
        chrome_log = (work / "chrome.log").read_text()
        assert "Received signal" not in chrome_log
        assert "FATAL:" not in chrome_log
        steps = [json.loads(p.read_text()) for p in (work / "agent-workspace").glob("*.json")]
        assert len(steps) >= 2
        for step in steps:
            assert step["exit_code"] == 0, step.get("stderr")
            assert not step.get("screenshot_error"), step.get("screenshot_error")
            assert (work / "agent-workspace" / step["screenshot"]).stat().st_size > 1000
        assert json.loads((work / "live.json").read_text())["frames"] > 0
