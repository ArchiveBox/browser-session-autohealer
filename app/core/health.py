"""User-facing access results, derived from complete recorded checks."""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from plain.postgres.functions import Coalesce
from plain.runtime import settings

from . import storage
from .models import Account, Check, CheckRun, Leader, Run

ACTIVE = {"queued", "starting", "running", "finishing"}
LABELS = {
    "accessible": ("Working", "success"),
    "login_required": ("Signed out", "failed"),
    "captcha_required": ("CAPTCHA blocking access", "failed"),
    "email_code_required": ("Email verification needed", "failed"),
    "sms_code_required": ("Text message verification needed", "failed"),
    "otp_required": ("Verification code needed", "failed"),
    "cookie_consent": ("Cookie notice blocking access", "failed"),
    "promo_blocked": ("Popup blocking access", "failed"),
    "rate_limited": ("Temporarily limited by the site", "failed"),
    "human_required": ("Needs review", "attention"),
    "needs_review": ("Needs review", "attention"),
    "unknown": ("Couldn't check", "attention"),
    "unchecked": ("Not checked yet", "neutral"),
    "checking": ("Checking now", "running"),
}


def time_label(value):
    return (
        value.astimezone(ZoneInfo(settings.TIME_ZONE)).strftime("%b %-d, %-I:%M %p %Z")
        if value
        else "Not yet"
    )


def access_summary(state):
    """Stable user copy; agent explanations remain in the recorded diagnostics."""
    return {
        "accessible": "The requested content is available.",
        "login_required": "Sign in to access this page.",
        "captcha_required": "The site requires a CAPTCHA before you can continue.",
        "email_code_required": "Check your email to finish signing in.",
        "sms_code_required": "Check your text messages to finish signing in.",
        "otp_required": "A verification code is needed to finish signing in.",
        "cookie_consent": "A cookie notice is covering the content.",
        "promo_blocked": "A popup is covering the content.",
        "rate_limited": "The site is temporarily limiting access.",
        "checking": "Checking whether this page is available.",
        "unchecked": "This page has not been checked yet.",
    }.get(state, "Access could not be confirmed. Review the screenshot.")


def report(run, observations, domain=None, check_id=None):
    plan = [p for p in run.plan if (domain is None or p["domain"] == domain)
            and (check_id is None or p["id"] == check_id)]
    ids = {p["id"] for p in plan}
    observations = [o for o in observations if o.check_id in ids]
    latest = {o.check_id: o for o in observations}
    failed = next((o for o in latest.values() if not o.passed), None)
    if run.status in ACTIVE and (check_id is None or not latest):
        state = "checking"
    elif failed:
        state = (
            failed.state if failed.state not in ("accessible", "human_required") else "needs_review"
        )
    elif not plan:
        state = "unchecked"
    elif len(latest) != len(plan):
        state = "unknown"
    elif run.issues and check_id is None and domain is None:
        state = "needs_review"
    else:
        state = "accessible"
    label, tone = LABELS.get(state, LABELS["needs_review"])
    thumbnail = (
        failed
        if failed and failed.evidence.get("screenshot")
        else next((o for o in observations if o.evidence.get("screenshot")), None)
    )
    return {
        "run": run,
        "state": state,
        "label": label,
        "tone": tone,
        "reason": access_summary(state),
        "at": max((o.ended_at or o.created_at for o in latest.values()),
                  default=run.finished_at or run.started_at or run.created_at),
        "next_step": next_step(state),
        "observations": observations,
        "thumbnail": thumbnail,
        "access_confirmed": bool(plan)
        and len(latest) == len(plan)
        and all(o.passed for o in latest.values()),
        "passed": sum(o.passed for o in latest.values()),
        "total": len(plan),
    }


def next_step(state):
    if state == "accessible":
        return "Ready for research."
    if state == "checking":
        return "The result will appear here when the check finishes."
    if state == "login_required":
        return "Open Login help to restore access, then check again."
    if state in ("captcha_required", "email_code_required", "sms_code_required", "otp_required"):
        return "Open Login help and complete the site’s verification."
    if state in ("cookie_consent", "promo_blocked"):
        return "Dismiss the notice in the browser, then check again."
    if state == "rate_limited":
        return "Wait before checking again. Follow any waiting period shown by the site."
    return "Review the screenshot or open the conversation for help."


