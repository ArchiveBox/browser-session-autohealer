import json
import time
from contextlib import ExitStack

from plain.postgres import transaction

from . import inference, network, services, storage
from .models import Check, Persona, Run
from .providers import adapter, browser_command, watch_browser


def schedule_due():
    for check_id in [c.id for c in Check.query.filter(enabled=True, mode="check")]:
        with transaction.atomic():
            candidate = Check.query.get(id=check_id)
            Persona.query.for_update().get(id=candidate.account.persona.id)
            check = Check.query.for_update().get(id=check_id)
            if check.next_due and check.next_due > services.now():
                continue
            active = Run.query.filter(
                persona=check.account.persona,
                provider=check.provider,
                scope=check.account.site.domain,
                status__in=["queued", "starting", "running", "finishing"],
            )
            if any(any(p["id"] == check.id for p in run.plan) for run in active):
                continue
            services.checkout(
                check.account.persona.id, check.provider.id, check.account.site.domain, "scheduler",
                check_ids=[check.id],
            )


def claim(run_id=None):
    with transaction.atomic():
        query = Run.query.for_update(skip_locked=True).filter(status="queued")
        if run_id is not None:
            query = query.filter(id=run_id)
        run = query.order_by("created_at").first()
        if run:
            run.status, run.started_at = "starting", services.now()
            run.update(fields=["status", "started_at"])
        return run


def verify_cookie_preservation(run, before, after):
    """Unrelated live cookies must survive a site-scoped browser use."""
    if run.scope == "*":
        return
    current = {storage.cookie_key(c): c for c in after.get("cookies", [])}
    signup = run.runtime.get('recovery', {}).get('binding', {}).get('signup')
    allowed = [run.scope]
    if signup:
        from urllib.parse import urlsplit
        allowed += [urlsplit(url).hostname for url in run.runtime['recovery']['binding']['origins']]
    changed = []
    for cookie in before.get("cookies", []):
        domain = cookie["domain"].lstrip(".")
        if (
            any(site == domain or site.endswith('.' + domain) or domain.endswith('.' + site) for site in allowed)
        ):
            continue
        if 0 < cookie.get("expires", -1) <= services.now().timestamp():
            continue  # Natural expiry is not a profile transfer failure.
        actual = current.get(storage.cookie_key(cookie))
        if actual is None or actual["value"] != cookie["value"]:
            changed.append(cookie)
    if changed:
        services.record_issue(
            run.id,
            f"Browser changed or lost {len(changed)} unexpired cookies outside the checked site",
        )


