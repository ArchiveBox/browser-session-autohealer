# Initial prototype plan

Design phase only. This document is a work sequence, not a claim that any of the
following capabilities are implemented or that named libraries passed acceptance.

## Decisions already supplied by the user

- Session Tender owns one canonical persona registry, DB, and filesystem.
- Always fork profiles before browser use; multiple forks of one persona may run
  concurrently. The most recently ended eligible successful check-in becomes leader. Any run
  issue prevents automatic promotion; coherent failed returns remain inspectable.
- Implement local Chrome and Browserbase first. Keep Kernel and Browserless as
  deferred placeholders, with no dependencies or provider work yet.
- Use narrow adapters for provider-specific lifecycle, state/config transfer,
  uploads/downloads, screenshots/viewing where needed. Everything else uses CDP.
- Reuse `abx-dl`/ArchiveBox Chrome utilities and study Stagehand teleport's state
  transfer implementation.
- Initial sites: Facebook, Instagram, X, TikTok, YouTube, Reddit, and LinkedIn.
- Use an existing agent harness and complete UI; do not rebuild provider/session
  onboarding, chat, terminal, or subagent displays.
- Develop from the interface and observable user outcomes. Support both
  persona → site/account and site → persona/account grouping over the same data.

## Phase 0: establish the interface contract

Start with the [interactive UI mockup and screen contract](ui-contract.md).
It covers account health, both grouping directions, persona configuration,
checks, human recovery, session revisions, checkout/check-in history, shared comparison, provider setup,
and agent/tool connections. Its sample state transitions are design material,
not evidence that the underlying capabilities work.

Implement subsequent phases as visible vertical slices. Each slice replaces an
example state with a real API-backed state, a real action, and a verifiable
receipt. In particular, show “not checked,” “unsupported,” “pending export,”
and “awaiting sync” until the corresponding work has actually completed.

Do not implement the whole data model before trying the user flows.
The first backend slice should make one persona's fork, end, export, and leader
promotion visible in the existing session and revision screens.

## Phase 1: prove persona forks and local ↔ Browserbase state transfer

This is the highest-risk dependency. Do it before building a sophisticated agent.

1. Define persona, immutable revision, runtime session, provider binding, and
   explicit completion ordering. Establish app-owned state paths and manifests.
2. Discover a disposable ArchiveBox persona through documented interfaces and
   enroll a coherent copy into the canonical store. Preserve original files.
3. Launch local forks with the existing Chrome helpers and exact runtime image.
   Export portable/tab state and a quiescent native revision on completion.
4. Add Browserbase launch/release and CDP attach. Push the same revision into an
   isolated session, export state back, and use that output for a subsequent local
   fork. Use its native context only where persistence semantics are qualified.
5. Reuse/extend teleport state logic selectively. Close the active-origin,
   IndexedDB/OPFS, cookie metadata, deletion, and before-site-script gaps.
6. Connect ArchiveBox's existing runtime-fork lifecycle to checkout/check-in;
   export before cleanup, and remove duplicate ownership/imports for this mode.

Required real evidence:

- Two real local Chrome processes fork the same revision into different writable
  directories. Mutating one does not mutate the other or the canonical base.
- Finish A then B, including B starting from an older base. When both are fully eligible successes,
  B becomes leader;
  C demonstrably inherits B's final files/cookies/storage, not A's.
- Reverse upload completion order while preserving session-end order. Duplicate
  completion callbacks do not create multiple revisions or wrong leaders.
- Stop/crash a real worker during export and restart the service. Partial data
  never becomes a committed leader. Staged recovery files remain accounted for.
- A failed or signed-out completion never automatically becomes leader, even
  with a coherent export. Unknown outcome, missing proof, any run issue, and
  export failure also retain the existing leader. Explicit operator revocation
  cannot be undone by an older returning fork.
- Local → Browserbase → local round-trip with the actual chosen credentials and
  content on a dedicated test account. Verify site identity/content after each
  hop; cookie count alone is insufficient.
- Verify HttpOnly, SameSite, Secure, host-only, expiry/session, partitioned cookie
  behavior, explicit clear versus omitted state, and unrelated-origin preservation
  using real browser state and existing fixtures. Unsupported cases fail visibly.
- Verify localStorage, tab/origin-specific sessionStorage, populated IndexedDB,
  and OPFS bytes, including a restart. Mark each surface passed/unsupported;
  a JSON placeholder is never a successful export.
- Compare browser-observed UA/client hints, locale/timezone, viewport/screen/DPR,
  geolocation/permissions, color/motion and configured provider identity settings.
- Finalizing a fork releases the correct owned browser/context. Other sessions
  stay alive. Network disconnect and explicit provider release are distinguished.
- A real ArchiveBox capture uses a fork from the canonical leader, produces
  actual authenticated artifacts, and checks in before runtime cleanup.

Reuse existing fixture web apps and real user-facing login flows. Do not create
fake CDP servers, fake provider SDKs, or mocked storage to claim this gate passed.
Infrastructure fault tests should interrupt the real process/network path.

## Phase 2: checks, scheduling, and the small status dashboard

Implement DB-backed due checks, per-site/account/provider budgets, events from
capture failures, and stale-health handling. Connect both persona → site/account
and site → persona/account views to the same records, with fork/revision/provider
detail and explicit push/pull/test actions.
Add OpenCode server integration and link one incident to its real UI session.

