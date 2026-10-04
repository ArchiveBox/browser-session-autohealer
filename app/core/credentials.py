"""Paginated, value-free inventory of configured accounts and saved browser state.

The checkpoint summary is a derived index, not an authentication verdict. Its
first read backfills metadata once; subsequent reads never open encrypted state.
"""

from collections import defaultdict
from math import ceil
from urllib.parse import urlsplit

from . import storage
from .models import Account, Check, Checkpoint, Leader, Persona

SUMMARY_KEY = "credential_inventory"
SUMMARY_VERSION = 2


def summarize_state(state):
    """Extract exact host counts and recorded storage coverage, never values."""
    domains = {}
    for cookie in state.get("cookies", []):
        domain = cookie.get("domain", "").lstrip(".")
        if domain:
            row = domains.setdefault(
                domain,
                {
                    "cookie_count": 0,
                    "domain_cookie_count": 0,
                    "host_only_cookie_count": 0,
                    "origins": [],
                },
            )
            row["cookie_count"] += 1
            # Explicit hostOnly is authoritative. Legacy Chrome exports omit it,
            # but retain the leading dot for cookies with domain scope. Treat an
            # unmarked host conservatively as host-only, never broaden its scope.
            host_only = cookie.get("hostOnly", not cookie["domain"].startswith("."))
            row["host_only_cookie_count" if host_only else "domain_cookie_count"] += 1
    for saved in state.get("origins", []):
        origin = saved.get("origin", "")
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        # Only valid origins belong in the index, never userinfo, paths or queries.
        if parsed.username or parsed.password or parsed.path not in {"", "/"}:
            continue
        if parsed.query or parsed.fragment:
            continue
        row = domains.setdefault(
            parsed.hostname,
            {
                "cookie_count": 0,
                "domain_cookie_count": 0,
                "host_only_cookie_count": 0,
                "origins": [],
            },
        )
        row["origins"].append(
            {
                "origin": origin,
                "local_storage_count": len(saved["localStorage"])
                if isinstance(saved.get("localStorage"), (dict, list))
                else None,
                "session_storage_count": len(saved["sessionStorage"])
                if isinstance(saved.get("sessionStorage"), (dict, list))
                else None,
            }
        )
    return {
        "version": SUMMARY_VERSION,
        "domains": dict(sorted(domains.items())),
        "setting_names": sorted(state.get("settings", {})),
    }


def checkpoint_inventory(checkpoint):
    """Read/backfill a joined checkpoint dict, without changing its object/hash."""
    summary = checkpoint["summary"]
    inventory = summary.get(SUMMARY_KEY)
    if isinstance(inventory, dict) and inventory.get("version") == SUMMARY_VERSION:
        return inventory
    # No DB transaction/cursor is held while reading and decrypting the snapshot.
    inventory = summarize_state(storage.read_state(checkpoint["digest"]))
    # Compare-and-set protects unrelated summary metadata from concurrent writers.
    Checkpoint.query.filter(id=checkpoint["id"], summary=summary).update(
        summary={**summary, SUMMARY_KEY: inventory}
    )
    checkpoint["summary"] = {**summary, SUMMARY_KEY: inventory}
    return inventory


def accepted_sources(persona_ids):
    """Choose one actual accepted snapshot per persona, never a rejected run tip."""
    leaders = list(
        Leader.query.filter(persona__id__in=persona_ids).values(
            "persona__id",
            "verified",
            "finished_at",
            "checkpoint__id",
            "checkpoint__digest",
            "checkpoint__created_at",
            "checkpoint__summary",
            "checkpoint__coverage",
        )
    )
    sources = {}
    for leader in leaders:
        persona_id = leader["persona__id"]
        at = leader["finished_at"] or leader["checkpoint__created_at"]
        sources[persona_id] = {
            "id": leader["checkpoint__id"],
            "digest": leader["checkpoint__digest"],
            "created_at": leader["checkpoint__created_at"],
            "accepted_at": at,
            "summary": leader["checkpoint__summary"],
            "coverage": leader["checkpoint__coverage"],
            "verified": leader["verified"],
        }
    return sources


