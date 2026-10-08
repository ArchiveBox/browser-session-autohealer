from plain.runtime import setup

setup()

import os
from pathlib import Path

import httpx
import pytest
from plain.runtime import settings

ORIGIN = os.environ.get('ACCOUNT_CHECKER_TEST_ORIGIN', 'http://127.0.0.1:8421')

@pytest.fixture
def app_session():
    with httpx.Client(base_url=ORIGIN, timeout=30) as client:
        response = client.post(
            "/login",
            data={
                "email": "local@account-checker.test",
                "password": (Path(settings.APP_CONFIG_DIR) / "admin-password").read_text(),
            },
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 302
        yield client

