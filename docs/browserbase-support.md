# Full Browserbase support

Status: implementation plan, verified against source and current documentation on
2026-10-03. **No Browserbase session has been launched for this work.** The active
collection was reset to a fresh test persona. Personal Brave cookies and prior
evidence are outside the active collection in a private local recovery backup.
Do not use that backup for remote tests.

## What exists

`app/core/providers.py::Browserbase` can create a fresh context and session, expose
its CDP endpoint, and request release. Shared CDP code seeds/exports cookies and
visited-origin local/session storage; browser-harness performs the English checks.
The adapter truthfully reports no native profile, IndexedDB, or OPFS transfer.

Missing: effective browser settings, proxy/fingerprint options, remote termination
confirmation, persistence readiness, context cleanup, remote live-view integration,
complete storage portability, and a real Browserbase acceptance run. Creating a
remote context today does not seed it with the canonical native profile.

## Keep ownership in the right place

The central collection owns personas, immutable revisions, checks, and promotion
rules. It must never contain `if provider == browserbase` branches. Adapters own
launch options, provider credentials, session/context identifiers, capabilities,
transfer receipts, and cleanup. Standard CDP handles browsing and observations.

Add an adapter-owned runtime record for each checkout: canonical base digest,
provider resource IDs, requested/effective settings, transfer coverage, connection
lease, provider end time, export status, and cleanup status. The shared UI renders
these records without interpreting provider-specific settings itself.

The lifecycle remains:

1. Pin a canonical revision and create an independent remote branch.
2. Restore supported data and report any missing requirements before checking.
3. Run the same English checks and recovery tools over CDP.
4. Export a candidate, confirm the browser ended, and verify persistence.
5. Admit only a completely successful candidate. A later failed branch cannot
   displace the leader. Retain the failed evidence and sanitized error.

## Workstreams and acceptance gates

| Work | Implementation | Evidence required |
|---|---|---|
| Connection setup | Adapter-defined form for project, region, timeout, Verified/standard mode, OS, proxy reference, recording/logging preference and test-persona selection. Store credentials outside the collection DB. | Test connection creates/releases only a clean test session; invalid credentials and quota errors are understandable in the UI. |
| Reliable lifecycle | Record resource creation immediately; compensate if later creation fails. Distinguish disconnected, expired, stopped and export-pending. Confirm remote terminal state after `REQUEST_RELEASE`. Reconcile orphans after a worker restart. | Kill/restart the worker during launch, work, and check-in; no untracked paid sessions or falsely successful runs. |
| State transfer | Seed cookies with partition/host-only/session attributes; restore per-origin state before application scripts. Add IndexedDB, OPFS and service-worker/cache coverage incrementally. Keep completeness explicit. | Export/import/export comparisons and authenticated checks across local → Browserbase → local, using disposable accounts only. |
| Independent branches | Use a fresh context per mutable checkout. Never let concurrent sessions write into the canonical context. Keep provider contexts as caches/candidates, not the sole authoritative copy. | Two successes plus one failure fork from the same digest; only the latest fully successful completed candidate becomes the leader. |
| Fingerprint and network | Adapter maps requested settings, probes effective values, and reports unsupported combinations. Pin network identity where the provider/proxy contract allows it. | Compare UA/client hints, screen/viewport/DPR, timezone, language, color/motion preferences, permissions, storage, and observed egress for each runtime. |
| Visual operation and recovery | Reuse local CDP evidence capture, thumbnails, annotations and OpenCode sessions. Add Browserbase live URLs for app-owned runs only; use temporary authenticated views for manual help. | User can watch a real check, understand failure, give help, and see a subsequent passing screenshot without reading implementation logs. |
| Retention and cleanup | Delete abandoned contexts and expire live URLs according to adapter policy. Disable provider recordings/logs for secret-entry sessions by default; retain locally masked evidence. | No secrets in model text requests or normal app logs in the test corpus; visual redaction tested and explicitly best effort. |

## Important compatibility decisions

**Standard browser versus Verified.** The current API deprecates `advancedStealth`
in favor of `verified`. Verified chooses a complete fingerprint and does not allow
custom viewport sizes. The UI should offer a provider-supported environment with
an explicit compatibility report, rather than silently applying conflicting CDP
overrides. Standard mode can honor a custom viewport. Neither mode proves exact
Brave fingerprint equivalence. [Verified configuration](https://docs.browserbase.com/platform/identity/verified-customization),
[session API](https://docs.browserbase.com/reference/api/create-a-session).

**Persistence versus portability.** Browserbase documents context persistence for
cookies (including session cookies), local/session storage, IndexedDB, service
workers and preferences, with synchronization after session closure. That does
not establish a supported native-profile download/clone API for our canonical
store. The current Get Context schema returns metadata; Create Context exposes
encryption metadata but the current index does not document a complete arbitrary
profile upload/download workflow. Verify this with a real test/API contract before
relying on it. Until then, use portable CDP transfer and declare its limits.
[Contexts](https://docs.browserbase.com/platform/browser/core-features/contexts),
[Get Context](https://docs.browserbase.com/reference/api/get-a-context),
[Create Context](https://docs.browserbase.com/reference/api/create-a-context).

**Reuse teleport selectively.** `stagehand-v4/stagehand-driver/recording/teleport.ts`
captures and replays settings plus browser state. Its `recording/browser_state.ts`
already handles cookies, environment, viewport, local/session storage and optional
UI state. However, `indexed_db` and `opfs` currently return “coming soon...” markers;
they are not implementations to reuse. Vendor tested pieces inside Session Tender
with attribution, without modifying that repo. Expand the canonical state schema
only as transfer implementations and real round-trip tests exist.

**IP continuity.** Built-in proxy selection is not evidence that the user's home
IP or a fixed address survives every session. Model proxy identity as an adapter
constraint and measure actual egress. A custom proxy may be necessary when a test
requires the same exit path. [Proxy documentation](https://docs.browserbase.com/platform/identity/proxies).

**Secrets still reach the browser.** The local broker retrieves only scoped
credentials/messages and feeds Stagehand variables over encrypted CDP to the
browser that needs them. Models see placeholders; reflected-page redaction is
best effort. Browserbase necessarily hosts the resulting browser state. Logging
and recording default on unless explicitly disabled; live views can work without
recording. Provider contexts/downloads are separate retention categories.
[Retention controls](https://docs.browserbase.com/account/enterprise/zero-data-retention),
[live view](https://docs.browserbase.com/platform/browser/observability/session-live-view).

## Order of implementation

First deliver connection settings, clean session lifecycle, and a real screenshot
check on the empty test persona. Then prove cookie/local/session-storage round
trips and parallel branch selection. Add test-account recovery with the local
broker, live help and retained evidence. Finally implement the missing storage
types and fingerprint compatibility probes before claiming full profile support.

Use a publicly reachable disposable test application for remote authentication
tests. The current loopback-only mail and app fixtures cannot be reached directly
from Browserbase; exposing the user's real app/profile is unnecessary.
Any later tunnel should expose only a dedicated test service, not the management
dashboard, CDP ports, mailbox, or private backup.
