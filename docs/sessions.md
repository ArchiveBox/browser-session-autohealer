# Direct browser sessions

Autohealer selects a persona, forks its leader, starts a browser, restores its state,
and returns the **provider's CDP URL**. Your application connects directly. Autohealer
keeps a separate settings/lifecycle connection; it does not relay your CDP traffic.

Run these alongside the HTTP server:

```bash
uv run plain accounts worker  # independent scheduled checks and recovery
uv run plain accounts broker  # session creation, deadlines, release and check-in
```

In the app, **Browser Sessions → New session** opens the same request builder at
`/edit/session`. Choose a provider (or any), persona (or any), site and maximum check
age. Require all checks, at least N, or choose which checks are required, preferred
or ignored. Add another site when needed; IP country applies to the whole session.
Leave the age blank to follow each check's schedule. The form previews matching
browsers and shows blocked requirements with their last check screenshots and times,
without allocating a session. **Advanced conditions** edits the condition JSON for
All/Any/Except groups and ordered preferences. Set rechecking, wait time,
lifetime and provider overrides, then create the session. The result page shows readiness
screenshots, copyable CDP/context IDs, expiry and check-in controls. **API request**
shows the equivalent JSON. Failed requests can be reopened with **Edit request** or
**Recheck before use**, preserving the requested provider, persona, sites and IP.

Use the existing API bearer token from the private configuration directory.
`GET /api/personas` and `GET /api/providers` include public UUIDs. Numeric IDs also
work in conditions. `POST /api/sessions` accepts:

```json
{
  "require_all": [
    {"type": "task", "site": "x.com", "tasks": "*", "status": "healthy", "max_age": 1200}
  ],
  "prefer": [
    {"type": "ip", "country": "US"},
    {"type": "ip", "country": "CA"},
    {"type": "ip", "country": "MX"}
  ],
  "recheck": false,
  "timeout": 0,
  "lifetime": 1800
}
```

| Field | Meaning |
|---|---|
| `require_all` | Every clause must match. Nested `require_all`, `require_any`, and `not` are supported. Task health cannot be negated. |
| `prefer` | Ordered preferences, compared lexicographically; requirements always win. |
| `recheck` | Run the selected checks on the **actual browser being handed out**. Every required task clause must pass. |
| `timeout: 0` | Reject unmet saved conditions immediately. Eligible requests still need asynchronous browser provisioning. Fresh checks require a nonzero timeout. |
| `timeout: 50` | Allow up to 50 seconds for readiness, including provisioning and checks. |
| `timeout: -1` | Keep a durable pending request until it can be prepared or is cancelled; return `202` for polling. |
| `lifetime` | Maximum seconds of client use; capped by the provider's browser lifetime. Default 1800. |
| `provider_options` | Adapter-validated overrides, layered over provider defaults and persona/provider configuration. A null value removes an inherited key. Credentials stay in environment variables. |
| `allow_unhealthy` | Explicit diagnostic escape hatch. Default false; incompatible with `recheck`. |

With no task clauses, all enabled read-only tasks for the selected persona/provider
must be healthy. An empty task set is **not** healthy. With task clauses, only those
sites/tasks are selected. `tasks` accepts `"*"` or a list of task UUIDs, numeric IDs,
or exact names. `min_passed` requires at least that many distinct checks to pass;
omitting it requires every selected check. Missing, failed, stale or changed results
never count as passes. Without `max_age`, each task's configured interval is its freshness
limit. A newer failure supersedes a previous pass. Changed prompts, browser settings,
provider settings, or saved site state invalidate old evidence.

The normal request reads saved health; it does not start checks or fixes. A waiting
request is reconsidered as the independent maintenance worker updates that health.
Unmet readiness requirements return an error and dispatch the existing asynchronous
recovery rules. A preferred check or an explicitly tolerated failure does not block
readiness. Promotion at check-in remains stricter: any failed check prevents adoption.

## Browserbase, any persona, X checked within five minutes

```json
{
  "require_all": [
    {"type": "provider", "kind": "browserbase"},
    {"type": "task", "site": "x.com", "tasks": "*", "max_age": 300}
  ],
  "timeout": 60
}
```

## At least three of four checks within twenty minutes

Use the names or UUIDs of the configured checks. This accepts any three; to require
three specific checks instead, put those in `require_all` and the fourth in `prefer`.

```json
{
  "require_all": [
    {"type": "ip", "country": "US"},
    {"type": "task", "site": "x.com", "tasks": ["CHECK_1_UUID", "CHECK_2_UUID", "CHECK_3_UUID", "CHECK_4_UUID"], "min_passed": 3, "max_age": 1200}
  ],
  "timeout": 60
}
```

With `recheck: true`, the selected checks run again on the new session and must meet
the same minimum and required-check rules. Preferred checks participate in selection
and rechecking, but their failures never become implicit requirements.

## Fresh Reddit check

```json
{
  "require_all": [{"type": "task", "site": "reddit.com", "tasks": "*"}],
  "recheck": true,
  "timeout": 50
}
```

## Exact persona and provider, checked again

