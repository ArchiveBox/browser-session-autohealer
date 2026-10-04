# Interface-first prototype

Current checkout/check-in policy and history design: [checkpoint history](checkpoint-history.md).
This supersedes the earlier unconditional last-ended leader and generic sync UI.

The [interactive mockup](../design/account-checker-ui.html) is an HTML fragment
for the conversation's preview runtime. It contains sample state, local UI
interactions, and light/dark responsive styling. It does not connect to any
accounts, browsers, vaults, inboxes, or providers. The data and observations are
illustrative, including the browser view, check preview, evidence manifest,
capability verification, and connection results.

Use these screens to decide what users need to understand and do before
committing to an application stack or complete database schema. Implement each
flow end to end, replacing example state with real observations and receipts.
The mockup's JavaScript is not a proposed scheduler, state store, or agent.

## Views and their required backing records

| View | User's question | Required records |
| --- | --- | --- |
| Overview, grouped by persona | Can this user begin today's work? | Persona, site-account, latest check proof, open incident, runtime and revision |
| Overview, grouped by site | Did this site lose access across our users? | The same accounts and proofs, grouped by canonical site identity |
| Persona accounts | Which identities and content belong to this profile? | Persona-account bindings, expected identity, check definitions |
| Browser configuration | What did I ask for, and what did the browser actually use? | Versioned desired config, observed fingerprint, required-field comparison |
| Revisions | Why is this revision the leader? What will the next browser inherit? | Fork base, session end order, durable export receipt, leader and last verified pointers |
| Checks | What is being tested, when, and why is a retry paused? | Immutable check versions, schedule, due time, run history, backoff and pause reason |
| Account detail | Does this account actually see the content we need? | Separate identity/content/blocker/freshness observations and evidence references |
| Recovery | What happened, what has been tried, and what do I need to do? | Incident, recovery attempts, waiting reason, scoped browser session, human handoff |
| Sessions | What is using this persona right now? | Isolated forks, runtime/provider identity, owner, lifecycle and export state |
| Destinations | Has the right state arrived where it is needed? | Provider bindings, persona scopes, direction, transfer receipts and coverage |
| Transfer review | What will change, including what will be deleted? | Candidate revision, change manifest, origin scope, deletion markers and provenance |
| Agent and tools | Which harness and recovery capabilities are available? | Agent endpoint, scoped tool connections, secret references, authorization status |

## Grouping in either direction

The overview has one **Group by: Persona / Site** switch, not separate dashboards
with separate status calculations. Both modes expose account identity, content
access, blocker, last check time, runtime, checked revision, and recent history.

Site headers aggregate all enrolled accounts for that site. “Facebook: 1/2
accessible” means one of two known accounts has a successful current proof in
the relevant scope. Opening Facebook shows the personas and their usernames.
“All accounts need help” should be distinct from a partial incident. Unknown,
stale, unchecked, and cooling-down accounts must remain distinguishable from
accounts observed to be signed out.

Filtering to “Needs attention” hides unrelated rows but does not redefine the
site's denominator. A partial failure must not become an apparent complete
outage. Search likewise preserves whole-site totals. Production should show a
small filtered-row count whenever the visible rows and aggregate differ.

Use a canonical site record with explicit origin membership; do not permanently
group by a display string or naïvely strip hostnames. For example, the identity
and feed origins can differ, and a persona may have multiple accounts on one
service. Count accounts and distinct personas separately. Correlated failures
suggest a site incident but do not establish one without evidence.

## Important state distinctions

- **Access:** authenticated identity, target content visibility, obstruction,
  freshness, and the specific checked runtime/revision. A cookie count is not
  access proof. Older successful results can be stale after leader changes.
- **Profile leadership:** the latest eligible successful check-in leads. Any run
  issue, unknown outcome, missing checks, or incomplete export prevents automatic
  promotion. Persona-wide and site-specific qualifications are separate.
- **Sync:** current/pending/review/failed against an explicit source revision,
  destination scope, and capability contract. Transfer success is not check
  success. A transfer can invalidate previous destination health.
- **Configuration:** desired versus observed. Saving settings changes future
  forks; it does not relabel a running session's fingerprint.
- **Recovery:** waiting for automation, a code, a person, backoff, or an unknown
  condition. A human finishing a challenge still requires a content recheck.
