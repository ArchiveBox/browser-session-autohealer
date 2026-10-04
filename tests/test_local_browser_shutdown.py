"""Exercise the real local adapter's shutdown boundary before checkpointing."""

import subprocess

from plain.postgres import transaction

from app.core import services
from app.core.models import Persona, Provider
from app.core.providers import adapter


def test_stop_waits_for_owned_chrome_process():
    persona = Persona.query.get(name="Test researcher")
    provider = Provider.query.get(name="Local test browser")
    assert provider.kind == "local" and provider.config["runtime"] == "host"
    with transaction.atomic():
        run = services.checkout(persona.id, provider.id, "127.0.0.1", "Browser shutdown acceptance")
        run.status, run.started_at = "starting", services.now()
        run.update(fields=["status", "started_at"])
    browser = adapter(provider)
    try:
        run.runtime = {**run.runtime, **browser.launch(run)}
        run.update(fields=["runtime"])
        browser.stop(run)
        result = subprocess.run(
            ["ps", "-p", str(run.runtime["pid"]), "-o", "stat="],
            capture_output=True, text=True, check=False,
        )
        assert not result.stdout.strip() or result.stdout.strip().startswith("Z"), (
            "Adapter returned while Chrome could still write its profile"
        )
    finally:
        services.record_issue(run.id, "Lifecycle-only acceptance; no account check was requested")
        run.finished_at = services.now()
        run.update(fields=["finished_at"])
        services.finish(run.id, success=False, export_complete=False)
