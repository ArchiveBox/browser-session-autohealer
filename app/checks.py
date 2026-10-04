"""A check's recorded history and its state at a chosen log boundary."""

import json
from datetime import UTC, datetime
from pathlib import Path

from .core import services
from .core.models import Check, CheckEvent
from .views import Base


def boundary(value):
    if not value:
        return None
    at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        raise ValueError("Include a timezone, for example 2026-10-04T03:00:00Z")
    return at


class CheckHistoryView(Base):
    template_name = "check_history.html"

    def get_template_context(self):
        ctx = super().get_template_context()
        check = Check.query.get(id=self.url_kwargs["id"])
        value = self.request.query_params.get("at", "")
        error = ""
        try:
            parsed = datetime.fromisoformat(value) if value else None
            at = parsed.replace(tzinfo=UTC) if parsed and parsed.tzinfo is None else parsed
        except ValueError as exc:
            error, at = str(exc), None
        projection = services.check_projection(check.uid, at)
        events = CheckEvent.query.filter(check_uid=check.uid)
        if at:
            events = events.filter(occurred_at__lte=at)
        ctx.update(
            nav="checks",
            check=check,
            projection=projection,
            at=at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") if at else "",
            error=error,
            events=list(events.order_by("-occurred_at", "-id")[:100]),
            projected_runs=sorted(
                projection["runs"].values(),
                key=lambda r: r.get("started_at") or r.get("ended_at") or "",
                reverse=True,
            ),
            filename=lambda value: Path(value).name,
            pretty=lambda value: json.dumps(value, indent=2, ensure_ascii=False),
        )
        return ctx
