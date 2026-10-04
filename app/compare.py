"""Side-by-side access evidence and provenance of saved browser values."""

import json
from collections import Counter, defaultdict
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from plain.runtime import settings

from .core import browser_settings, health, storage
from .core.models import Checkpoint, CheckRun, Persona, Run
from .views import Base


def revision_time(checkpoint):
    return checkpoint.created_at.astimezone(ZoneInfo(settings.TIME_ZONE))


def recorded_at(checkpoint):
    return revision_time(checkpoint).strftime("%b %-d, %Y · %-I:%M:%S %p %Z")


def source_name(checkpoint):
    source = checkpoint.source
    return source.get("provider") or ("Browser import" if source else "Persona created")


def setting_value(key, value, present):
    if not present:
        return "Not set"
    # Arbitrary settings may contain credentials; only display known preferences.
    if key not in {"locale", "timezone", "viewport", "colorScheme", "reducedMotion"}:
        return "Setting changed"
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)


def evidence_for(checkpoint, run, observations):
    result = {
        "checkpoint": checkpoint,
        "time": recorded_at(checkpoint),
        "provider": source_name(checkpoint),
        "report": health.report(run, observations) if run else None,
        "sites": {},
    }
    if run:
        latest = {observation.check_id: observation for observation in observations}
        for domain in sorted({p["domain"] for p in run.plan}):
            site = health.report(run, observations, domain)
            # Show each check's own final screenshot and link, including failures.
            site["checks"] = [
                {
                    "name": plan["name"],
                    "url": f"/runs/{run.id}/checks/{plan['id']}",
                    "image": health.result_image(observation)[0],
                    "at": health.time_label(observation.ended_at or observation.created_at),
                    "state": observation.state,
                    "label": health.LABELS.get(observation.state, health.LABELS["needs_review"])[0],
                    "tone": "success" if observation.passed else "failed",
                }
                for plan in run.plan
                if plan["domain"] == domain
                for observation in [latest.get(plan["id"])]
                if observation
            ]
            result["sites"][domain] = site
    return result


class ValueHistory:
    """Walk real parent links, not wall-clock neighbours or a different branch."""

    def __init__(self, checkpoints, runs, observations):
        self.checkpoints = checkpoints
        self.runs = runs
        self.observations = observations
        self._values = {}

    def values(self, digest, category):
        if (digest, category) in self._values:
            return self._values[digest, category]
        state = storage.read_state(digest)
        if category == "cookies":
            values = {storage.cookie_key(c): c for c in state.get(category, [])}
        elif category == "origins":
            values = {o["origin"]: o for o in state.get(category, [])}
        else:
            values = state.get(category, {})
        self._values[digest, category] = values
        return values

    def origin(self, checkpoint, category, key, *, value_only=False):
        def identity(cp):
            values = self.values(cp.digest, category)
            item = values.get(key)
            if value_only and category == "cookies" and item is not None:
                item = item.get("value")
            return key in values, item

        expected = identity(checkpoint)
        seen = {checkpoint.digest}
        while checkpoint.parent in self.checkpoints:
            parent = self.checkpoints[checkpoint.parent]
            if (
                parent.digest in seen
                or parent.persona.id != checkpoint.persona.id
                or identity(parent) != expected
            ):
                break
            seen.add(parent.digest)
            checkpoint = parent
        return checkpoint

    def source(self, checkpoint, domain):
        run = self.runs.get(checkpoint.source.get("run"))
        evidence = evidence_for(checkpoint, run, self.observations.get(run.id, []) if run else [])
        site = evidence["sites"].get(domain)
        return {
            "digest": checkpoint.digest,
            "at": recorded_at(checkpoint),
            "source": source_name(checkpoint),
            "timestamp": checkpoint.created_at,
            "location": checkpoint.source.get("hostname", "") if not run else "",
            "checks": site["checks"] if site else [],
            "run_id": run.id if run else None,
        }

    def cell(self, checkpoint, change, domain):
        category, key = change["category"], change["key"]
        present = key in self.values(checkpoint.digest, category)
        origin = self.origin(checkpoint, category, key, value_only=True)
        changed = self.origin(checkpoint, category, key)
        return {
            "present": present,
            "origin": self.source(origin, domain),
            "change": self.source(changed, domain),
            "inherited": origin.digest != checkpoint.digest,
        }


