"""User interaction with app-owned browsers, through adapter and CDP APIs."""

import math

from plain.http import ForbiddenError403, JsonResponse, RedirectResponse
from plain.postgres import transaction

from .core import services, storage
from .core.agent_sessions import sessions_for
from .core.models import Check, Persona, PersonaSetup, Run
from .core.providers import adapter, browser_command
from .views import Base


class OpenBrowser(Base):
    @transaction.atomic()
    def post(self):
        persona = Persona.query.get(id=self.url_kwargs["id"])
        check_id = self.request.form_data.get("check_id")
        if not check_id:
            return JsonResponse({"error": "Choose a site check and its browser connection"}, status_code=400)
        check = Check.query.get(id=int(check_id), account__persona=persona, enabled=True, provider__enabled=True)
        provider = check.provider
        existing = Run.query.filter(persona=persona, provider=provider,
            scope=check.account.site.domain,
            runtime__interactive=True, status__in=["queued", "starting", "running"]).order_by("-id")
        existing = next((r for r in existing if [p["id"] for p in r.plan] == [check.id]), None)
        if existing:
            return RedirectResponse(f"/runs/{existing.id}/browser", status_code=303)
        setup = PersonaSetup.query.filter(persona=persona).first() if check.pattern.startswith('signup:') else None
        if setup and setup.run and setup.run.status in {'queued', 'starting', 'running', 'finishing'}:
            return JsonResponse({'error':'Wait for the active setup session'}, status_code=409)
        run = services.checkout(persona.id, provider.id, check.account.site.domain,
            self.user.email, check_ids=[check.id],
            base_digest=(setup.run.tip or setup.run.base.digest) if setup and setup.run else None)
        run.runtime["interactive"] = True
        if setup and provider.kind == 'local' and provider.config.get('runtime') != 'docker':
            run.runtime['provider_config'] = {**run.runtime['provider_config'], 'headless':False}
        run.update(fields=["runtime"])
        if setup:
            setup.run, setup.status = run, 'running'
            setup.update(fields=['run', 'status'])
        return RedirectResponse(f"/runs/{run.id}/browser", status_code=303)


class BrowserView(Base):
    template_name = "browser.html"

    def get_template_context(self):
        run = Run.query.get(id=self.url_kwargs["id"])
        work = storage.data_root() / "runs" / str(run.id)
        live = adapter(run.provider).live_url(run) if run.status == "running" else None
        return {**super().get_template_context(), "run": run, "live_url": live,
                "sessions": sessions_for(run), "nav": "runs",
                "selected": (work / "active-target").read_text() if (work / "active-target").exists() else ""}

    def post(self):
        if self.request.headers.get("X-Account-Checker") != "browser-control":
            raise ForbiddenError403()
        run = Run.query.get(id=self.url_kwargs["id"])
        if run.runtime.get("external") or not run.runtime.get("interactive") or run.checked_in_at:
            return JsonResponse({"error": "This browser is not open for interaction"}, status_code=409)
        data = self.request.json_data
        work = storage.private_dir(storage.data_root() / "runs" / str(run.id))
        if data.get("operation") == "close":
            if run.runtime.get('recovery', {}).get('binding', {}).get('signup') and not run.runtime.get('interactive_ready'):
                return JsonResponse({'error':'Wait for the agent to request your help'}, status_code=409)
            (work / "close-requested").touch()
            return JsonResponse({"ok": True})
        if not run.runtime.get("interactive_ready") or (work / "close-requested").exists():
            return JsonResponse({"error": "This browser is not open for interaction"}, status_code=409)
        target = data.get("target")
        if target not in {tab["targetId"] for tab in run.runtime.get("tabs", [])}:
            return JsonResponse({"error": "Unknown browser tab"}, status_code=400)
        operation = data.get("operation")
        payload = {"operation": operation, "target": target}
        if operation in {"click", "scroll"}:
            viewport = run.runtime["settings"].get("viewport", {"width": 1440, "height": 1000})
            for axis, dimension in (("x", "width"), ("y", "height")):
                value = float(data[axis])
                if not math.isfinite(value) or not 0 <= value <= viewport[dimension]:
                    return JsonResponse({"error": "Invalid coordinates"}, status_code=400)
                payload[axis] = value
            if operation == "scroll":
                payload["delta"] = max(-2000, min(2000, int(data["delta"])))
        elif operation == "text":
            payload["text"] = str(data["text"])[:4000]
        elif operation == "key" and data.get("key") in {"Enter", "Tab", "Escape", "Backspace", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight"}:
            payload["key"] = data["key"]
        elif operation != "select":
            return JsonResponse({"error": "Unknown browser action"}, status_code=400)
        browser_command("control", run, **payload)
        return JsonResponse({"ok": True}, headers={"Cache-Control": "no-store"})


class BrowserStatus(Base):
    def get(self):
        run = Run.query.get(id=self.url_kwargs["id"])
        return JsonResponse({"status": run.status, "ready": run.runtime.get("interactive_ready", False),
                             "tabs": run.runtime.get("tabs", [])}, headers={"Cache-Control": "no-store"})
