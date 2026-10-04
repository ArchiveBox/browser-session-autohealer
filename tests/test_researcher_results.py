"""Acceptance against retained real browser results and their encrypted history."""

import json
import os
from html.parser import HTMLParser

from app.compare import ValueHistory
from app.core import health, storage
from app.core.models import Checkpoint, CheckRun, Run

BASE = os.environ["ACCOUNT_CHECKER_COMPARE_BASE"]
HEAD = os.environ["ACCOUNT_CHECKER_COMPARE_HEAD"]
COMPARE = f"/compare?base={BASE}&head={HEAD}"
DIAGNOSTIC = os.environ["ACCOUNT_CHECKER_PRIVATE_DIAGNOSTIC"]


class VisibleText(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.details = []
        self.text = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "details":
            self.details.append("open" in dict(attrs))

    def handle_endtag(self, tag):
        if tag == "details":
            self.details.pop()

    def handle_data(self, text):
        if all(self.details):
            self.text.append(text)


def test_each_site_uses_its_own_access_result():
    run = Run.query.get(id=53)
    observations = list(CheckRun.query.filter(run=run).order_by("created_at"))
    assert health.report(run, observations, "news.ycombinator.com")["state"] == "accessible"
    assert health.report(run, observations, "linkedin.com")["state"] == "login_required"
    hn = next(o for o in observations if o.check_id == 10)
    assert health.report(run, observations, "news.ycombinator.com")["at"] == hn.ended_at


def test_raw_agent_output_is_only_in_expandable_details(app_session):
    for path in ["/runs/53/checks/12", "/personas/4", "/?view=overview", "/checks/12", COMPARE]:
        response = app_session.get(path)
        assert response.status_code == 200
        visible = " ".join(VisibleText(response.text).text)
        assert DIAGNOSTIC not in visible, path
        assert "Final evidence was saved" not in visible, path
        assert "Saved · value hidden" not in visible, path
    assert DIAGNOSTIC in app_session.get("/runs/53/checks/12").text


def test_compare_shows_chronology_sources_and_site_screenshots(app_session):
    response = app_session.get(COMPARE)
    assert response.status_code == 200
    assert 'data-newer="right"' in response.text
    assert (
        'data-source-revision="f9881f6da359744f707cdd76ca1339d0509ff3b3803cd55283e663a56c5a8ff2"'
        in response.text
    )
    assert f'data-source-revision="{HEAD}"' in response.text
    assert "Browser import" in response.text
    assert "Browserbase" in response.text
    for run in (50, 53):
        for check in (10, 12):
            assert f"/runs/{run}/checks/{check}" in response.text
            assert f"/evidence/{run}/check-{check}.png" in response.text
    # Even though provenance uses the exact secret internally, no values reach HTML.
    for digest in (BASE, HEAD):
        cookies = storage.read_state(digest)["cookies"]
        for cookie in cookies:
            if cookie["name"] == "li_at":
                assert cookie["value"] not in response.text
    swapped = app_session.get(f"/compare?base={HEAD}&head={BASE}")
    assert 'data-newer="left"' in swapped.text


def test_navigation_has_two_distinct_account_views(app_session):
    home = app_session.get("/")
    assert home.status_code == 200
    nav = home.text.split('<nav aria-label="Main navigation">')[1].split("</nav>")[0]
    assert "Account access" not in nav
    assert 'aria-current="page"><span aria-hidden="true">◎</span>Personas' in nav
    assert 'aria-label="Personas"' in home.text
    for path in ["/?view=sites", "/?view=sites&group=persona"]:
        sites = app_session.get(path)
        assert sites.status_code == 200
        assert 'aria-label="Personas"' not in sites.text
        assert 'aria-label="Sites"' in sites.text
        for domain in ("linkedin.com", "news.ycombinator.com"):
            assert domain in sites.text
        assert sites.text.count('data-model="sites"') == 2
        assert 'aria-label="Group accounts"' not in sites.text


def test_sessions_are_not_presented_as_check_history(app_session):
    response = app_session.get("/?view=runs")
    assert response.status_code == 200
    assert "<h1>Browser Sessions</h1>" in response.text
    assert 'aria-label="Browser Sessions"' in response.text
    assert "Research workspace" not in response.text
    assert "All monitored sites" not in response.text
    assert "No screenshot recorded" not in response.text
    for run_id in (50, 53, 54):
        assert response.text.count(f'data-session="{run_id}"') == 1
    assert "Persona leader" in response.text
    assert "Site leader" not in response.text
    assert response.text.count('>Persona leader<') == 1
    assert "Discarded" in response.text
    assert "Started by" in response.text
    assert "Started</span>" in response.text and "Ended</span>" in response.text


def test_value_age_tracks_ancestry_not_copy_time():
    checkpoints = {c.digest: c for c in Checkpoint.query.all()}
    history = ValueHistory(checkpoints, {}, {})
    base, head = checkpoints[BASE], checkpoints[HEAD]
    changes = storage.compare(BASE, HEAD)
    login = next(
        c for c in changes if c["category"] == "cookies" and json.loads(c["key"])[0] == "li_at"
    )
    imported = history.origin(base, "cookies", login["key"], value_only=True)
    assert imported.digest == "f9881f6da359744f707cdd76ca1339d0509ff3b3803cd55283e663a56c5a8ff2"
    assert history.origin(head, "cookies", login["key"], value_only=True).digest == HEAD
    expiry = next(
        c for c in changes if c["category"] == "cookies" and json.loads(c["key"])[0] == "bcookie"
    )
    assert expiry["changed_fields"] == ["expires"]
    # Refreshing expiry must not relabel an inherited cookie value as new.
    assert (
        history.origin(base, "cookies", expiry["key"], value_only=True).digest
        == history.origin(head, "cookies", expiry["key"], value_only=True).digest
    )
    assert history.origin(head, "cookies", expiry["key"]).digest == HEAD
