# Research and reuse inventory

Inspected on 2026-10-03. Findings below distinguish source/documentation evidence
from runtime qualification. No real user profile was opened or changed,
no mailbox/vault contents were fetched, and no hosted browser was launched.

## Existing workspace code

Source snapshots: `abx-plugins` `477ccfe8`, `abx-dl` `270b93bc`, ArchiveBox
`098a3f036`, browser extension `447cc8a`, stagehand-v4 `bb4455b1`. Several checkouts
have pre-existing changes; these IDs identify inspected bases, not clean releases.

| Source | Reuse | Limit/change needed |
| --- | --- | --- |
| `abx-plugins/abx_plugins/plugins/chrome/README.md`, `config.json` | Browser setup contract, binary dependencies, CDP adoption, keepalive | These do not implement canonical persona revision management |
| `chrome/chrome_utils.js` | `ensureChromeSession`, `connectToPage`, target IDs, session locks/liveness, imports, extensions, downloads | Browser lifecycle, repeated imports, and cookie attribute fidelity need integration tests |
| `chrome/on_Snapshot__01_chrome_tab.daemon.bg.js` | Per-target lifecycle and color-scheme application | Most of the desired environment contract is not applied here |
| `archivebox/archivebox/personas/models.py` | Derived config, persona dirs, locked per-crawl copying, per-snapshot forks | Runtime cleanup deletes fork state; no inspected last-ended-leader publication |
| `archivebox/archivebox/services/runner.py` | Persona preparation and runtime cleanup points | Add checkout/check-in through a generic session contract |
| `archivebox/archivebox/api/v1_personas.py` | Metadata listing and extension sync ingress | Canonical ownership must move to Session Tender without writing ArchiveBox's DB behind its API |
| `../archivebox-browser-extension/src/lib/personaSync.ts` | Immutable selected-server request, cookies/settings payload | No local/session storage, IndexedDB, or OPFS in this sync payload |
| `../archivebox-browser-extension/src/lib/personaSettings.ts` | Browser environment observation | Viewport currently comes from an extension page; verify the intended research-tab dimensions |
| `abx-dl/Dockerfile`, `archivebox/Dockerfile` | Shared local browser/runtime image | ArchiveBox adds server/agent dependencies; hosted Browserbase is a different runtime |

Current Chrome helper exports use Puppeteer, with exact target lookup rather than
the active tab. This is the safest initial browser interface to retain. Standard
CDP can also support Stagehand/Playwright without a second browser launcher.

ArchiveBox's persona cleanup lists `Sessions`, `Sessions_Encrypted`, and Chrome
session/tab files as volatile. Copying that cleanup behavior into canonical
revisions would undermine the requested restart continuity. Separate disposable
process locks/caches from durable auth and tab state.

The extension sends same-site/security/expiry cookie fields, but its serialization
omits partition/host-only metadata. The Chrome importer rejects a supplied
extension partition key; the current sender can omit it before it reaches that
check. JSON auth import consumes cookies, not a complete storage-state bundle.
Missing optional Netscape cookies and malformed explicit JSON have deliberately
different behavior; preserve that existing contract.

The API stores language, timezone, geolocation, and device scale in persona config.
The inspected Chrome plugin applies UA/resolution and color scheme, but no matching
application of those other stored `BROWSER_*` settings was found in that path.
The launcher also rewrites the Chrome version within a configured UA. A configured
value therefore is not evidence of the actual browser-observed value.

Stagehand teleport findings and the two-provider boundary are in
[providers.md](providers.md).

## Old screenshot QA

Found the actual script on ugNAS at
`/mnt/nvme/opt/archivebox-spreadsheet-bot/ai_qa.py` (209 lines).
The local caller is `old/archivebox.ts:3380`, `saveAIQualityAssuranceResult`.
The caller chooses a cropped screenshot when available, invokes the script,
parses JSON, and adds URL/version metadata.

Reusable ideas: content-present, login/captcha/popup/banner obstruction, missing
content, visible/obscured estimates, and observed error text. Extend those into
separate identity/access/content observations and multiple simultaneous blockers.

Do not reuse the implementation unchanged: it sends screenshots to GPT-4o,
extracts JSON heuristically, lacks the new local-classification/secret boundaries,
and conflates content QA with generated summaries. Its ASCII-only instruction is
unsuitable for multilingual evidence. Percentage visibility is an estimate, not
a calibrated measurement or acceptance assertion.

