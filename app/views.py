import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from plain.auth.views import AuthView
from plain.exceptions import ValidationError
from plain.htmx.views import HTMXView
from plain.http import FileResponse, NotFoundError404, RedirectResponse
from plain.passwords.views import PasswordLoginView
from plain.postgres import transaction
from psycopg import IntegrityError

from .core import browser_settings, credentials, health, inference, services, storage
from .core.agent_sessions import sessions_for
from .core.health import account_rows
from .core.models import (
    Account,
    AppConfig,
    Check,
    Checkpoint,
    CheckRun,
    Leader,
    Persona,
    Provider,
    Run,
    Site,
)
from .core.providers import adapter, provider_options


class LoginView(PasswordLoginView):
    template_name = "login.html"
    success_url = "/"


class Base(AuthView, HTMXView):
    admin_required = True

    def get_template_context(self):
        return {
            **super().get_template_context(),
            "nav": self.request.query_params.get("view", "personas"),
            "time_label": health.time_label,
            "access_labels": health.LABELS,
            "access_summary": health.access_summary,
        }


@transaction.atomic()
def check_account(account, actor, provider=None, check_id=None):
    Persona.query.for_update().get(id=account.persona.id)
    checks = Check.query.filter(account=account, enabled=True, provider__enabled=True)
    if check_id is None:
        checks = checks.filter(mode="check")
    if provider:
        checks = checks.filter(provider=provider)
    if check_id is not None:
        checks = checks.filter(id=check_id)
    checks = list(checks)
    if not checks:
        raise ValueError("Enable an access check and its browser connection first")
    runs = []
    for check in checks:
        active = Run.query.filter(persona=account.persona, provider=check.provider,
            scope=account.site.domain, status__in=health.ACTIVE).order_by("-created_at")
        existing = next((r for r in active if any(p["id"] == check.id for p in r.plan)), None)
        runs.append(existing or services.checkout(account.persona.id, check.provider.id,
            account.site.domain, actor, check_ids=[check.id]))
    return runs


class Dashboard(Base):
    def get_template_names(self):
        return ['dashboard.html' if self.request.query_params.get('view') == 'settings' else 'tree_browser.html']

    def get_template_context(self):
        from .trees import TITLES, root_table

        ctx = super().get_template_context()
        if ctx['nav'] == 'overview':
            ctx['nav'] = 'personas'
        if ctx['nav'] in TITLES:
            return {**ctx, **root_table(ctx['nav'], self.request.query_params)}
        if ctx['nav'] == 'settings':
            return {**ctx, 'inference': inference.config()}
        raise NotFoundError404()

    def post(self):
        data = self.request.form_data
        if data.get("action") == "run":
            check = Check.query.get(id=int(data["check_id"]))
            run = check_account(check.account, self.user.email, check.provider, check.id)[0]
            return RedirectResponse(f"/runs/{run.id}/checks/{check.id}", status_code=303)
        if data.get("action") == "inference":
            model = data.get("model", "")
            if not model.startswith("openai/"):
                raise ValueError("Choose an OpenAI model")
            row, _ = AppConfig.query.get_or_create(key="inference", defaults={"value": {}})
            row.value = {"model": model, "enabled": data.get("enabled") == "on"}
            row.update(fields=["value"])
        return RedirectResponse("/?view=settings", status_code=303)


