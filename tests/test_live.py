"""Acceptance against a running app and completed, genuinely executed browser runs.

No browser behavior is implemented here. The user's English prompts and the
OpenCode/browser-harness programs provide all site interaction and judgments.
"""

import hmac
import json
import os
from pathlib import Path

import httpx
import pytest
from plain.runtime import settings

from app.core import storage
from app.core.models import CheckRun, Leader, Run

SUCCESS_IDS = [int(value) for value in os.environ["ACCOUNT_CHECKER_SUCCESS_RUNS"].split(",")]
BLOCKED_ID = int(os.environ["ACCOUNT_CHECKER_BLOCKED_RUN"])


@pytest.fixture
def api():
    with httpx.Client(
        base_url="http://127.0.0.1:8421",
        headers={
            "Authorization": "Bearer "
            + (Path(settings.APP_CONFIG_DIR) / "api-token").read_text().strip()
        },
    ) as client:
        yield client


@pytest.mark.parametrize("run_id", SUCCESS_IDS)
def test_real_signed_in_check_and_roundtrip(api, run_id):
    response = api.get(f"/api/runs/{run_id}/status")
    assert response.status_code == 200
    status = response.json()
    assert status["status"] == "success"
    assert status["issues"] == []
    assert status["tip"] != status["base"]
    run = Run.query.get(id=run_id)
    assert run.runtime["inference"]["model"] == "openai/gpt-6.1-sol"
    observations = list(CheckRun.query.filter(run=run))
    assert len(observations) == len(run.plan) == 3
    work = storage.data_root() / "runs" / str(run.id)
    for observation in observations:
        assert observation.passed and observation.state == "accessible"
        assert observation.classification["model"] == "openai/gpt-6.1-sol"
        assert observation.evidence["identity"] == os.environ["ACCOUNT_CHECKER_EXPECTED_IDENTITY"]
        assert observation.evidence["issues"] == []
        assert all(c["passed"] and c["evidence"] for c in observation.evidence["checks"])
        assert observation.evidence["programs"]
        for program in observation.evidence["programs"]:
            evidence = json.loads(
                (work / "agent-workspace" / f"{program['step']}.json").read_text()
            )
            assert evidence["exit_code"] == 0
            assert (work / "agent-workspace" / f"{program['step']}.py").stat().st_size > 0
        screenshot = work / "agent-workspace" / observation.evidence["screenshot"]
        assert screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        transcript = json.loads((work / f"transcript-{observation.check_id}.json").read_text())
        assert any(
            part.get("tool") == "browser_harness_execute_python"
            and part["state"]["status"] == "completed"
            for message in transcript
            for part in message["parts"]
        )
    base, tip = storage.read_state(run.base.digest), storage.read_state(run.tip)

    def cookies(state):
        return {storage.cookie_key(c): c for c in state["cookies"]}

    base_cookies, tip_cookies = cookies(base), cookies(tip)
    # A real imported authentication cookie survives export without being printed.
    assert any(
        c["domain"].lstrip(".") == "news.ycombinator.com" and c["name"] == "user"
        for c in tip_cookies.values()
    )
    unrelated = {
        k: c for k, c in base_cookies.items() if c["domain"].lstrip(".") != "news.ycombinator.com"
    }
    assert unrelated
    for key, cookie in unrelated.items():
        present = key in tip_cookies
        if not present:
            assert 0 < cookie.get("expires", -1) <= run.finished_at.timestamp(), (
                f"Unexpired cookie lost: {key}"
            )
        else:
            preserved = hmac.compare_digest(tip_cookies[key]["value"], cookie["value"])
            assert preserved, f"Unrelated cookie value changed: {key}"
            for attribute in ["secure", "httpOnly", "sameSite", "hostOnly", "partitionKey"]:
                assert tip_cookies[key].get(attribute) == cookie.get(attribute), (
                    f"Cookie attribute changed: {key} / {attribute}"
                )
    assert tip["settings"] == run.runtime["settings"]
    assert (storage.object_path(run.tip) / "profile/Default").is_dir()


def test_empty_browser_requires_login_and_cannot_lead(api):
    response = api.get(f"/api/runs/{BLOCKED_ID}/status")
    assert response.status_code == 200
    status = response.json()
    assert status["status"] == "failed" and not status["promoted"]
    run = Run.query.get(id=BLOCKED_ID)
    assert storage.read_state(run.base.digest)["cookies"] == []
    observations = list(CheckRun.query.filter(run=run))
    assert observations and all(not o.passed and o.state == "login_required" for o in observations)
    assert run.issues and run.tip  # Evidence and the failed branch remain inspectable.
    assert not Leader.query.filter(persona=run.persona, verified=True).exists()


def test_concurrent_forks_and_latest_finish_wins():
    earlier, later = sorted(
        [Run.query.get(id=i) for i in SUCCESS_IDS[-2:]], key=lambda r: r.finished_at
    )
    assert earlier.base.digest == later.base.digest
    assert max(earlier.started_at, later.started_at) < min(earlier.finished_at, later.finished_at)
    assert earlier.runtime["container"] != later.runtime["container"]
    leader = Leader.query.get(persona=later.persona)
    assert leader.checkpoint.digest == later.tip
    assert leader.finished_at == later.finished_at
    assert earlier.promoted and later.promoted


def test_api_does_not_expose_personas_without_token():
    assert httpx.get("http://127.0.0.1:8421/api/personas").status_code == 403
