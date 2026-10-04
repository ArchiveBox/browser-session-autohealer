"""A real multi-site run must expose a separate result for each check."""


def test_linkedin_result_contains_only_linkedin(app_session):
    response = app_session.get("/runs/53/checks/12")
    assert response.status_code == 200
    assert "Signed in and reading LinkedIn" in response.text
    assert "Signed in and reading Hacker News" not in response.text
    assert 'data-access-summary="login_required"' in response.text
    assert response.text.count('class="agent-session"') == 1
    assert "2 captured browser steps" in response.text
    assert "/agents?run=53&amp;check=12" in response.text


def test_hn_result_is_not_failed_by_linkedin(app_session):
    response = app_session.get("/runs/53/checks/10")
    assert response.status_code == 200
    assert "Signed in and reading Hacker News" in response.text
    assert "Signed in and reading LinkedIn" not in response.text
    assert 'data-access-summary="accessible"' in response.text
    assert response.text.count('class="agent-session"') == 1
    assert "3 captured browser steps" in response.text


def test_unknown_check_is_not_an_unrelated_result(app_session):
    assert app_session.get("/runs/53/checks/9").status_code == 404
