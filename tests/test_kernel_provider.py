"""Kernel configuration boundaries; lifecycle acceptance uses real cloud browsers."""

import pytest

from app.core.provider_backends.kernel import Kernel


@pytest.mark.parametrize("config", [
    {"api_key": "keep-credentials-in-the-environment"},
    {"profile": {"id": "shared"}},
    {"headless": 1},
    {"stealth": "true"},
    {"timeout_seconds": True},
    {"timeout_seconds": 9},
    {"timeout_seconds": 259201},
    {"region": "us-west"},
    {"proxy": {}},
    {"proxy": {"mode": "residential"}},
    {"proxy": {"id": ""}},
    {"proxy": {"mode": "direct", "id": "ambiguous"}},
])
def test_invalid_connection_settings_are_rejected(config):
    with pytest.raises(ValueError):
        Kernel().validate_config(config)


@pytest.mark.parametrize("config", [
    {},
    {"headless": True, "stealth": False, "timeout_seconds": 10},
    {"region": "eu-west", "proxy": {"mode": "default"}},
    {"proxy": {"mode": "direct"}},
    {"proxy": {"id": "selected-proxy"}},
    {"proxy": {"name": "selected-proxy"}},
])
def test_documented_connection_settings_are_accepted(config):
    Kernel().validate_config(config)


def test_kernel_does_not_claim_platform_emulation():
    # Live Kernel acceptance keeps Linux x86_64 even after a MacIntel CDP override.
    assert Kernel().settings_support({"platform": "MacIntel"})["platform"]["supported"] is False


def test_kernel_does_not_claim_native_window_bounds_transfer():
    assert Kernel().settings_support({"window": {"width": 1440, "height": 1000}})["window"]["supported"] is False