The source contains hardcoded API-key defaults, including a commented key. Values
were redacted before tool output and were not copied into this project or tested.
Those historical credentials should be reviewed/rotated by their owner if still
active. This is an observed source issue, not a hypothetical warning.

## Agent harness and UI

A subagent read “OpenCode UI Replacements”
(`codex://threads/01a0b76b-6719-7990-ac26-9ff9897aba60`) and refreshed current
upstream sources. Retained requirement: approachable graphical chat with provider,
model, session, tool, and subagent UI supplied off the shelf. Multi-provider is
different from multi-harness.

| Candidate | Recommendation | Qualification gap |
| --- | --- | --- |
| OpenCode server + web | First prototype; reuse known runtime/provider experience and HTTP/SSE orchestration | Verify programmatic incident sessions, tools, cancellation and restarts on pinned binary |
| OpenChamber + OpenCode | Preferred richer UI to evaluate next | Current docs require OpenCode 2.x; inspected plugin pins 1.18.31 |
| OpenHands / Agent Canvas | Keep as a broader-workbench alternative | Adds agent-server/automation-server integration beyond initial need |
| CloudCLI / ClaudeCodeUI | Useful if multiple harnesses become essential | CLI login onboarding; subpath and subagent parity need tests; AGPL-3.0 |
| Hermes | Reconsider for a desktop-first product | Web dashboard remains terminal-oriented; newer desktop has graphical Simple mode |
| assistant-ui / Chainlit / Gradio | Do not select as turnkey UI | They leave substantial session/provider/setup work to us |

OpenCode and OpenChamber are MIT licensed. Use a dedicated origin at first rather
than inheriting the compiled-JS prefix rewrites in the ArchiveBox embed. The older
OpenChamber prefix bug has been fixed in current source; full prefixed operation
is still unqualified. Never repeat the old source defect as a current finding.

Local reuse points: `abx-plugins/.../opencode/runtime.py` config/state layout and
server/session startup; `archivebox/.../opencode/views.py` auth/proxy handling.
Do not import the fixed prefix, singleton globals, or ArchiveBox prompt wholesale.

