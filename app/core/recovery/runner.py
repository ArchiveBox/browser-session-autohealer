"""Recovery is an isolated, untrusted candidate until a fresh access check passes."""

import json
import secrets
import time
from pathlib import Path

from .. import inference, network, services, storage
from ..models import Check, Run
from ..providers import ProviderAPIError, adapter, browser_command, watch_browser
from .config import Unavailable


def agent():
    workflow = inference.access_agent()["prompt"].split("Browser-harness skill (task scope above takes precedence):", 1)[1]
    return {
        **inference.access_agent(),
        "description": "Restore login with local credential placeholders",
        "prompt": Path(__file__).parents[1].joinpath("prompts/recovery.txt").read_text()
            + "\n" + Path(__file__).parents[1].joinpath("prompts/learning.txt").read_text()
            + "\nBrowser-harness skill (task scope above takes precedence):\n" + workflow,
        "steps": 18,
        "permission": {"*": "deny", "private_login_*": "allow", "browser_harness_*": "allow"},
    }


def signup_agent():
    result = agent()
    skill = result['prompt'].split('Browser-harness skill (task scope above takes precedence):', 1)[1]
    result.update(description='Set up researcher accounts with private autofill and verification',
        prompt=Path(__file__).parents[1].joinpath('prompts/signup.txt').read_text()
            + '\nBrowser-harness skill (task scope above takes precedence):' + skill,
        steps=60)
    return result


def queue(account, provider_id, actor, *, base_digest=None, check_id=None):
    from ..tasks import ensure_fix

    task = Check.query.filter(id=check_id, account=account, provider__id=provider_id, enabled=True).first()
    if not task:
        raise Unavailable("Choose a task for this account and provider")
    fix = task if task.mode == 'fix' else ensure_fix(task, 'login_required')
    return services.checkout(account.persona.id, provider_id, account.site.domain, actor,
        check_ids=[fix.id], base_digest=base_digest)


