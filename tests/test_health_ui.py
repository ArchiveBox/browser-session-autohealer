"""Read the live UI and its real screenshot evidence; no fabricated account data."""

from pathlib import Path

import httpx
import pytest
from plain.runtime import settings


@pytest.fixture
def browser_session():
    with httpx.Client(base_url="http://127.0.0.1:8421", follow_redirects=True) as client:
        response = client.post(
            "/login",
            data={
                "email": "local@account-checker.test",
                "password": (Path(settings.APP_CONFIG_DIR) / "admin-password").read_text(),
            },
            headers={"Origin": "http://127.0.0.1:8421"},
        )
        assert response.status_code == 200 and response.url.path == "/"
        yield client


def test_persona_answers_access_question_without_manual_checkout(browser_session):
    response = browser_session.get("/personas/1")
    assert response.status_code == 200
    assert "Check out a copy" not in response.text
    assert 'name="base"' not in response.text
    assert 'name="provider"' not in response.text
    assert 'aria-label="Credentials"' in response.text
    from app.core.health import check_rows
    from app.core.models import Check

    tiles = check_rows(Check.query.filter(account__persona__id=1).join("provider"))
    assert response.text.count("data-final-evidence") == sum(
        tile["image_label"] == "Final evidence" for tile in tiles
    )
    assert response.text.count('class="credential-check"') == len(tiles)
    assert "Check all sites" in response.text
    assert "Access history" in response.text
    assert "Last confirmed working" in response.text
    assert "data-evidence-image" in response.text


def test_blocked_persona_explains_observed_problem(browser_session):
    response = browser_session.get("/personas/2")
    assert response.status_code == 200
    assert "Sign-in needed" in response.text
    assert "Detected" in response.text
    assert "Monitoring paused" in response.text
    assert "data-evidence-image" in response.text


def test_partial_failure_is_not_presented_as_working(browser_session):
    # Real run 10 passed the first and last check, but failed its own-profile check.
    response = browser_session.get("/runs/10")
    assert response.status_code == 200
    assert "Needs review" in response.text
    assert 'data-access-summary="needs_review"' in response.text
    assert "Technical details" in response.text


def test_browser_settings_use_readable_fields(browser_session):
    response = browser_session.get("/personas/1")
    assert response.status_code == 200
    for category in (
        "Location &amp; language",
        "Screen &amp; appearance",
        "Site permissions",
        "Saved site data",
        "Tabs &amp; page state",
    ):
        assert category in response.text
    response = browser_session.get("/edit/persona?id=1")
    assert response.status_code == 200
    assert 'name="config"' not in response.text
    assert 'name="browser_timezone"' in response.text
    assert 'name="browser_viewport_width"' in response.text


@pytest.mark.parametrize("run_id", [20, 21])
def test_real_browser_frames_and_step_evidence_are_retrievable(browser_session, run_id):
    from app.core.models import Run
    from app.views import browser_steps

    run = Run.query.get(id=run_id)
    assert run.checked_in_at and not run.promoted
    image = browser_session.get(f"/evidence/{run_id}/live.jpg")
    assert image.status_code == 200
    assert image.content.startswith(b"\xff\xd8\xff")
    assert "no-store" in image.headers["cache-control"]
    steps = browser_steps(run)
    assert steps
    for step in steps:
        image = browser_session.get(f"/evidence/{run_id}/{step['screenshot']}")
        assert image.status_code == 200
        assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
    response = browser_session.get(f"/runs/{run_id}")
    assert response.status_code == 200
    assert "data-live-browser" not in response.text  # Ended sessions are not advertised as live.
    assert "Pages visited during this check" in response.text
    assert "Open conversation" in response.text


def test_agent_activity_lists_real_retained_conversations(browser_session):
    response = browser_session.get("/agents")
    assert response.status_code == 200
    assert "Open conversation" in response.text
    assert "Copy OpenCode password" in response.text
    assert "Checkout #" not in response.text


def test_final_check_thumbnails_serve_real_images(browser_session):
    import re

    from app.core.health import check_rows
    from app.core.models import Check

    response = browser_session.get("/personas/1?filter=configured")
    images = re.findall(r'<img[^>]*src="([^"]+)"[^>]*data-evidence-image', response.text)
    tiles = check_rows(Check.query.filter(account__persona__id=1).join("provider"))
    assert set(images) == {t["image"] for t in tiles}
    for tile in tiles:
        result = browser_session.get(tile["image"])
        assert result.status_code == 200
        artifact = (
            Path(tile["result"].artifact_dir) / "live.jpg"
            if tile["image_label"] != "Final evidence"
            else Path(tile["result"].screenshot)
        )
        assert result.content == artifact.read_bytes()


def test_check_history_reconstructs_real_enable_and_pause_changes(browser_session):
    from app.core.models import Check, CheckEvent
    from app.core.services import check_projection

    check = Check.query.get(id=4)
    edits = list(
        CheckEvent.query.filter(check_uid=check.uid, kind="definition_saved").order_by("-id")[:2]
    )
    assert len(edits) == 2
    paused, enabled = edits
    assert enabled.payload["enabled"] is True
    assert paused.payload["enabled"] is False
    assert check_projection(check.uid, enabled.occurred_at)["definition"]["enabled"] is True
    assert check_projection(check.uid, paused.occurred_at)["definition"]["enabled"] is False
    response = browser_session.get("/checks/4", params={"at": enabled.occurred_at.isoformat()})
    assert response.status_code == 200
    assert "Historical state" in response.text and "Enabled" in response.text
    response = browser_session.get("/checks/4", params={"at": "2020-01-01T00:00:00Z"})
    assert response.status_code == 200
    assert "Earlier history is unknown" in response.text