class PersonaView(Base):
    template_name = "persona.html"

    def get_template_context(self):
        ctx = super().get_template_context()
        persona = Persona.query.get(id=self.url_kwargs["id"])
        runs = list(
            Run.query.filter(persona=persona).join("provider").order_by("-created_at")[:100]
        )
        checkpoints = list(Checkpoint.query.filter(persona=persona).order_by("-created_at")[:100])
        accounts = account_rows(persona)
        inventory = credentials.inventory(
            persona,
            search=self.request.query_params.get("q", ""),
            kind=self.request.query_params.get("filter", "all"),
            page=self.request.query_params.get("page", 1),
        )
        all_checks = list(Check.query.filter(account__persona=persona, mode="check").join("provider"))
        tiles = health.check_rows(all_checks)
        by_check = {tile["check"]["id"]: tile for tile in tiles}
        for row in inventory["rows"]:
            row["check_results"] = health.grouped_checks([by_check[c["id"]] for c in row["checks"]])
            counts = health.check_counts(row["check_results"])
            row.update(counts)
            row["latest_at"] = max((t["at"] for t in row["check_results"] if t["at"]), default=None)
            account = next(
                (a for a in accounts if row["account"] and a["account"].id == row["account"]["id"]),
                None,
            )
            row["last_working"] = account["last_working"] if account else None
            row["detected"] = account["detected"] if account else None
            row["access_label"], row["access_tone"] = (
                ("Needs attention", "failed")
                if counts["failed"]
                else ("Working", "success")
                if counts["total"] and counts["passed"] == counts["total"]
                else ("Not confirmed", "neutral")
                if counts["total"]
                else ("Not checked", "neutral")
            )
        declarations = {}
        connections = {c.provider.id: c.provider for c in all_checks}
        for provider in connections.values():
            for key, claim in adapter(provider).settings_support(persona.config).items():
                declarations.setdefault(key, []).append((provider.name, claim))
        support = {}
        for key, claims in declarations.items():
            first = claims[0][1]
            support[key] = {
                "supported": first.get("supported")
                if all(c.get("supported") == first.get("supported") for _, c in claims)
                else None,
                "detail": " · ".join(name + ": " + c.get("detail", "") for name, c in claims),
            }
            if "default" in first and all(c.get("default") == first["default"] for _, c in claims):
                support[key]["default"] = first["default"]
        saved = services.leader_for(persona).checkpoint
        history = sorted(
            [
                {**event, "site": row["site"], "username": row["account"].username}
                for row in accounts
                for event in row["history"]
            ],
            key=lambda event: event["at"],
            reverse=True,
        )
        ctx.update(
            persona=persona,
            credential_inventory=inventory,
            check_summary=health.check_counts(health.grouped_checks(tiles)),
            browser_settings=browser_settings.inventory(
                persona.config, support=support, checkpoint=saved
            ),
            settings_connections=list(connections.values()),
            runs=runs,
            checkpoints=checkpoints,
            config_json=json.dumps(persona.config, indent=2),
            accounts=accounts,
            retained_versions=[
                row["current"]["run"]
                for row in accounts
                if row["current"]
                and row["current"]["access_confirmed"]
                and row["current"]["run"].issues
            ],
            history=history[:30],
            active=any(r["active"] for r in accounts),
            checkable=any(r["check"] for r in accounts),
            working=sum(r["state"] == "accessible" for r in accounts),
            attention=sum(r["state"] not in ("accessible", "unchecked") for r in accounts),
            nav="personas",
        )
        return ctx

    def post(self):
        persona = Persona.query.get(id=self.url_kwargs["id"])
        account_id = self.request.form_data.get("account")
        if account_id:
            account = Account.query.get(id=int(account_id), persona=persona)
            runs = check_account(account, self.user.email)
            return RedirectResponse(f"/runs/{runs[0].id}" if len(runs)==1 else f"/personas/{persona.id}", status_code=303)
        raise ValidationError("Choose a site or a check to run")


class RunView(Base):
    template_name = "run.html"

    def get_template_context(self):
        ctx = super().get_template_context()
        run = Run.query.get(id=self.url_kwargs["id"])
        observations = list(CheckRun.query.filter(run=run).order_by("created_at"))
        check_id = self.url_kwargs.get("check_id")
        focused = next((p for p in run.plan if p["id"] == check_id), None)
        if check_id is not None and focused is None:
            raise NotFoundError404()
        sessions = sessions_for(run, observations)
        if focused:
            observations = [o for o in observations if o.check_id == check_id]
            sessions = [s for s in sessions if s["check_id"] == str(check_id)]
        active = not run.checked_in_at and (not focused or not observations)
        ctx.update(
            run=run,
            session_info=health.session_rows([run])[0] if not focused else None,
            observations=observations,
            focused=focused,
            plans=[focused] if focused else run.plan,
            report=health.report(run, observations, check_id=check_id),
            sessions=sessions,
            steps=browser_steps(run, check_id=check_id),
            nav="runs",
            frame_version=(storage.data_root() / "runs" / str(run.id) / "live.jpg")
            .stat()
            .st_mtime_ns
            if (storage.data_root() / "runs" / str(run.id) / "live.jpg").exists()
            else 0,
            failure_image=not focused and (storage.data_root() / "runs" / str(run.id) / "failure.png").exists(),
            checkpoints=list(Checkpoint.query.filter(branch=str(run.id))),
            active=active,
            current_leader=Leader.query.filter(
                persona=run.persona, checkpoint__digest=run.tip
            ).exists()
            if run.tip
            else False,
            live=not focused and (storage.data_root() / "runs" / str(run.id) / "live.jpg").exists(),
        )
        return ctx