def inventory(persona=None, *, search="", kind="all", page=1, page_size=25):
    """Return a bounded page of rows plus counts, filter state and source metadata.

    ``persona`` accepts a Persona or integer ID; None selects all personas. Rows
    contain plain account/check dictionaries for direct use in editable views.
    Domains are exact hosts with leading cookie dots removed; parent domains and
    subdomains remain separate. ``inherited_cookie_count`` counts only saved
    domain-scoped cookies at parent hosts, independently of exact-host cookies.
    These are domain-scope counts, not a claim of access or a request-specific
    cookie header (which also depends on path, expiry, scheme and partition).
    """
    try:
        page_size = int(page_size)
    except TypeError, ValueError:
        page_size = 25
    if page_size not in {25, 50}:
        page_size = 25
    try:
        page = max(1, int(page))
    except TypeError, ValueError:
        page = 1
    if kind not in {"all", "configured", "saved", "unmonitored"}:
        kind = "all"
    search = str(search or "").strip()
    personas = list(Persona.query.order_by("name"))
    selected_id = getattr(persona, "id", persona)
    if selected_id == "":
        selected_id = None
    selected = [p for p in personas if selected_id is None or str(p.id) == str(selected_id)]
    persona_map = {p.id: p for p in selected}
    sources = accepted_sources(list(persona_map))
    rows = {}
    domain_scopes = {}

    def row_for(persona_id, domain):
        key = (persona_id, domain)
        if key not in rows:
            source = sources.get(persona_id)
            rows[key] = {
                "persona": persona_map[persona_id],
                "domain": domain,
                "account": None,
                "username": "",
                "checks": [],
                "cookie_count": 0
                if source and source["coverage"].get("cookies") != "native-only"
                else None,
                "domain_cookie_count": 0,
                "host_only_cookie_count": 0,
                "inherited_cookie_count": 0,
                "origins": [],
                "checkpoint": source,
                "hosts": [],
                "source_at": source["created_at"] if source else None,
                "coverage": source["coverage"] if source else {},
                "monitored": False,
            }
        return rows[key]

    for persona_id, source in sources.items():
        metadata = checkpoint_inventory(source)
        source["setting_names"] = metadata["setting_names"]
        domain_scopes[persona_id] = defaultdict(int)
        for domain, saved in metadata["domains"].items():
            row_for(persona_id, domain).update(
                cookie_count=saved["cookie_count"],
                domain_cookie_count=saved["domain_cookie_count"],
                host_only_cookie_count=saved["host_only_cookie_count"],
                origins=saved["origins"],
            )
            domain_scopes[persona_id][domain.casefold()] += saved["domain_cookie_count"]
        # Keep the full cached index out of the paginated response payload.
        source.pop("summary")
    # Joined projections use one query each, with no lazy foreign-key lookups.
    accounts = list(
        Account.query.filter(persona__id__in=list(persona_map)).values(
            "id", "persona__id", "site__id", "site__domain", "username"
        )
    )
    checks_by_account = defaultdict(list)
    for check in Check.query.filter(account__id__in=[a["id"] for a in accounts], mode="check").values(
        "id",
        "account__id",
        "name",
        "instruction",
        "url",
        "enabled",
        "interval_seconds",
        "next_due",
        "provider__id",
        "provider__name",
        "provider__enabled",
    ):
        checks_by_account[check["account__id"]].append(
            {
                "id": check["id"],
                "name": check["name"],
                "instruction": check["instruction"],
                "url": check["url"],
                "enabled": check["enabled"],
                "interval_seconds": check["interval_seconds"],
                "next_due": check["next_due"],
                "provider": {
                    "id": check["provider__id"],
                    "name": check["provider__name"],
                    "enabled": check["provider__enabled"],
                },
            }
        )
    for account in accounts:
        row = row_for(account["persona__id"], account["site__domain"])
        row["account"] = {
            "id": account["id"],
            "username": account["username"],
            "site": {"id": account["site__id"], "domain": account["site__domain"]},
        }
        row["username"] = account["username"]
        row["checks"] = sorted(checks_by_account[account["id"]], key=lambda c: c["id"])
        row["monitored"] = any(c["enabled"] and c["provider"]["enabled"] for c in row["checks"])
    all_rows = list(rows.values())
    for row in all_rows:
        row["hosts"] = [{"domain": row["domain"], "cookies": row["cookie_count"]}]
        if row["cookie_count"] is None:
            row["domain_cookie_count"] = None
            row["host_only_cookie_count"] = None
            row["inherited_cookie_count"] = None
            continue
        labels = row["domain"].casefold().split(".")
        scopes = domain_scopes.get(row["persona"].id, {})
        # Only whole parent labels match: example.com does not cover notexample.com.
        # Host-only counts are deliberately absent from this parent lookup.
        row["inherited_cookie_count"] = sum(
            scopes.get(".".join(labels[i:]), 0) for i in range(1, len(labels))
        )
    # Cookie hostnames are storage details of the configured site, not accounts.
    configured = [row for row in all_rows if row["account"]]
    for row in list(all_rows):
        if row["account"]:
            continue
        parents = [parent for parent in configured if parent["persona"].id == row["persona"].id
                   and row["domain"].endswith("." + parent["domain"])]
        if not parents:
            continue
        parent = max(parents, key=lambda item: len(item["domain"]))
        parent["hosts"].extend(row["hosts"])
        parent["origins"].extend(row["origins"])
        for key in ("cookie_count", "domain_cookie_count", "host_only_cookie_count"):
            if parent[key] is not None and row[key] is not None:
                parent[key] += row[key]
        all_rows.remove(row)
    counts = {
        "all": len(all_rows),
        "configured": sum(row["account"] is not None for row in all_rows),
        "saved": sum(
            bool(row["cookie_count"] or row["inherited_cookie_count"] or row["origins"])
            for row in all_rows
        ),
        "unmonitored": sum(not row["monitored"] for row in all_rows),
    }
    needle = search.casefold()
    filtered = [
        row
        for row in all_rows
        if (
            kind == "all"
            or (kind == "configured" and row["account"] is not None)
            or (
                kind == "saved"
                and (row["cookie_count"] or row["inherited_cookie_count"] or row["origins"])
            )
            or (kind == "unmonitored" and not row["monitored"])
        )
        and (
            not needle
            or needle
            in " ".join(
                [
                    row["domain"],
                    row["username"],
                    row["persona"].name,
                    *(check["name"] for check in row["checks"]),
                ]
            ).casefold()
        )
    ]
    filtered.sort(
        key=lambda row: (
            row["account"] is None,
            row["domain"].casefold(),
            row["persona"].name.casefold(),
        )
    )
    total = len(filtered)
    pages = max(1, ceil(total / page_size))
    page = min(page, pages)
    start = (page - 1) * page_size
    return {
        "rows": filtered[start : start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "has_previous": page > 1,
        "has_next": page < pages,
        "previous_page": page - 1 if page > 1 else None,
        "next_page": page + 1 if page < pages else None,
        "start": start + 1 if total else 0,
        "end": min(start + page_size, total),
        "personas": personas,
        "selected_persona": str(selected_id) if selected_id is not None else "",
        "sources": sources,
        "counts": counts,
        "search": search,
        "kind": kind,
    }