def session_rows(runs):
    runs = list(runs)
    leaders = set(Leader.query.values_list("checkpoint__digest", flat=True))
    observations = defaultdict(list)
    for result in CheckRun.query.filter(run__id__in=[r.id for r in runs]).join("run").order_by("created_at"):
        observations[result.run.id].append(result)
    rows = []
    for run in runs:
        active = run.status in ACTIVE
        disposition = (
            "In use" if active else "Persona leader" if run.tip in leaders
            else "Superseded" if run.promoted else "Discarded"
        )
        duration = (run.finished_at - run.started_at).total_seconds() if run.started_at and run.finished_at else None
        rows.append({"run": run, "active": active, "disposition": disposition,
            "tone": "running" if active else "success" if run.tip in leaders else "neutral" if run.promoted else "failed",
            "source": run.actor if run.runtime.get("external") else "Browser Session Autohealer",
            "duration": f"{int(duration // 60)}m {int(duration % 60)}s" if duration is not None else "",
            "checks": [{"result": o, "image": result_image(o)[0],
                "url": f"/runs/{run.id}/checks/{o.check_id}",
                "domain": next((p["domain"] for p in run.plan if p["id"] == o.check_id), "")}
                for o in observations[run.id]],
            "passed": sum(o.passed for o in observations[run.id]),
            "total": len(run.plan)})
    return rows


def account_rows(persona=None):
    query = Account.query.filter(persona=persona) if persona else Account.query.all()
    accounts = list(query.join("persona", "site"))
    checks_by_account = defaultdict(list)
    for check in (
        Check.query.filter(account__id__in=[a.id for a in accounts], mode="check")
        .join("account", "provider")
        .order_by("id")
    ):
        checks_by_account[check.account.id].append(check)
    result, histories = [], {}
    for account in accounts:
        persona_id = account.persona.id
        if persona_id not in histories:
            runs = list(
                Run.query.filter(persona=account.persona)
                .join("provider")
                .order_by("-created_at")[:200]
            )
            observations = defaultdict(list)
            for o in (
                CheckRun.query.filter(run__id__in=[r.id for r in runs])
                .join("run")
                .order_by("created_at")
            ):
                observations[o.run.id].append(o)
            histories[persona_id] = runs, observations
        runs, observations = histories[persona_id]
        domain = account.site.domain
        checks = checks_by_account[account.id]
        enabled = [c for c in checks if c.enabled]
        available = [c for c in enabled if c.provider.enabled]
        relevant = [
            r for r in runs if r.scope == domain or any(p["domain"] == domain for p in r.plan)
        ]
        active = next((r for r in relevant if r.status in ACTIVE), None)
        history = sorted(
            [
                report(r, observations[r.id], domain)
                for r in relevant
                if r.status not in ACTIVE and any(p["domain"] == domain for p in r.plan)
            ],
            key=lambda h: h["at"],
            reverse=True,
        )
        current = history[0] if history else None
        # Every enabled condition/provider needs its own current proof.
        tiles = check_rows(enabled)
        if current and tiles:
            worst = next((t for t in tiles if t["tone"] in {"failed", "attention"}), None)
            worst = worst or next((t for t in tiles if t["state"] != "accessible"), tiles[0])
            current = {**current, "state": worst["state"], "label": worst["label"],
                "tone": worst["tone"], "reason": worst["summary"]}
        good = next((h for h in history if h["access_confirmed"]), None)
        detected = None
        if current and current["state"] not in ("accessible", "unchecked"):
            for h in history:
                if h["state"] == "accessible":
                    break
                detected = h["at"]
        result.append(
            {
                "account": account,
                "persona": account.persona,
                "site": account.site,
                "checks": checks,
                "check": available[0] if available else None,
                "monitoring": bool(available),
                "current": current,
                "history": history,
                "active": active,
                "state": current["state"] if current else "unchecked",
                "label": current["label"] if current else "Not checked yet",
                "tone": current["tone"] if current else "neutral",
                "reason": current["reason"]
                if current
                else (
                    "Run a check to confirm access."
                    if checks
                    else "Add a check to find out whether this account can access the site."
                ),
                "screenshot": next((h["thumbnail"] for h in history if h["thumbnail"]), None),
                "last_checked": current["at"] if current else None,
                "last_working": good["at"] if good else None,
                "detected": detected,
                "next_due": min((c.next_due for c in available if c.next_due), default=None),
            }
        )
    return result