def run_agent(run, target, directory):
    inference.ensure_server()
    identifier = run.runtime['recovery'].get('task', {}).get('id', 'recovery')
    binding = run.runtime["recovery"]["binding"]
    signup = binding.get('signup')
    context = {
        "active": True,
        "nonce": secrets.token_hex(24),
        "expires_at": time.time() + (1200 if signup else 600),
        "binding": binding,
        "cdp": run.runtime["cdp"],
        "container": run.runtime.get("container"),
        "target_id": target["targetId"],
        "model": run.runtime["inference"]["model"],
        "check_id": identifier if isinstance(identifier, int) else None,
        "since": run.created_at.replace(microsecond=0).isoformat(),
    }
    context_path = directory / "recovery-context.json"
    context_path.write_text(json.dumps(context))
    (directory / "harness-context.json").write_text(
        json.dumps(
            {
                "active": True,
                "cdp": run.runtime["cdp"],
                "container": run.runtime.get("container"),
                "check_id": identifier,
            }
        )
    )
    work = storage.private_dir(directory / "recovery-agent")
    (work / ".ignore").write_text("*\n")
    with inference.client(work) as api:
        if signup:
            response = api.get('/agent')
            response.raise_for_status()
            if not any(item['name'] == 'signup' for item in response.json()):
                raise Unavailable('Restart the app-owned OpenCode server to load the signup agent')
        response = api.post(
            "/mcp",
            json={
                "name": "private_login",
                "config": {
                    "type": "local",
                    "command": [
                        "uv",
                        "run",
                        "--project",
                        str(Path(__file__).resolve().parents[3]),
                        "python",
                        str(Path(__file__).with_name("secret_mcp.py")),
                        "--directory",
                        str(directory),
                    ],
                    "enabled": True,
                    "timeout": 250000 if signup else 60000,
                },
            },
        )
        response.raise_for_status()
        if response.json().get("private_login", {}).get("status") != "connected":
            raise Unavailable("OpenCode could not connect to the local credential broker")
        response = api.post(
            "/mcp",
            json={
                "name": "browser_harness",
                "config": {
                    "type": "local",
                    "command": [
                        "uv",
                        "run",
                        "--project",
                        str(Path(__file__).resolve().parents[3]),
                        "python",
                        str(Path(__file__).parents[1] / "harness_mcp.py"),
                        "--run-dir",
                        str(directory),
                    ],
                    "enabled": True,
                    "timeout": 100000,
                },
            },
        )
        response.raise_for_status()
        if response.json().get("browser_harness", {}).get("status") != "connected":
            raise Unavailable("OpenCode could not connect to browser-harness")
        saved_session = directory / f"agent-{identifier}.json"
        if run.runtime.get('interaction_ended_at') and saved_session.exists():
            session_id = json.loads(saved_session.read_text())['session_id']
        else:
            response = api.post(
                "/session", json={"title": f"{'Set up account' if signup else 'Restore login'} · {run.scope} · browser {run.id}"}
            )
            response.raise_for_status()
            session_id = response.json()["id"]
        metadata = {
            "session_id": session_id,
            "directory": str(work),
            "model": inference.config()["model"],
        }
        (directory / f"agent-{identifier}.json").write_text(json.dumps(metadata))
        try:
            model = run.runtime["inference"]["model"]
            response = api.post(
                f"/session/{session_id}/message",
                json={
                    "agent": "signup" if signup else "recovery",
                    "model": {"providerID": "openai", "modelID": model.split("/", 1)[1]},
                    "parts": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "task": run.runtime["recovery"].get('task', {}).get('instruction',
                                        "Restore this research account's login using placeholders."),
                                    "site": run.scope,
                                    "task_id": identifier,
                                    "configured_purposes": list(binding.get("fields", {})),
                                    "authentication": binding.get('authentication', 'password'),
                                    "approved_origins": binding["origins"],
                                    "start_url": binding["start_url"],
                                    "target_id": target["targetId"],
                                    "username": run.plan[0].get("username", "") if run.plan else "",
                                    **({'setup': signup} if signup else {}),
                                    **({'resume': 'The user completed the requested browser step and clicked Continue account setup. Inspect the current page and continue from it; do not restart signup.'}
                                       if run.runtime.get('interaction_ended_at') else {}),
                                    "screenshot_path": "/run/agent-workspace/recovery.png"
                                    if run.runtime.get("container")
                                    else str(directory / "agent-workspace/recovery.png"),
                                }
                            ),
                        }
                    ],
                },
                timeout=1200 if signup else 300,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("info", {}).get("error"):
                raise Unavailable("The recovery agent stopped with an error")
            text = "\n".join(
                p.get("text", "") for p in payload.get("parts", []) if p.get("type") == "text"
            )
            import re

            answer = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()))
            if answer.get("status") not in {"submitted", "needs_human"}:
                raise Unavailable("The recovery agent did not return a valid outcome")
            return answer
        except Exception:
            api.post(f"/session/{session_id}/abort")
            raise
        finally:
            context_path.write_text(json.dumps({"active": False}))
            (directory / "harness-context.json").write_text(json.dumps({"active": False}))
            transcript = api.get(f"/session/{session_id}/message")
            transcript.raise_for_status()
            (directory / f"transcript-{identifier}.json").write_text(transcript.text)
            if any(
                part.get("type") == "tool" and part.get("state", {}).get("status") == "error"
                for message in transcript.json() for part in message.get("parts", [])
            ):
                services.record_issue(run.id, "A recovery agent tool failed during this attempt")
            for path in (directory / "agent-workspace").glob("*.json"):
                if json.loads(path.read_text()).get("exit_code"):
                    services.record_issue(run.id, "A browser program failed during login recovery")
                    break
            if (directory / "secret-events.jsonl").exists() and any(
                json.loads(line)["status"] in {"blocked", "unavailable"}
                for line in (directory / "secret-events.jsonl").read_text().splitlines()
            ):
                services.record_issue(run.id, "A credential action was blocked or unavailable")
            if (directory / "recovery-actions.jsonl").exists() and any(
                json.loads(line)["result"].get("status") in {"failed", "blocked", "unavailable"}
                for line in (directory / "recovery-actions.jsonl").read_text().splitlines()
            ):
                services.record_issue(run.id, "A credential browser action did not complete")