- **Coverage:** verified, unverified, unsupported, or excluded per state surface.
  Do not call portable cookies a complete browser profile.

The prototype highlights these distinctions but does not implement complete
stale-proof computation or revision history. Its fixed timeline and historical
observations are example rows, not a production event reducer.

The persona Lineage view now shows a vertical branch graph, checkout provider
activity, checkpoint hashes, check events, successful check-ins, failed returns,
and new forks from the selected leader. All comparisons open one shared Compare
view with base/head selectors and semantic state changes. The Providers and
Checkouts screens replace the generic synchronization dashboard.

## Creation and editing flows

**Persona:** name and location → source/import choice → runtime and browser
preferences → review → canonical persona with no assumed access. Start from an
ArchiveBox profile, paired extension, or an empty profile. Real implementation
must preview discovered profiles and validate import coverage before enrollment.

**Site account:** choose persona → domain → expected username/email → known
content URL → opaque credential reference → recovery/consent preferences →
review → not checked. Adding an account does not imply a successful sign-in.

**Check:** describe success in ordinary language → inspect/edit assertions and
generated script → run against a real fork → inspect identity/content/blocker
evidence → save a versioned definition and schedule. The mockup's preview is
explicitly an example and does not infer a script with a model or run a browser.
Production must support multiple checks per account; the mockup keeps one
editable example definition per account for navigation simplicity.

**Provider:** runtime → connection and credential reference → permitted personas
→ checkout/check-in policy → allowed origins → required state coverage.
Returned state and the run outcome appear together, followed by a separate
promotion decision. Failed check-ins remain inspectable.
Local/ArchiveBox and Browserbase are the initial runtime paths; an extension is
a sync source. Kernel and Browserless remain visibly disabled future choices.
Provider credentials must never become profile data or enter the agent prompt.
Real connections need provider-specific forms and actual authorization probes.
See the current checkpoint history document for required fields and acceptance.

**Recovery connection:** connection type → narrowly scoped credential reference
and recipient → authorization → ready. The prototype only saves the proposed
configuration; it cannot establish an authorized vault or mail connection.

Edits remain in the current mockup instance until Reset or reload. Host widget
state only remembers navigation and grouping; it stores no credentials or
profile data. Persistent draft/configuration saving belongs to the future API.

## Observable completion, not optimistic green statuses

| Action | Immediate UI state | Evidence required to finish |
| --- | --- | --- |
| Save browser config | Saved, pending verification | New fork's observed required fields match |
| Start a check | Queued, then running | Run receipt with assertions and evidence |
| Complete a human challenge | Rechecking | Original identity/content assertions pass |
| End a browser session | Ending, exporting | Durable export and assigned end order |
| Promote a leader | New revision visible | Future fork demonstrably inherits it |
| Push/pull state | Preparing, transferring, verifying | Destination receipt for revision, scope, and coverage |
| Connect a provider | Configuration saved, authorizing | Actual scoped capability probe succeeds |
| Update a site recipe | Candidate version | Real reviewed run; no silent overwrite of prior runs |

Keep these intermediate states visible through reconnects and restarts. Failures
need a concrete reason, an actionable next step, and the original receipt or
attempt identity. “Retry” creates or resumes a tracked attempt; it cannot merely
clear an error badge.

## Integration and development order

Keep the persona/check/sync interface in this app. Link incidents to an existing
OpenCode workspace at its own origin for provider configuration, agent chat,
terminal, and session inspection. The handoff preview defines context and tool
scope; it deliberately does not reproduce that third-party UI.

1. Review the screen model and grouping with users.
2. Make one persona's local fork/end/export/leader flow real behind Sessions
   and Revisions. Show failures and pending export as well as success.
3. Connect one real content check to the overview and evidence screen.
4. Make a Browserbase destination's scoped round trip and receipts real.
5. Add human recovery in the same browser and prove post-recovery access.
6. Add scheduled runs, then bounded automatic recovery and scoped tools.

Browser verification of this design exercised both groupings, search and status
filters, configuration retention, persona/site/check creation and validation,
human recovery transitions, destination setup, deletion review, and connection
configuration. Light/dark and narrow layouts were inspected. This verifies the
prototype interaction only, not any browser/provider capability.
