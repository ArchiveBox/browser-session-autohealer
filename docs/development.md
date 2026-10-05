# Development & operations

[← Browser Session Autohealer](../README.md)

Run these commands from the project root.

Versioned browser personas and plain-English account access checks for ArchiveBox
and other browser automation systems. This is a working local prototype built
with **Plain** (the standalone Django-derived framework), PostgreSQL, HTMX, and
Tailwind. It uses OpenCode with **OpenAI `gpt-6.1-sol`** and browser-use's
**browser-harness** to inspect real browsers.

## Run locally

Requires `uv`, Docker, Node, and `opencode` on PATH. Runtime data and credentials
live outside this checkout, in `~/.local/share/account-checker` and
`~/.config/account-checker`. Copy `.env.example` to `.env` and set your API keys and local preferences there.
The app, CLI, and bootstrap load `.env`; existing process environment variables
take precedence. `.env` is ignored by Git and should have permissions `0600`.
Personas, account bindings, browser state, and generated credentials stay in the
private database and config/data directories; never put them in source files.

```bash
cp -n .env.example .env
chmod 600 .env
# Set OPENAI_API_KEY in .env.
uv sync
npm ci
uv run python bin/bootstrap.py
uv run plain server --bind 127.0.0.1:8421 --workers 1
# In another terminal:
uv run plain accounts worker
```

Open <http://127.0.0.1:8421>. The local admin email is
`local@account-checker.test`; its generated password is in the private
`admin-password` file. This prototype binds to loopback and is not configured
for remote deployment.

The separate `account-checker-browser:dev` image extends the existing abx-dl
image, adding pinned browser-harness. It reuses that image's Chrome binary and
`chrome_utils.js`. **No upstream image or sibling repository is modified.**
Its Chrome launcher preserves native logs and applies a narrow CPU compatibility
guard for Linux ARM64 hosts with SME but no SVE; see
[browser stability](browser-stability.md) for the reproduced crash and tests.

Configure the local provider with:

```json
{"runtime": "docker", "image": "account-checker-browser:dev"}
```

Create a persona, add a site account, then write an access check in plain English.
`Check now` starts an isolated browser copy for the selected check, persona,
site and provider. Monitoring never implicitly runs across sites. The CLI uses
`uv run plain accounts run --check <id>`. Opening a live browser also requires
selecting a particular check and provider. Imports accept an explicit list of domains (and
their subdomains), or an explicit all-sites choice. Browser settings travel with
the selected site data.

## Current behavior

- The app owns the persona registry, provider configuration, database, encrypted
  portable state, immutable checkpoints, and isolated working profiles.
- Every checkout freezes its check prompts, browser settings, inference model,
  provider configuration, and base checkpoint. Concurrent checkouts use separate
  browsers and separate browser-harness connections.
- OpenCode receives the prompt and browser-harness MCP tools. It inspects the live
  page, generates Python browser-harness programs, evaluates the findings, and
  returns a structured verdict. There are **no hardcoded site check scripts,
  selectors, Playwright checks, or simulated browser results** in the application.
- [Append-only check history](check-history.md) records versioned source, exact
  inputs/output, schedule changes, and execution lifecycle with UUID7 public IDs.
  The History view reconstructs recorded state at a selected timestamp.
- Browser Sessions, Personas, Sites, Checks, AI Sessions, Check Types, and Browser
  Providers share a full-width, collapsed tree table. Expanding a row shows its
  fields, edit links, and related records; creation controls are available in each
  view. Site views start with sites, and persona views start with personas.
- Check Types have Plain create/edit forms, CSV domain patterns with `*`
  wildcards, and append-only revisions. Related execution lists include only
  invocations of that exact revision. Foreign-key values link to their editors.
- Programs, redacted tool output, screenshots, and OpenCode sessions are retained
  with each run. The dashboard leads with actual page screenshots, account access status,
  last-working times, and next steps. Browser personas and Sites are separate
  grouping views. Session history tracks browser lifetimes, the initiating app,
  provider, and adoption or discard; check histories remain under each check.
- Persona pages expose grouped, readable browser settings; familiar form controls
  preserve unspecified settings. Adapter declarations supply support and defaults,
  keeping provider behavior out of collection management.
- App-owned checks stream real CDP frames and capture every browser-harness step.
  Active and past conversations open in embedded OpenCode under their browser
  session and check, alongside a scrubbable screenshot timeline. The AI Sessions
  view provides another entry point using the existing app login. HTTP, SSE and WebSocket forwarding reuse
  ArchiveBox’s transport; see [OpenCode embedding](opencode-embedding.md).
- Compare any two saved versions with persona/day-grouped selectors and aligned
  before/after evidence and redacted state differences. Value provenance follows
  parent revisions, distinguishing inherited values from expiry-only updates. Technical lineage and
  diagnostics remain expandable. users never manually check out a browser.
- Any run issue blocks automatic promotion, including a recovered agent/tool
  error. Only a complete successful export with every required check passing can
  lead. The latest successful **browser finish time** wins, not upload order.
- Each persona has one **Persona leader**, shared by every provider and site.
  The latest eligible successful check-in replaces that pointer; earlier leaders
  remain in session history as superseded. Check scope only selects work to run.
- Integrations provides shared connector settings and a dense permission matrix
  with inherited or explicit overrides per persona, site account, and check.
  Starting a session from either session view requires one exact check assignment.