```json
{
  "require_all": [
    {"type": "persona", "id": "PERSONA_UUID"},
    {"type": "provider", "id": "PROVIDER_UUID"},
    {"type": "task", "site": "news.ycombinator.com", "tasks": "*"}
  ],
  "recheck": true,
  "timeout": 50
}
```

## HTTP lifecycle

- `POST /api/sessions` → `200` ready, `202` pending, or `409 application/problem+json`
  with failed conditions. Malformed requests return `400`.
- `Idempotency-Key` makes creation safe to repeat. Reusing a key with a different body is an error.
- `Location` identifies `GET /api/sessions/<uuid>`. Poll it after `202`.
- `Prefer: respond-async` returns immediately. Otherwise positive timeouts wait up to
  60 seconds per HTTP request; the persisted readiness deadline still applies.
- A ready response includes `cdp_url`, `browser_context_id`, prepared `targets`,
  persona/provider UUIDs, checkpoint, expiry, task results and IP observations.
- `POST /api/sessions/<uuid>` with `{"success": true}` releases the browser. Autohealer
  checks it again, exports its state, stops it, and applies the existing leader rules.
- `{"success": false, "message": "..."}` records an unsuccessful use. `{}` reports
  no outcome. Neither automatically becomes leader. Repeated release is idempotent.
- Browser termination, owned-context closure, or lifetime expiry also triggers check-in.
  A closed browser may no longer be exportable; its branch is discarded. Explicit
  release **before closing the browser** preserves the opportunity to export and verify.
- Pending requests can be cancelled with the same release call. Cancellation or a
  readiness deadline prevents handoff even if a task is still finishing its current operation.

Connect with Puppeteer using `defaultViewport: null`, or Playwright with
`connect_over_cdp`. Use a returned prepared target, and the returned browser context
when present. The sidecar configures new tabs best-effort, but a client's immediate
navigation can race those overrides. Prepared targets are configured before readiness.
Disconnect your client after release; do not close a borrowed CDP browser's other contexts.
CDP URLs are bearer capabilities: do not put them in logs or public frontend code.

| Adapter | Direct handoff |
|---|---|
| Local host Chrome | Yes; endpoint reachable from the client host |
| Generic CDP | Yes; isolated context, shared browser stays alive |
| Browserbase / Kernel / Anchor / Browserless | Direct provider endpoint |
| Local Docker | Rejected: current image only exposes CDP inside its container |
| ZenRows | Rejected: current adapter requires a relay |

The existing state-exchange `/api/checkouts` endpoints remain available for apps
that own browser launch themselves. They can report an observed IP with
`POST /api/runs/<id>/ip` and `{"ip":"IP_ADDRESS"}` before check-in.

## IP audit and network settings

**IPs** lists immutable observations with persona, provider, start/end timestamps,
geolocation and the session's task screenshots. Expand a row to inspect the browser
session. Provider and persona rows link to filtered IP history and network settings.

`IPUsage` belongs to a session; persona/provider and task results are derived through
that session. No unique IP constraint or IP-to-provider/geolocation dictionary is used.
Repeated observations of the same IP within one session form one row at
check-in. Later sessions get new rows and fresh geolocation snapshots.

Set `MAXMIND_CITY_DB` to a locally installed GeoLite2 City or GeoIP2 City MMDB. Lookup
stays local and snapshots the database version with each observation. Missing or invalid
databases show unknown location; geography requirements fail closed.

The browser probes `SESSION_IP_PROBE_URL` (default `https://api64.ipify.org?format=json`)
at launch and before check-in. IP conditions always match this browser session's
current address and location. They accept `ip`, `country`, `state` and `city`;
there are no site, historical-session or freshness selectors. IP history is an
audit log, not a substitute for measuring the newly created session.
Client-reported observations are labelled `reported`.

Network settings are one JSON configuration per persona/provider pair, interpreted
only by its adapter. Blank form fields inherit provider defaults. Settings affect new
sessions. Region choices do not promise the same IP across sessions.

| Adapter | Persona/provider controls |
|---|---|
| Browserbase | [Country, US state, city](https://github.com/browserbase/sdk-node/blob/main/src/resources/sessions/sessions.ts) |
| Browserless | [Country, state, city](https://docs.browserless.io/baas/bot-detection/proxies); finer regions require the provider's plan support; sticky within a session |
| Anchor | [Country, region, city](https://docs.anchorbrowser.io/api-reference/sessions/start-browser-session); city requires region |
| Kernel | Existing saved proxy name; choose a static or geographically configured proxy in Kernel |
| ZenRows | Country or broad proxy region for scheduled tasks |
| Local / Generic CDP | Network managed by host/upstream browser |

## Live acceptance

With a real HN task configured, server and broker running:

```bash
uv run pytest tests/test_session_conditions.py tests/test_session_api.py tests/test_session_builder.py -q
SESSION_TEST_PROVIDER=cdp uv run pytest tests/test_session_handoff.py -q -s
SESSION_TEST_PROVIDER=browserbase uv run pytest tests/test_session_handoff.py -q -s
```

The handoff test runs the configured browser-harness task, connects a separate real
client to the returned endpoint, checks settings, reports completion, verifies fresh
postflight results, checkpoint promotion and exactly-once IP records. It uses live
accounts and provider resources; it is not a fixture or screenshot simulation.
