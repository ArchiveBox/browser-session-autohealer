"""Reject unrequested monitoring scope using the real collection and HTTP UI."""

import pytest

from app.core import services
from app.core.models import Check, Run


def test_monitoring_requires_one_explicit_check():
    check = Check.query.get(id=9)
    before = Run.query.count()
    for scope, checks in [
        ("*", None),
        ("*", [9]),
        (check.account.site.domain, None),
        ("*", [9, 11]),
    ]:
        with pytest.raises(ValueError, match="Choose one check"):
            services.checkout(
                check.account.persona.id,
                check.provider.id,
                scope,
                "scope validation",
                check_ids=checks,
            )
    assert Run.query.count() == before


def test_open_browser_requires_a_specific_check(app_session):
    before = Run.query.count()
    response = app_session.post(
        "/personas/4/browser", data={"provider": "1"}, headers={"Origin": "http://127.0.0.1:8421"}
    )
    assert response.status_code == 400
    assert "Choose a site check" in response.json()["error"]
    assert Run.query.count() == before