- Whole-profile forks preserve native databases. Cookie, localStorage, and
  sessionStorage state are also exported; sessionStorage is restored before
  page scripts run. Historical browser tab sets are retained separately, so a
  working fork does not reopen hundreds of personal tabs.
- Local Docker and Browserbase have been exercised with real accounts. Browserbase
  supports residential proxies and explicit Verified mode; unsupported plan
  features fail visibly. Generic CDP, Kernel, Anchor Browser, Browserless and
  ZenRows use the same check and state-transfer flow; see [provider results](providers.md).

This prototype uses one OpenCode agent session per check. The application owns
lifecycle and success-gating; browser-harness owns browser execution. A separate
planner/browser-agent hierarchy is deliberately deferred until live checks show
that it improves reliability. Browser-harness itself is an execution harness;
it does not contain a local inference loop.

## Verification

The current disposable-account tests are documented in
[Login recovery](login-recovery.md). They exercise real SMTP/IMAP, Stagehand
variables, browser-harness, OpenCode, profile persistence and a fresh access check.
Those disposable fixtures were removed from this installation without backups.
Current acceptance evidence includes real HN, LinkedIn, and X checks; see [screenshot sources](images/README.md) and [browser stability](browser-stability.md).

The live acceptance suite uses real imported HN authentication, an actual empty
profile for login-required detection, concurrent browser forks, real OpenCode
sessions, and persisted PostgreSQL/filesystem evidence. See
[the live results](live-validation.md) for run IDs and limitations.

```bash
uv run plain preflight
uv run plain postgres sync --check
uv run ruff check app bin tests
# Inspect completed real runs, not fixtures or mocks:
uv run pytest tests/test_opencode_embed.py tests/test_site_selection.py -q
```

The run IDs are installation-specific. Acceptance tests require an authenticated
local app and existing real runs; they deliberately fail when that evidence is
missing. New runs can be queued through the UI, the CLI, or `POST /api/checkouts`.
API calls require the private `api-token` as a bearer token.

## Current limits

Checks and fixes share the Tasks editor, revision history, evidence, and agent
conversations. Rules instantiate common fixes when a known condition is observed.
A fix and its read-only verification execute in one browser session; failed
sessions never become the leader. Each rule edge can fire once per causal chain.
The general scheduler can be paused while `plain accounts worker --queued-only`
handles UI requests and rule-triggered work.

The persona lineage is horizontal: canonical state in the center, browser sessions
above/below, task-result dots linked to screenshots, and discard endpoints for
unadopted sessions. Leader changes are recorded separately from checkpoint parents.
Providers have editable colors; result dots retain status colors.
Interactive browsers run their selected tasks after interaction ends, before
export and check-in. They follow the same success and recency rules as other sessions.

Token sums come from OpenCode's actual message usage, cached with Django's file
cache. No usage tables, background synchronization, estimates, or historical-data
migration are involved. Totals cover OpenCode calls, not separate Stagehand calls.

The LinkedIn 1Password binding is configured and was verified with the real vault.
Messages access and a signed-in Google Voice test browser remain setup gates.
Learned routines that execute without inference, user handoff, and continuous
extension synchronization remain unfinished. Current browser checks still invoke
OpenCode and browser-harness; saved snippets are execution history, not a validated
routine cache. No social posting is supported.

Both agents receive the installed browser-harness skill and an iterative learning
prompt. Reusable functions live in the harness's `agent_helpers.py`; each invocation
snapshots that source into existing Task Type revisions alongside its call and
output. The next task loads its latest recorded helper as a candidate to inspect
and refine; the scoped helper editor also works when an import is broken.
Agents must replay unchanged helpers before claiming repeatability and
retain incomplete candidates when blocked. This does not yet enable automatic
inference-free execution of saved routines. Restart OpenCode after changing prompts.

Checkpoints have commit-like hashes, but are an immutable object store, not Git
or a Chrome SQLite merge engine. Portable state is encrypted; native profile
files rely on private directories and host volume encryption. Native LevelDB
imports taken from a running browser are marked unverified. Browserbase cannot
currently round-trip native IndexedDB/OPFS, and is rejected when those are required.
Browser emulation does not establish identical hardware/font/network fingerprints
across machines. A successful HN check does not validate other sites.

[Full Browserbase support](browserbase-support.md) maps adapter lifecycle,
state transfer, independent contexts, fingerprint constraints, visual evidence,
and acceptance tests. Only explicitly selected sites may be transferred. This
installation has exercised authorized HN, LinkedIn, and X state on Browserbase.
Successful checks establish access at that moment, not indefinite session durability.

Earlier [architecture](architecture.md), [research](research.md),
[UI contract](ui-contract.md), and [prototype plan](prototype-plan.md)
documents record the broader design exploration. This README and live results
state what the current implementation actually does. The old HTML design mockup
contains example data and is not acceptance evidence.

## Personal configuration and source control

- `.env.example` contains variable names and generic defaults only.
- `.env` holds local API keys, preferences, and acceptance-test expectations.
- 1Password passwords remain in the vault; account bindings are edited in Integrations.
- The database, browser profiles, and run artifacts live outside the checkout.
- Live tests read account identity and vault/item references from the environment.
- README account examples and screenshots are intentionally included with the owner's permission; do not treat that permission as approval to commit credentials.