Sources: [OpenCode server](https://opencode.ai/docs/server/),
[web UI](https://opencode.ai/docs/web/), [MCP](https://opencode.ai/docs/mcp-servers/),
[OpenChamber providers](https://github.com/openchamber/openchamber/blob/main/packages/docs/content/docs/providers.mdx),
[security/version policy](https://github.com/openchamber/openchamber/blob/main/packages/docs/content/docs/security.mdx),
[URL helper](https://github.com/openchamber/openchamber/blob/main/packages/ui/src/lib/runtime-url.ts),
[Hermes web dashboard](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/web-dashboard.md),
[Hermes desktop](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/desktop.md),
[CloudCLI provider contract](https://github.com/siteboon/claudecodeui/blob/main/server/modules/providers/README.md),
[OpenHands](https://github.com/OpenHands/OpenHands).

## Local classifiers

| Candidate | Verified source facts | Fit for this prototype |
| --- | --- | --- |
| Clef / Clef-Flash | Apache-2.0 decision models with vision; Clef-Flash is 9B, published repository about 19.1 GB | Useful typed-decision API design; does not satisfy sub-1-GB small-CPU requirement as published |
| Jev | Official introduction describes hosted early-access System One decisions | No verified downloadable local-weight path from the inspected official sources |
| GLiClass Instruct Edge v1.0 | 32.7M parameters, 131 MB weights, repository about 143 MB, Apache-2.0, text labels/instructions | Concrete small-download candidate; benchmark CPU latency, false healthy rate and multilingual accuracy |
| Small supervised encoder | Requires real labeled incidents | Sensible later baseline; compare with rules and zero-shot before adding complexity |

A small weight file does not guarantee small total dependencies or RAM. Measure
CPU runtime packages, cold start, warmed latency, peak RSS, and disk use separately.
No local model was downloaded or benchmarked in this design phase. Do not present
vendor GPU latency as local CPU latency. Text models cannot independently establish
visual occlusion; use geometry/visibility checks and optional OCR/vision.

Sources: [Cloudflare Clef announcement](https://blog.cloudflare.com/clef-decision-models/),
[Clef-Flash model card](https://huggingface.co/Cloudflare/clef-flash),
[published files](https://huggingface.co/Cloudflare/clef-flash/tree/main),
[Jev introduction](https://typesafe.ai/blog/introducing-system-one-models-and-jev),
[GLiClass Edge model](https://huggingface.co/knowledgator/gliclass-instruct-edge-v1.0),
[Edge files](https://huggingface.co/knowledgator/gliclass-instruct-edge-v1.0/tree/main).

## Browser automation and secret inputs

Stagehand `act` supports variable substitution without sending variable values
to the model and documents reducing verbosity to avoid logging secrets. It can
operate on supplied pages; qualify target binding and cleanup against our pinned
CDP session. Browser-use supports domain-scoped `sensitive_data`; its own docs
recommend disabling vision to prevent screenshot leaks. Neither mechanism stops
an unrestricted coding agent from reading the secrets via filesystem/CDP access.

Use Puppeteer/shared CDP as the baseline, Stagehand as the first assisted authoring
candidate, and Playwright for real acceptance flows. Agent-browser's strict
`--pin-tab` option is useful if later adopted; target binding must still be
verified against our exact target ID. “browser-harness” is not pinned to a specific
repository/version here, so it is not treated as a verified dependency.

Sources: [Stagehand act](https://docs.stagehand.dev/v3/basics/act),
[Stagehand configuration](https://docs.stagehand.dev/v3/references/stagehand),
[browser-use sensitive data](https://docs.browser-use.com/open-source/examples/templates/sensitive-data),
[agent-browser sessions](https://github.com/vercel-labs/agent-browser/blob/main/docs/src/app/sessions/page.mdx).

## 1Password and mail

The official 1Password Environments MCP lists/manages environment variables and
local mounts; it deliberately never returns stored secret values. It is useful
for setup, but not a generic Login-item/OTP-fetching server. The official SDK can
resolve secret references; CLI `op item get --otp` returns a current code. Use
those within the trusted executor and expose only opaque fill/consume operations.
Browserbase also documents a 1Password login integration; it still needs our
origin, recording, and runtime isolation policy.

Sources: [1Password MCP](https://www.1password.dev/environments/mcp-server),
[SDK setup](https://www.1password.dev/sdks/setup-tutorial/),
[CLI item reference](https://www.1password.dev/cli/reference/management-commands/item),
[SDK releases, including OTP resolution](https://releases.1password.com/developers/sdks/),
[Browserbase 1Password](https://docs.browserbase.com/integrations/1password/quickstart).

Candidate mail transports were reviewed at documentation level, not installed:

| Option | Useful property | Decision |
| --- | --- | --- |
| Gmail read-only API | Scoped OAuth and incremental retrieval; push/history available | Preferred Gmail path behind a local challenge matcher |
| ImapFlow | Existing Node IMAP client with read-only selection and retrieval primitives | Preferred generic IMAP building block; enforce read-only methods locally |
| `dtaveeva/gmail-multi-mcp` | Documented per-account readonly tier and keychain/encrypted token storage | Reusable candidate to qualify behind matcher; not broad agent mailbox access |
| `wildsurfer/your-mail-mcp` | Read-only tools over local notmuch/mbsync index | Useful when that index already exists; whole-mail mirroring is unnecessary for short-lived OTPs |
| `SinoEdwards/mail-agent-mcp` | Gmail/IMAP support and documented read-only mode | Alternative candidate; verify granted scopes and absence of writes |

No reviewed MCP establishes correct challenge matching by itself. The local
matcher must bind recipient/site/account/time/attempt and return only an expiring
handle. Gmail metadata scope cannot read message bodies; read-only is still broad
mailbox access. A local classifier reduces inference disclosure, not OAuth scope.

Sources: [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes),
[Gmail push](https://developers.google.com/workspace/gmail/api/guides/push),
[ImapFlow](https://imapflow.com/docs/api/imapflow-client/),
[gmail-multi-mcp](https://github.com/dtaveeva/gmail-multi-mcp),
[your-mail-mcp](https://github.com/wildsurfer/your-mail-mcp),
[mail-agent-mcp](https://github.com/SinoEdwards/mail-agent-mcp).