def execute(run):
    if run.runtime.get("recovery"):
        from .recovery.runner import execute as recover

        return recover(run)
    provider = adapter(run.provider, kind=run.runtime.get("provider_kind"))
    launched = False
    exported = None
    stopped = False
    current_check = None
    current_plan = None
    current_target = None
    watchers = ExitStack()
    try:
        required = run.runtime["settings"].get(
            "requiredCapabilities", ["cookies", "localStorage", "sessionStorage"]
        )
        missing = [c for c in required if not provider.capabilities.get(c)]
        if missing:
            raise ValueError(
                "Provider cannot preserve required capabilities: " + ", ".join(missing)
            )
        from .site_scope import select_state

        state = select_state(storage.read_state(run.base.digest), run.runtime["settings"].get("siteScope"))
        # Desired settings are frozen at checkout execution and retained with the run.
        run.runtime = {**run.runtime, **provider.launch(run)}
        launched = True
        from .network import observe as observe_ip
        run.status = "running"
        run.update(fields=["runtime", "status"])
        from .twocaptcha import configure_browser
        configure_browser(run)
        observe_ip(run)
        # Native-only imports carry no semantic cookie export; keep their native cookies.
        if run.base.coverage.get("cookies") != "native-only":
            browser_command("seed", run, state=state)
        targets = []
        for plan in run.plan:
            target = browser_command("prepare", run, state=state)
            current_target = target["targetId"]
            targets.append(target)
            run.runtime.setdefault("tabs", []).append({**target, "name": plan["name"], "check_id": plan["id"]})
            run.update(fields=["runtime"])
            watchers.enter_context(watch_browser(run, target, state))
            measured = browser_command("environment", run, target=target["targetId"])
            (storage.data_root() / "runs" / str(run.id) / f"environment-{plan['id']}.json").write_text(json.dumps(measured))
            if run.runtime.get("interactive"):
                browser_command("navigate", run, target=target["targetId"], url=plan["url"])
        if run.runtime.get("interactive"):
            run.runtime["interactive_ready"] = True
            run.update(fields=["runtime"])
            stop = storage.data_root() / "runs" / str(run.id) / "close-requested"
            deadline = run.started_at.timestamp() + 1500
            while not stop.exists() and time.time() < deadline:
                time.sleep(1)
            # Certify the state after human use, immediately before export.
            run.runtime.update(interactive_ready=False, interaction_ended_at=services.now().isoformat())
            run.update(fields=["runtime"])
        for plan, target in zip(run.plan, targets, strict=True):
            current_plan = plan
            current_target = target["targetId"]
            current_check = services.start_check(run.id, plan)
            result, classification = inference.check_access(run, plan, target)
            for issue in result.get("issues", []):
                services.record_issue(run.id, issue)
            services.observe(run.id, plan, result, classification, check_run=current_check)
            current_check = None
            measured = browser_command("environment", run, target=target["targetId"])
            (storage.data_root() / "runs" / str(run.id) / f"environment-final-{plan['id']}.json").write_text(json.dumps(measured))
        exported = browser_command("export", run, state=state)
        verify_cookie_preservation(run, state, exported)
    except Exception as exc:  # noqa: BLE001 - record every failed run and close its owned browser
        services.record_issue(run.id, str(exc)[:400])
        if launched:
            try:
                browser_command(
                    "screenshot",
                    run,
                    target=current_target,
                    screenshot=str(storage.data_root() / "runs" / str(run.id) / "failure.png"),
                )
            except Exception as screenshot_error:  # noqa: BLE001 - the failed browser may already be closed
                services.record_issue(
                    run.id, "Could not capture the stopped browser: " + str(screenshot_error)[:250]
                )
        if current_check is not None:
            services.fail_check(current_check, current_plan, str(exc)[:400])
    finally:
        try:
            watchers.close()
        except Exception as exc:  # noqa: BLE001 - retain cleanup failures without losing browser teardown
            services.record_issue(run.id, "Live browser stopped with an error: " + str(exc)[:250])
        context = storage.data_root() / "runs" / str(run.id) / "harness-context.json"
        if context.exists():
            context.write_text(json.dumps({"active": False}))
        if launched:
            try:
                observe_ip(run)
                provider.stop(run)
                stopped = True
            except Exception as exc:  # noqa: BLE001 - record every failed run and close its owned browser
                services.record_issue(run.id, "Browser did not stop cleanly: " + str(exc)[:300])
        run = Run.query.get(id=run.id)
        run.status, run.finished_at = "finishing", services.now()
        if run.runtime.get("interactive"):
            run.runtime["interactive_ready"] = False
        run.update(fields=["status", "finished_at", "runtime"])
    if exported and stopped:
        try:
            services.checkpoint_run(
                run.id,
                exported,
                native=provider.native_path(run),
                coverage={
                    "cookies": "complete",
                    "settings": "complete",
                    "origins": "visited origins",
                    "native": provider.capabilities["native"],
                    "capabilities": provider.capabilities,
                },
            )
        except Exception as exc:  # noqa: BLE001 - record every failed run and close its owned browser
            services.record_issue(run.id, "Checkpoint export failed: " + str(exc)[:300])
            exported = None
    result = services.finish(
        run.id,
        success=not Run.query.get(id=run.id).issues,
        export_complete=bool(exported and stopped),
    )

    from .tasks import dispatch

    dispatch(result)
    return result


def loop(once=False, *, schedule=True):
    while True:
        network.ensure_database()
        if schedule:
            schedule_due()
        run = claim()
        if run:
            execute(run)
        if once:
            return
        if not run:
            time.sleep(2)
