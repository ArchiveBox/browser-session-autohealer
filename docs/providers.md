# Provider boundary

Design only. Implement local Chrome and Browserbase first. Kernel and Browserless
are explicitly deferred in [the manifest](../providers/manifest.json); selecting
a deferred provider must eventually return an unsupported-provider error, never
silently use a different provider.

## Small adapters, shared CDP

Keep one normalized session handle: canonical persona ID, base revision, session
ID, provider ID, provider resource IDs, authenticated CDP connection reference,
target IDs, observed browser contract, and capability report. URLs containing
credentials are secrets and must not appear in normal logs or status API responses.

This is a proposed interface shape, not exported application code:

| Operation | Provider-specific responsibility | Shared behavior |
| --- | --- | --- |
| `capabilities()` | Supported profile import/export, configuration, files, viewing, CDP/version limits | Validate required capabilities and show fidelity differences |
| `launch(fork, config)` | Local process/profile path or Browserbase session/context creation; provider identity options | Return session handle; bind explicit targets |
| `pushState(session, revision)` | Native profile/context handling and settings unavailable over CDP | Cookie, origin storage and emulation import through standard CDP where possible |
| `pullState(session)` | Native profile snapshot/provider persistence status | Portable state export with scope/completeness and tombstones |
| `upload(session, artifact)` | Stage a real file on the remote browser host or use local path | Set the intended file input via CDP |
| `download(session, artifact)` | Retrieve provider-stored bytes or local download | Verify size/hash, store in canonical artifact filesystem |
| `screenshot(session, target)` | Override only for an actual provider limitation | Default to `Page.captureScreenshot` |
| `view(session, target, mode)` | Browserbase live-view URL; local headed/noVNC details if needed | CDP screencast/input when supported |
| `finish(session)` | Graceful stop, persistence completion, provider close semantics | Check-in outcome/evidence, end ordering, export validation and success-gated leader promotion |
| `kill(session)` | Terminate/release owned resource | Mark interrupted, preserve available state; do not claim export succeeded |

Navigation, DOM/accessibility observations, evaluation, cookies, input, emulation,
target selection, and most screenshots stay in one CDP implementation. Provider
checks do not contain `if Browserbase` logic. Standard CDP is still negotiated:
test required commands and target behavior on the actual provider/binary. Do not
invent API parity where an endpoint filters a command.

The adapter reports applied, unsupported, unavailable, and unverified values.
Configuration precedence is canonical desired settings → explicit run override
→ documented provider defaults. Record the effective result. A missing setting
must not silently become a different fingerprint. A provider's supported browser
identity/stealth preset can constrain other overrides; validate the combination
instead of layering contradictory user-agent/client-hint values.

## Local adapter

Use installed `abx-plugins/plugins/chrome/chrome_utils.js` via its package location,
with `abxpkg` resolution and the existing launch/CDP/session helpers. Do not copy
the helper or make Stagehand download another browser. The local runtime can use
the same `abx-dl` image/build as ArchiveBox.

Create each fork from immutable, quiescent state into a unique user-data directory.
Keep a local session's profile separate from its Chrome marker/output directory.
On finish, export tab-scoped/portable state before shutting down, wait for Chrome
to exit, then preserve native state and promote. An ongoing file copy of an active
profile is not a coherent export. CoW is an optimization, never a required storage
backend; do not hardlink files that Chrome will modify.

ArchiveBox's profile copying and lifecycle are a useful starting point. The new
work is authority, checkout/check-in, completion ordering, and retention before
its runtime cleanup. Import/export changes should follow generic hook interfaces.

## Browserbase adapter

Use its SDK/API for session creation, explicit release, uploads/downloads, native
viewer URLs, and provider settings. Attach the common CDP driver to the returned
endpoint. A disconnect is not necessarily a released session when keepalive is
enabled, so finalization must explicitly reconcile resource state.

Browserbase documents persistent Contexts with cookies, origin storage and other
profile state, saved on session close. It advises waiting for persistence and
avoiding concurrent logins with the same Context. Consequently, concurrent forks
must not all write to one mutable provider Context. Use an isolated destination
for each fork, or an immutable seed only if actual provider semantics qualify.
Account-checker alone selects the canonical leader. [Contexts documentation](https://docs.browserbase.com/platform/browser/core-features/contexts).

The inspected [Get Context API](https://docs.browserbase.com/reference/api/get-a-context)
returns metadata; [Create Context](https://docs.browserbase.com/reference/api/create-a-context)
accepts project/name, not a native Chrome profile upload. Do not assume a profile
archive export/import/clone API exists. The first cross-provider implementation
therefore needs a verified portable state path through the live browser. Native
Context persistence is useful, but does not prove recoverability into local Chrome.

Before an account depends on nonportable state, either implement that round-trip
or report the account/runtime pair as unsupported. Preserve any provider-only
state as a clearly identified replica, not as proof that the app owns a complete
canonical backup. If a remote session dies before portable export, show incomplete
export and retain the last durable canonical revision.

Browserbase already supplies [live viewing](https://docs.browserbase.com/platform/browser/observability/session-live-view),
[uploads](https://docs.browserbase.com/platform/browser/files/uploads), and
[downloads](https://docs.browserbase.com/platform/browser/files/downloads).
Use these where a local path cannot represent a remote file. Keep screenshot and
browser actions on CDP unless an observed limitation requires an adapter override.

Hosted browser choice is also a data-location choice: the provider receives
cookies and rendered research content, even when inference is entirely local.
Recording, console/network logs, storage retention, and region must be deliberate
per-deployment settings. Local inference alone does not mean local-only data.

## Reuse from stagehand-v4 teleport

Inspected `stagehand-driver/recording/teleport.ts` and its `browser_state.ts` and
`replay.ts` dependencies in your existing `stagehand-v4`
checkout. Useful pieces are the explicit source capture, remote CDP attachment,
distinct destination session IDs, state-before-actions ordering, and provider
launch boundary in `BBSdkClient`.

Do not import the whole Stagehand event/replay system merely to get cookies. The
teleport flow requires a Stagehand extension and custom `Mod.*`/`Stagehand.*`
commands; these are not standard CDP. Reuse/generalize the underlying state logic
behind the shared Chrome interface where packaging permits. Keep an optional
Stagehand-assisted authoring path separate from the mandatory CDP contract.

Observed gaps requiring real tests:

- Cookies are reconstructed from selected fields; partition metadata is omitted.
- Origin storage capture skips every origin except the active page's origin.
- IndexedDB and OPFS exports currently return `coming soon...` placeholders.
- Geolocation capture is commented out.
- Storage replay is inserted after the first navigation, so initial site scripts
  can already have run. Session restoration needs a qualified bootstrap/init path.
- Empty cookies are omitted during restore, which is unsuitable for explicit
  deletion/tombstone semantics. Permission replay covers granted permissions.
- Optional UI-state capture collects form values, including password/OTP-shaped
  fields; it must be disabled/redacted for Session Tender. Its default is enabled.
- Teleport starts a recorded, keepalive Browserbase session; stopping the stream
  disconnects CDP, not an explicit provider release/export/promotion workflow.

These are source observations, not demonstrated live failures or requests to
modify stagehand-v4 in this phase. Replaying an entire old action history is not
the default persona import operation; avoid repeating one-time login submissions.
