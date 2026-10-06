"""Local integration API. Bearer authentication is separate from browser sessions."""

import hmac
from pathlib import Path

from plain.exceptions import ValidationError
from plain.http import ForbiddenError403, JsonResponse, NotFoundError404
from plain.postgres import transaction
from plain.runtime import settings
from plain.views import View

from .core import services, storage
from .core.models import Leader, Persona, Provider, Run


def run_json(run):
    return {
        "id": run.id,
        "persona_id": run.persona.id,
        "provider_id": run.provider.id,
        "scope": run.scope,
        "base": run.base.digest,
        "tip": run.tip,
        "status": run.status,
        "plan": run.plan,
        "issues": run.issues,
        "created_at": run.created_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "checked_in_at": run.checked_in_at.isoformat() if run.checked_in_at else None,
        "promoted": run.promoted,
        "promotion_reason": run.promotion_reason,
    }


class API(View):
    def before_request(self):
        keyfile = Path(settings.APP_CONFIG_DIR) / "api-token"
        authorization = self.request.headers.get("Authorization", "")
        if not keyfile.exists() or not hmac.compare_digest(
            authorization, "Bearer " + keyfile.read_text().strip()
        ):
            raise ForbiddenError403("A valid Browser Session Autohealer API token is required")

    def handle_exception(self, exc):
        if isinstance(exc, (ValueError, ValidationError)):
            return JsonResponse({"error": str(exc)}, status_code=400)
        return super().handle_exception(exc)


class CollectionAPI(API):
    def get(self):
        resource = self.url_kwargs["resource"]
        if resource == "check-history":
            from uuid import UUID

            from .checks import boundary

            check_uid = UUID(self.request.query_params.get("check", ""))
            at = boundary(self.request.query_params.get("at", ""))
            return JsonResponse(
                services.check_projection(check_uid, at), headers={"Cache-Control": "no-store"}
            )
        if resource == "personas":
            return JsonResponse(
                {
                    "personas": [
                        {
                            "id": p.id,
                            "uid": str(p.uid),
                            "name": p.name,
                            "config": p.config,
                            "leaders": [
                                {
                                    "checkpoint": l.checkpoint.digest,
                                    "verified": l.verified,
                                }
                                for l in Leader.query.filter(persona=p)
                            ],
                        }
                        for p in Persona.query.all()
                    ]
                }
            )
        if resource == "providers":
            return JsonResponse(
                {
                    "providers": [
                        {"id": p.id, "uid": str(p.uid), "name": p.name, "kind": p.kind, "enabled": p.enabled}
                        for p in Provider.query.all()
                    ]
                }
            )
        if resource == "runs":
            return JsonResponse(
                {"runs": [run_json(r) for r in Run.query.order_by("-created_at")[:100]]}
            )
        raise NotFoundError404()

    def post(self):
        data = self.request.json_data
        if self.url_kwargs["resource"] == "imports":
            from .core.importers import import_portable
            from .core.site_scope import normalize_sites

            sites = None if data.get("all_sites") is True else normalize_sites(data.get("sites", []))
            persona = Persona.query.get(id=int(data["persona_id"]))
            cp = import_portable(persona, data["state"], "API import", {"kind": "portable API"}, sites=sites)
            return JsonResponse({"checkpoint": cp.digest, "summary": cp.summary, "coverage": cp.coverage}, status_code=201)
        if self.url_kwargs["resource"] == "checkouts":
            run = services.checkout(
                int(data["persona_id"]),
                int(data["provider_id"]),
                data.get("scope", "*"),
                data.get("actor", "API provider"),
                check_ids=data.get("check_ids"),
                external=data.get("external", True),
                base_digest=data.get("base"),
            )
            return JsonResponse(run_json(run), status_code=201)
        raise NotFoundError404()


class RunAPI(API):
    def get(self):
        run = Run.query.get(id=self.url_kwargs["id"])
        action = self.url_kwargs["action"]
        if action == "status":
            return JsonResponse(run_json(run), headers={"Cache-Control": "no-store"})
        if action == "state":
            from .core.site_scope import select_state

            if run.checked_in_at:
                raise ValueError("Checkout is already checked in")
            return JsonResponse(
                select_state(storage.read_state(run.base.digest), run.runtime["settings"].get("siteScope")), headers={"Cache-Control": "no-store"}
            )
        raise NotFoundError404()

    def post(self):
        run = Run.query.get(id=self.url_kwargs["id"])
        if not run.runtime.get("external"):
            raise ValueError("This run belongs to the browser worker")
        if run.runtime.get('managed_request'):
            raise ValueError('Release managed sessions through /api/sessions/' + run.runtime['managed_request'])
        action, data = self.url_kwargs["action"], self.request.json_data
        if run.checked_in_at:
            if action == "checkin":
                return JsonResponse(run_json(run))
            raise ValueError("Run is already checked in")
        if action == 'ip':
            from .core.network import record
            record(run, data['ip'], source='reported', scope=data.get('scope', 'probe'))
        elif action == "issue":
            services.record_issue(run.id, str(data["message"])[:400])
        elif action == "observation":
            plan = next((p for p in run.plan if p["id"] == int(data["check_id"])), None)
            if not plan:
                raise ValueError("Check was not declared at checkout")
            evidence = data["evidence"]
            if type(evidence.get("passed")) is not bool or not isinstance(
                evidence.get("checks"), list
            ):
                raise ValueError("Provide explicit pass/fail and assertion evidence")
            if not evidence["checks"] or any(
                c.get("passed") is not True for c in evidence["checks"]
            ):
                evidence["passed"] = False
            services.observe(run.id, plan, evidence, data.get("classification"))
        elif action == "end":
            with transaction.atomic():
                run = Run.query.for_update().get(id=run.id)
                if not run.finished_at:
                    run.finished_at, run.status = services.now(), "finishing"
                    run.update(fields=["finished_at", "status"])
        elif action == "checkpoint":
            state = data["state"]
            if (
                not isinstance(state.get("cookies"), list)
                or not isinstance(state.get("settings"), dict)
                or not isinstance(state.get("origins"), list)
            ):
                raise ValueError(
                    "A complete portable state envelope is required, including explicit empty fields"
                )
            cp = services.checkpoint_run(
                run.id,
                state,
                coverage={
                    "native": False,
                    "cookies": "complete",
                    "settings": "complete",
                    "origins": "provider-reported",
                },
            )
            return JsonResponse({"checkpoint": cp.digest}, status_code=201)
        elif action == "checkin":
            if (
                type(data.get("success")) is not bool
                or type(data.get("export_complete")) is not bool
            ):
                raise ValueError("Explicit success and export_complete booleans are required")
            services.finish(
                run.id, success=data["success"], export_complete=data["export_complete"]
            )
        else:
            raise NotFoundError404()
        return JsonResponse(run_json(Run.query.get(id=run.id)))
