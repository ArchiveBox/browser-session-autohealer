"""Inspect real app runs; missing evidence fails rather than creating fixtures.

Run each configured provider/site task through the UI and queued worker first.
This matrix covers direct check sessions; recovery has a separate execution flow.
An accurately recorded challenge is valid evidence, not a successful access test.
"""

import hashlib
import json
from pathlib import Path

import pytest

from app.core import storage
from app.core.images import screenshot_format
from app.core.models import Check, CheckRun, Leader, Run

KINDS = ("cdp", "browserless", "kernel", "anchor")
SITES = ("news.ycombinator.com", "linkedin.com", "x.com")


def digest(value):
    # A failed assertion must never print cookie values or saved browser data.
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("domain", SITES)
def test_real_provider_site_evidence_and_checkin(kind, domain, app_session):
    task = Check.query.get(provider__kind=kind, account__site__domain=domain, mode="check")
    observation = next((result for result in
        CheckRun.query.filter(check_id=task.id).order_by("-created_at")
        if not result.run.runtime.get("recovery")), None)
    assert observation is not None, "Run the real configured task first"
    run = observation.run
    assert run.status in {"success", "failed"}, f"Session {run.id} has not completed"
    assert observation.status in {"success", "failure"}
    assert observation.started_at < observation.ended_at
    assert observation.evidence["programs"], "Real browser-harness execution is required"
    image = Path(observation.screenshot).read_bytes()
    image_format = screenshot_format(image)
    response = app_session.get(f"/evidence/{run.id}/{Path(observation.screenshot).name}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/" + image_format
    assert hashlib.sha256(response.content).digest() == hashlib.sha256(image).digest()
    assert bool(observation.passed) == (observation.state == "accessible")
    assert observation.status == ("success" if observation.passed else "failure")
    assert run.checked_in_at is not None
    if run.issues or not observation.passed:
        assert not run.promoted
        assert run.status == "failed"
    else:
        assert run.status == "success"
        assert run.promoted, run.promotion_reason
    if not run.tip:
        # An incomplete export/teardown must remain failed and unadopted.
        assert run.status == "failed"
        assert run.issues
        assert not run.promoted
        return
    before = storage.read_state(run.base.digest)
    after = storage.read_state(run.tip)
    assert digest(before["settings"]) == digest(after["settings"])
    cookies = {storage.cookie_key(cookie): cookie for cookie in after.get("cookies", [])}
    for cookie in before.get("cookies", []):
        domain = cookie["domain"].lstrip(".")
        if domain == run.scope or domain.endswith("." + run.scope) or run.scope.endswith("." + domain):
            continue
        if 0 < cookie.get("expires", -1) <= run.finished_at.timestamp():
            continue
        actual = cookies.get(storage.cookie_key(cookie))
        assert actual is not None, "An unrelated unexpired cookie was lost"
        fields = ("value", "secure", "httpOnly", "sameSite", "partitionKey", "hostOnly")
        assert digest([cookie.get(key) for key in fields]) == digest([actual.get(key) for key in fields])
    # Provider-controlled fingerprint values never replace the canonical request.
    measured = storage.data_root() / "runs" / str(run.id) / f"environment-final-{task.id}.json"
    environment = json.loads(measured.read_text())
    for key in ("userAgent", "timezone", "viewport", "screen", "platform", "colorScheme"):
        assert key in environment
    assert environment["viewport"]["width"] > 0
    origins = {origin["origin"]: origin for origin in after.get("origins", [])}
    for origin in before.get("origins", []):
        for key in ("indexedDB", "opfs"):
            if key in origin:
                assert digest(origin[key]) == digest(origins[origin["origin"]][key])
    assert Leader.query.filter(persona=run.persona).count() == 1


def test_failed_sessions_never_changed_the_leader():
    for run in Run.query.filter(provider__kind__in=KINDS, status="failed"):
        assert not run.promoted
        assert not Leader.query.filter(checkpoint__digest=run.tip).exists()