def change_groups(changes, left, right, history):
    groups = {}
    domains = left["sites"].keys() | right["sites"].keys()
    labels = {key: label for _, _, fields in browser_settings.GROUPS for key, label in fields}
    for change in changes:
        category, key, kind = change["category"], change["key"], change["kind"]
        domain = ""
        if category == "cookies":
            name, host, path, partition = json.loads(key)
            host = (host or "").lstrip(".")
            title, detail = (
                name,
                f"{host} · {path or '/'}" + (" · partitioned" if partition else ""),
            )
            domain = next(
                (
                    d
                    for d in sorted(domains, key=len, reverse=True)
                    if host == d or host.endswith("." + d)
                ),
                host,
            )
        elif category == "origins":
            host = urlsplit(key).hostname or key
            domain = next(
                (
                    d
                    for d in sorted(domains, key=len, reverse=True)
                    if host == d or host.endswith("." + d)
                ),
                host,
            )
            title, detail = "Site preferences & session data", key
        else:
            title, detail = labels.get(key, key), "Browser setting"
        before = history.cell(left["checkpoint"], change, domain)
        after = history.cell(right["checkpoint"], change, domain)
        fields = change.get("changed_fields", [])
        value_changed = kind != "updated" or category != "cookies" or "value" in fields
        for side, cell in (("before", before), ("after", after)):
            cell["label"] = "Recorded value" if value_changed else "Same value"
            if not cell["present"]:
                cell["label"] = "Not present"
                origin_cp = history.checkpoints[cell["origin"]["digest"]]
                if origin_cp.parent in history.checkpoints and key in history.values(
                    origin_cp.parent, category
                ):
                    cell["label"] = "Removed"
            if category == "settings":
                cell["value"] = setting_value(key, change[side], cell["present"])
            else:
                cell["value"] = ""
        if value_changed and before["present"] and after["present"]:
            newer = (
                "before"
                if before["origin"]["timestamp"] > after["origin"]["timestamp"]
                else "after"
            )
            (before if newer == "before" else after)["label"] = "Newer value"
            (after if newer == "before" else before)["label"] = "Older value"
        details_changed = ["expiry" if f == "expires" else f for f in fields if f != "value"]
        group = groups.setdefault(
            domain or "Browser settings", {"name": domain or "Browser settings", "rows": []}
        )
        group["rows"].append(
            {
                "title": title,
                "detail": detail,
                "kind": kind,
                "label": {"added": "Added", "removed": "Removed", "updated": "Changed"}[kind],
                "before": before,
                "after": after,
                "details_changed": ", ".join(details_changed),
                "value_changed": value_changed,
            }
        )
    return [groups[key] for key in sorted(groups)]


class CompareView(Base):
    template_name = "compare.html"

    def get_template_context(self):
        ctx = super().get_template_context()
        query = self.request.query_params
        personas = list(Persona.query.order_by("name"))
        checkpoints = list(Checkpoint.query.join("persona").order_by("-created_at"))
        by_digest = {c.digest: c for c in checkpoints}
        base, head = by_digest.get(query.get("base")), by_digest.get(query.get("head"))
        error = ""
        if (query.get("base") and not base) or (query.get("head") and not head):
            error = "One of these versions could not be found. Choose another below."
        selected_persona = query.get("persona")
        if selected_persona is None:
            chosen = head or base or (checkpoints[0] if checkpoints else None)
            selected_persona = str(chosen.persona.id) if chosen else ""
        if base and head and base.persona.id != head.persona.id:
            selected_persona = ""
        available = [
            c for c in checkpoints if not selected_persona or str(c.persona.id) == selected_persona
        ]
        if any(c and c not in available for c in (base, head)):
            selected_persona, available = "", checkpoints
        if not base and not head and not error and available:
            head = available[0]
            base = next((c for c in available[1:] if c.persona.id == head.persona.id), None)
        runs = {
            r.id: r
            for r in Run.query.filter(
                id__in=[c.source["run"] for c in checkpoints if c.source.get("run")]
            ).join("provider")
        }
        observations = defaultdict(list)
        for observation in (
            CheckRun.query.filter(run__id__in=list(runs)).join("run").order_by("created_at")
        ):
            observations[observation.run.id].append(observation)
        groups = {}
        for checkpoint in available:
            run = runs.get(checkpoint.source.get("run"))
            report = health.report(run, observations[run.id]) if run else None
            at = revision_time(checkpoint)
            group = (checkpoint.persona.name, at.strftime("%A, %b %-d, %Y"))
            label = f"{at.strftime('%-I:%M:%S %p %Z')} · {source_name(checkpoint)}"
            if report:
                label += f" · {report['label']}"
            groups.setdefault(group, []).append({"checkpoint": checkpoint, "label": label})
        revision_groups = [
            {"persona": p, "day": d, "revisions": revisions} for (p, d), revisions in groups.items()
        ]
        revision_groups.sort(key=lambda g: g["persona"].casefold())
        sides = [
            evidence_for(
                c, runs.get(c.source.get("run")), observations.get(c.source.get("run"), [])
            )
            if c
            else None
            for c in (base, head)
        ]
        left, right = sides
        changes = storage.compare(base.digest, head.digest) if base and head else []
        site_rows = []
        if left and right:
            for domain in sorted(left["sites"].keys() | right["sites"].keys()):
                a, b = left["sites"].get(domain), right["sites"].get(domain)
                site_rows.append(
                    {
                        "domain": domain,
                        "before": a,
                        "after": b,
                        "changed": bool(a and b and a["state"] != b["state"]),
                    }
                )
        ctx.update(
            nav="compare",
            personas=personas,
            selected_persona=selected_persona,
            revision_groups=revision_groups,
            base=base,
            head=head,
            left=left,
            right=right,
            error=error,
            changes=changes,
            change_groups=change_groups(
                changes, left, right, ValueHistory(by_digest, runs, observations)
            )
            if base and head
            else [],
            counts=Counter(c["kind"] for c in changes),
            site_rows=site_rows,
            cross_persona=bool(base and head and base.persona.id != head.persona.id),
            newer="left"
            if base and head and base.created_at > head.created_at
            else "right"
            if base and head and base.created_at < head.created_at
            else "same",
            swap_url="/compare?" + urlencode({"base": head.digest, "head": base.digest})
            if base and head
            else "",
        )
        return ctx