def execute(run):
    provider = adapter(run.provider, kind=run.runtime["provider_kind"])
    work = storage.private_dir(storage.data_root() / "runs" / str(run.id))
    launched, exported, stopped, outcome = False, None, False, None
    plan = next(p for p in run.plan if p['mode'] == 'fix')
    execution = None
    signup = run.runtime['recovery']['binding'].get('signup')
    try:
        from ..site_scope import select_state

        state = select_state(storage.read_state(run.base.digest), run.runtime["settings"].get("siteScope"))
        run.runtime = {**run.runtime, **provider.launch(run)}
        launched = True
        run.status = "running"
        run.update(fields=["runtime", "status"])
        from ..twocaptcha import configure_browser
        configure_browser(run)
        network.observe(run)
        execution = services.start_check(run.id, plan)
        if run.base.coverage.get("cookies") != "native-only":
            browser_command("seed", run, state=state)
        target = browser_command("prepare", run, state=state)
        (work / "recovery-context.json").write_text(json.dumps({"active": False}))
        storage.private_dir(work / "agent-workspace")
        services.restore_agent_helpers(plan['id'], work / 'agent-workspace')
        with watch_browser(run, target, state, capture=bool(run.runtime.get('interactive'))):
            outcome = run_agent(run, target, work)
            while signup and run.runtime.get('interactive') and outcome['status'] == 'needs_human':
                from ..models import PersonaSetup

                deadline = run.started_at.timestamp() + min(1500,
                    provider.session_lifetime(run.runtime['provider_config']) - 30)
                run.runtime.update(tabs=[{**target, 'name':plan['name'], 'check_id':plan['id']}],
                    interactive_ready=True, human_reason=outcome.get('reason', ''), human_until=deadline)
                run.update(fields=['runtime'])
                setup = PersonaSetup.query.get(id=signup['setup_id'])
                setup.status = 'needs_human'
                setup.update(fields=['status'])
                while not (work / 'close-requested').exists() and time.time() < deadline:
                    time.sleep(1)
                run.runtime.update(interactive_ready=False, interaction_ended_at=services.now().isoformat())
                run.update(fields=['runtime'])
                if not (work / 'close-requested').exists():
                    outcome['reason'] = 'Live signup session expired before Continue account setup was clicked; resume from the saved browser state.'
                    break
                (work / 'close-requested').unlink()
                setup.status = 'running'
                setup.update(fields=['status'])
                outcome = run_agent(run, target, work)
        from ..agent_sessions import read_json

        programs = [read_json(p) for p in sorted((work / 'agent-workspace').glob('*.json'), key=lambda p: p.stat().st_mtime)]
        programs = [p for p in programs if p.get('step')]
        metadata = read_json(work / f"agent-{plan['id']}.json")
        if signup and outcome['status'] == 'submitted':
            from ..models import PersonaSetup
            from ..onboarding import read
            account = read(PersonaSetup.query.get(id=signup['setup_id']))['accounts'][signup['site']]
            if not account.get('credentials_saved'):
                services.record_issue(run.id, 'Save the new account credentials before completing setup')
        completed = outcome['status'] == 'submitted' and not Run.query.get(id=run.id).issues
        services.observe(run.id, plan, {'passed': completed, 'screenshot': 'recovery.png',
            'programs': programs, 'reason': outcome.get('reason', '')},
            {'state': 'accessible' if completed else 'human_required', **metadata}, check_run=execution)
        execution = None
        if outcome["status"] == "submitted":
            from ..tasks import verify_fix

            if completed:
                verify_fix(run, plan, state)
            exported = browser_command("export", run, state=state)
            from ..worker import verify_cookie_preservation

            verify_cookie_preservation(run, state, exported)
        else:
            services.record_issue(run.id, outcome.get("reason", "Login needs human help")[:400])
            if signup:
                exported = browser_command('export', run, state=state)
    except Exception as error:  # noqa: BLE001 - never log upstream errors or secret-bearing browser diagnostics
        import traceback

        (work / "recovery-error.json").write_text(
            json.dumps(
                {
                    "type": type(error).__name__,
                    "frames": [
                        {
                            "file": Path(frame.filename).name,
                            "line": frame.lineno,
                            "function": frame.name,
                        }
                        for frame in traceback.extract_tb(error.__traceback__)
                    ],
                }
            )
        )
        services.record_issue(
            run.id,
            error.public_message if isinstance(error, ProviderAPIError) else
            "Login recovery stopped; inspect the sanitized agent activity and connection status",
        )
        if execution:
            services.fail_check(execution, plan, 'Fix stopped before completion')
        if signup and launched and exported is None:
            try:
                exported = browser_command('export', run, state=state)
            except Exception:  # noqa: BLE001 - preserve the primary error without leaking browser state
                services.record_issue(run.id, 'Could not preserve the interrupted signup browser')
    finally:
        (work / "recovery-context.json").write_text(json.dumps({"active": False}))
        (work / "harness-context.json").write_text(json.dumps({"active": False}))
        if launched:
            try:
                network.observe(run)
                provider.stop(run)
                stopped = True
            except Exception:  # noqa: BLE001 - same secret boundary applies to shutdown errors
                services.record_issue(run.id, "The login browser did not stop cleanly")
        run = Run.query.get(id=run.id)
        run.status, run.finished_at = "finishing", services.now()
        run.runtime['interactive_ready'] = False
        run.update(fields=["status", "finished_at", 'runtime'])
    if exported and stopped and (signup or not run.issues):
        try:
            services.checkpoint_run(
                run.id, exported, native=provider.native_path(run), coverage=run.base.coverage
            )
        except Exception:  # noqa: BLE001 - finish the failed run without leaking profile diagnostics
            services.record_issue(run.id, "Could not save the restored browser profile")
            exported = None
    result = services.finish(run.id, success=bool(exported and stopped and not run.issues), export_complete=bool(exported and stopped))
    if signup:
        from ..onboarding import finished
        finished(result)
        return result
    from ..tasks import dispatch

    dispatch(result)
    return result