def browser_steps(run, check_id=None):
    work = storage.data_root() / "runs" / str(run.id) / "agent-workspace"
    steps = []
    for path in sorted(work.glob("*.json"), key=lambda p: p.stat().st_mtime):
        try:
            step = json.loads(path.read_text())
        except OSError, ValueError:
            continue
        if check_id is not None and str(step.get("check_id")) != str(check_id):
            continue
        name = step.get("screenshot", "")
        if name and Path(name).name == name and name.endswith(".png") and (work / name).is_file():
            steps.append(step)
    return steps


class EvidenceView(AuthView):
    admin_required = True

    def get(self):
        run = Run.query.get(id=self.url_kwargs["id"])
        filename = self.url_kwargs["filename"]
        allowed = {o.evidence.get("screenshot") for o in CheckRun.query.filter(run=run)}
        if not run.runtime.get("external"):
            allowed.update(["live.jpg", "failure.png"])
            allowed.update(step["screenshot"] for step in browser_steps(run))
        if filename not in allowed or Path(filename).name != filename:
            raise NotFoundError404()
        path = storage.data_root() / "runs" / str(run.id) / filename
        if not path.is_file():
            path = path.parent / "agent-workspace" / filename
        if not path.is_file():
            raise NotFoundError404()
        from .core.images import screenshot_format

        with path.open("rb") as image:
            content_type = "image/" + screenshot_format(image.read(12))
        return FileResponse(
            path.open("rb"),
            content_type=content_type,
            headers={"Cache-Control": "no-store"},
        )


EDITABLE = {
    "persona": Persona,
    "provider": Provider,
    "site": Site,
    "account": Account,
    "check": Check,
}