For each of the seven initial sites, create a check-only recipe from a real
user-approved account and URL. Start depth on one uncomplicated account,
then expand coverage; do not claim seven working integrations from generic rules.

| Site | First capability to specify with user |
| --- | --- |
| Facebook | Expected account plus readable approved group/post |
| Instagram | Expected account plus required feed/post/media |
| X | Expected handle plus required feed/search/post |
| TikTok | Expected account plus required feed/video access |
| YouTube | Expected account/channel plus required private or account-specific content |
| Reddit | Expected account plus approved community/post access |
| LinkedIn | Expected account plus required feed/post access |

These are check intents, not assertions about current site behavior. Record the
actual indicators and authentication factors during onboarding. YouTube/Google
and other multi-account selectors need explicit identity tests.

Acceptance: visible dashboard reflects real DB changes; last success differs
from last attempt; wrong account, logged-out, missing/deleted canary, transient
network failure and overlay states remain distinct. Same recipe runs on both
providers from the same revision and produces an explainable comparison. A check
that promotes a revision must not trigger an infinite self-check/promotion loop.
Agent sessions created through the API appear in the off-the-shelf UI and survive
restart; cancellation actually stops the operation.

## Phase 3: local classification and bounded recovery

Start with known selectors/text/visibility assertions. Benchmark GLiClass Edge on
short real observations against that baseline before making it a dependency.
Collect consented, minimized examples for all blocker classes and languages;
keep training examples separate from held-out sites/time periods.

Measure per-class precision/recall, false healthy results, unknown/abstention,
wrong-account outcomes, and combined blockers. Measure total model/runtime
download size, peak RSS, cold start and warm p50/p95 on the intended small CPU.
Proposed target: model artifacts below 1 GB and warm classification p95 under
2 seconds on that named CPU. This is a target to validate, not measured performance.
Browser memory and hosted minutes need separate concurrency/cost limits.

Add approved consent/promo recipes first, then one password/TOTP flow and one
email-code flow through local secret tools. An agent can author or repair scripts
within the existing harness, but periodic checks replay reviewed artifacts.
Captcha, passkey/push and unfamiliar identity/security challenges get a human
takeover path; provider-supported challenge handling is explicitly configurable,
bounded, and logged rather than silently assumed to work everywhere.

Acceptance: repair passes the original identity/content check, then a fresh fork
of the promoted revision passes again. A step budget is enforced. A changed page
cannot cause a new post/follow/purchase/security change. Website/mail prompt
injection cannot expand tool scope or retrieve arbitrary credentials. A newly
learned recipe remains proposed until reviewed and tested.

## Phase 4: secret/mail and human handoff qualification

Use an organization-approved 1Password test vault and dedicated research mailbox.
Exercise real SDK/CLI resolution, Gmail OAuth or IMAP, and a real site code flow.
Do not read or send personal mail as incidental test setup.

- Verify recent matching message selection, wrong recipient/site, stale and
  duplicate codes, overlapping challenges, MIME/HTML messages, and link redirects.
- Verify codes/passwords are absent from application logs, model requests,
  session history, traces, screenshots, error reports, and persisted recipes.
  Secret masking tests must inspect actual generated artifacts.
- Verify email access uses granted read-only scope and read-only IMAP operations;
  no send/delete/draft capability reaches the recovery agent.
- Verify local and Browserbase takeover on the correct fork, exclusive input,
  expiry of viewer/control links, stop/resume, and successful post-handoff recheck.
- Verify no late background actor overwrites the human's active target or bypasses
  canonical completion ordering.

Do not share the agent process's filesystem/network authority with the secret
executor and call that isolation. Browserbase necessarily receives browser-side
secret inputs; configure hosted recording/retention and inference disclosure
separately and validate the selected deployment settings.

## Phase 5: operational hardening before user rollout

Add user/persona authorization before multi-user deployment; an admin-only
prototype is not a tenant boundary. Validate backups/restores of DB plus revision
filesystem, revoked-source handling, bounded retention without deleting referenced
revisions, provider cleanup after coordinator crashes, and import/export loop
deduplication. Preserve evidence holds independently of operational-log expiry.

Track detection delay, user minutes per incident, recurrence after repair,
post-promotion health, fork conflicts, model cost and browser minutes. These are
the product outcomes; login-attempt count is not success.

## Still-open implementation choices

- Exact session-end ordering when Browserbase terminates unexpectedly or reports
  an end late; UI wording while the newest completion's export is unavailable.
- Which storage surfaces can be faithfully exported from Browserbase, especially
  IndexedDB/OPFS/non-exportable keys, and whether required accounts depend on them.
- Whether a future targeted-origin merge is worth its complexity. For this
  prototype, preserve the requested whole-fork last-ended-leader policy.
- First test accounts/URLs and allowed auth methods; consent and reauthentication
  policies vary by organization and site.
- Exact pinned OpenCode version and whether to qualify an independent OpenChamber
  pair before changing ArchiveBox's shared runtime.
- Whether the CPU classifier's complete runtime fits the intended machine; the
  model's download size alone does not settle this.

The next implementation should start with Phase 1's real state round-trip and
completion-order tests, then build the dashboard and recovery on that contract.