def result_image(result):
    if not result:
        return "", "No evidence yet"
    screenshot = Path(result.screenshot).name if result.screenshot else ""
    label = "Final evidence"
    # A crashed renderer can leave a blank failure capture. Retain its last
    # actual frame only when it was captured during this specific check.
    if result.status == "failure" and screenshot in {"", "failure.png"}:
        work = storage.data_root() / "runs" / str(result.run.id)
        try:
            captured = datetime.fromisoformat(
                json.loads((work / "live.json").read_text())["captured_at"]
            )
            if (
                result.started_at
                and result.ended_at
                and result.started_at <= captured <= result.ended_at
                and (work / "live.jpg").is_file()
            ):
                screenshot, label = "live.jpg", "Last live frame · check incomplete"
        except OSError, ValueError, KeyError, TypeError:
            pass
    return (f"/evidence/{result.run.id}/{screenshot}" if screenshot else ""), label


def check_rows(checks):
    """Latest actual execution per configured check; no arbitrary history cutoff."""
    checks = list(checks)
    identifiers = [c["id"] if isinstance(c, dict) else c.id for c in checks]
    latest = {
        result.check_id: result
        for result in CheckRun.query.filter(
            check_id__in=identifiers, status__in=["success", "failure"]
        )
        .join("run")
        .order_by("check_id", Coalesce("ended_at", "started_at", "run__created_at").desc(), "-id")
        .distinct("check_id")
    }
    active = {
        r.check_id: r
        for r in CheckRun.query.filter(check_id__in=identifiers, status="running")
        .join("run")
        .order_by("started_at")
    }
    rows = []
    for check in checks:
        value = (
            check
            if isinstance(check, dict)
            else {
                "id": check.id,
                "account_id": check.account.id,
                "name": check.name,
                "url": check.url,
                "instruction": check.instruction,
                "enabled": check.enabled,
                "interval_seconds": check.interval_seconds,
                "next_due": check.next_due,
                "provider": {
                    "id": check.provider.id,
                    "name": check.provider.name,
                    "enabled": check.provider.enabled,
                },
            }
        )
        result = latest.get(value["id"])
        running = active.get(value["id"])
        if result is None:
            result = running
        plan = next((p for p in result.run.plan if p["id"] == value["id"]), {}) if result else {}
        changed = bool(
            result
            and (plan.get("instruction") != value["instruction"] or plan.get("url") != value["url"])
        )
        state = "unchecked"
        if result:
            state = (
                "checking"
                if result.status == "running"
                else (
                    "accessible"
                    if result.status == "success"
                    else result.state
                    if result.state != "accessible"
                    else "needs_review"
                )
            )
        label, tone = LABELS.get(state, LABELS["needs_review"])
        if changed:
            state, label, tone = "unchecked", "Updated · needs checking", "neutral"
        elif state == "accessible":
            label = "Passed"
        image, image_label = result_image(result)
        rows.append(
            {
                "check": value,
                "result": result,
                "running": running,
                "changed": changed,
                "state": state,
                "label": label,
                "tone": tone,
                "next_step": next_step(state) if tone in {"failed", "attention"} else "",
                "image": image,
                "image_label": image_label,
                "url": f"/runs/{result.run.id}/checks/{value['id']}"
                if result
                else f"/edit/check?id={value['id']}",
                "at": (result.ended_at or result.started_at or result.created_at)
                if result
                else None,
                "summary": access_summary(state),
                "duration": round((result.ended_at - result.started_at).total_seconds(), 1)
                if result and result.started_at and result.ended_at
                else None,
            }
        )
    return rows


def grouped_checks(rows):
    """One visible condition, with the latest execution and provider comparisons."""
    grouped = defaultdict(list)
    for row in rows:
        check = row["check"]
        key = (check.get("account_id"), check["name"], check["url"], check["instruction"])
        grouped[key].append(row)
    result = []
    for providers in grouped.values():
        providers.sort(key=lambda row: row["at"].timestamp() if row["at"] else 0, reverse=True)
        result.append({**providers[0], "provider_results": providers,
            "member_ids": [row["check"]["id"] for row in providers]})
    return result


def check_counts(rows):
    return {
        "total": len(rows),
        "passed": sum(all(p["tone"] == "success" for p in t.get("provider_results", [t])) for t in rows),
        "failed": sum(any(p["tone"] in {"failed", "attention"} for p in t.get("provider_results", [t])) for t in rows),
    }