class Editor(Base):
    template_name = "editor.html"
    error = ""

    def get_template_context(self):
        ctx = super().get_template_context()
        kind = self.url_kwargs["kind"]
        if kind not in EDITABLE and kind != "import":
            raise NotFoundError404()
        identifier = self.request.query_params.get("id")
        obj = (
            EDITABLE[kind].query.get(id=int(identifier))
            if identifier and kind in EDITABLE
            else None
        )
        ctx.update(
            kind=kind,
            obj=obj,
            error=self.error,
            values=self.request.form_data if self.request.method == "POST" else {},
            config_json=json.dumps(getattr(obj, "config", {}), indent=2),
            browser_settings_fields=browser_settings.edit_context(
                getattr(obj, "config", {}),
                values=self.request.form_data if self.request.method == "POST" else None,
            ),
            selected_account=int(self.request.query_params.get("account", 0)),
            selected_persona=int(self.request.query_params.get("persona", 0)),
            selected_domain=self.request.query_params.get("domain", ""),
            personas=list(Persona.query.all()),
            sites=list(Site.query.all()),
            providers=list(Provider.query.all()),
            provider_options=provider_options(),
            accounts=list(Account.query.all()),
        )
        if kind == "import":
            from .core.importers import discover

            ctx["discovered"] = discover()
        return ctx

    def post(self):
        kind, data = self.url_kwargs["kind"], self.request.form_data
        identifier = self.request.query_params.get("id")
        try:
            obj = (
                EDITABLE[kind].query.get(id=int(identifier))
                if identifier and kind in EDITABLE
                else None
            )
            if kind == "persona":
                config = (
                    browser_settings.parse_fields(data, obj.config if obj else {})
                    if data.get("browser_settings_form")
                    else json.loads(data.get("config", "{}"))
                )
                if not isinstance(config, dict):
                    raise ValueError("Settings must be a JSON object")
                if obj:
                    obj.name, obj.description, obj.config = (
                        data["name"],
                        data.get("description", ""),
                        config,
                    )
                    obj.update(fields=["name", "description", "config"])
                else:
                    obj = services.create_persona(
                        data["name"], config, data.get("description", ""), self.user.email
                    )
                return RedirectResponse(f"/personas/{obj.id}", status_code=303)
            if kind == "import":
                from .core.importers import import_source
                from .core.site_scope import normalize_sites

                obj = Persona.query.get(id=int(data["persona"]))
                sites = None if data.get("site_mode") == "all" else normalize_sites(data.get("sites", ""))
                import_source(obj, data["browser"], data["path"], self.user.email, sites=sites)
                return RedirectResponse(f"/personas/{obj.id}", status_code=303)
            if kind == "site":
                domain = data["domain"].lower().strip().strip(".")
                if urlsplit("https://" + domain).hostname != domain or "/" in domain or not domain:
                    raise ValueError("Enter a domain, without a path or protocol")
                fields = {"domain": domain}
            elif kind == "account":
                domain = data.get("domain", "").lower().strip().strip(".")
                if domain:
                    if urlsplit("https://" + domain).hostname != domain or "/" in domain:
                        raise ValueError("Enter a domain, without a path or protocol")
                    site, _ = Site.query.get_or_create(domain=domain)
                else:
                    site = Site.query.get(id=int(data["site"]))
                fields = {
                    "persona": Persona.query.get(id=int(data["persona"])),
                    "site": site,
                    "username": data.get("username", ""),
                }
            elif kind == "provider":
                from .core.site_scope import normalize_sites
                color = data.get('color', '')
                if color and not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
                    raise ValueError('Choose a valid color')
                config = json.loads(data.get("config", "{}"))
                if not isinstance(config, dict):
                    raise ValueError("Settings must be a JSON object")
                if any(
                    "key" in k.lower() or "password" in k.lower() or "token" in k.lower()
                    for k in config
                ):
                    raise ValueError(
                        "Store credentials in the private provider env file, not in configuration"
                    )
                adapter(kind=data["provider_kind"]).validate_config(config)
                fields = {
                    "site_scope": (normalize_sites(data['site_scope']) if data['site_scope'].strip() else None)
                        if 'site_scope' in data else obj.site_scope if obj else None,
                    "color": color,
                    "name": data["name"],
                    "kind": data["provider_kind"],
                    "config": config,
                    "enabled": data.get("enabled") == "on",
                }
            elif kind == "check":
                instruction = data.get("instruction", "").strip()
                if not instruction:
                    raise ValueError("Describe the access you want to verify in plain English")
                url = data["url"]
                if urlsplit(url).scheme not in {"http", "https"}:
                    raise ValueError("Checks require an HTTP(S) URL")
                interval = int(data.get("interval_seconds", 3600))
                if interval < 60:
                    raise ValueError("The minimum interval is 60 seconds")
                fields = {
                    "account": Account.query.get(id=int(data["account"])),
                    "provider": Provider.query.get(id=int(data["provider"])),
                    "name": data["name"],
                    "instruction": instruction,
                    "url": url,
                    "interval_seconds": interval,
                    "enabled": data.get("enabled") == "on",
                }
            else:
                raise ValueError("Unknown editor")
            with transaction.atomic():
                if obj:
                    obj = EDITABLE[kind].query.for_update().get(id=obj.id)
                    for key, value in fields.items():
                        setattr(obj, key, value)
                    obj.update(fields=list(fields))
                else:
                    obj = EDITABLE[kind].query.create(**fields)
                if kind == "check":
                    services.record_definition(obj, self.user.email)
            if kind in {"check", "account"}:
                persona = obj.account.persona if kind == "check" else obj.persona
                return RedirectResponse(f"/personas/{persona.id}#credentials", status_code=303)
            return RedirectResponse(
                "/?view="
                + {
                    "check": "checks",
                    "provider": "providers",
                    "site": "sites",
                    "account": "overview",
                }[kind],
                status_code=303,
            )
        except (ValueError, ValidationError, IntegrityError) as exc:
            self.error = str(exc)
            return self.get()
