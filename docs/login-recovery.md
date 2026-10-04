# Login recovery

Open **Integrations** in the app to test connections, bind credential sources to a
site, or start **Restore login**. The OpenCode conversation and masked browser
steps appear on the resulting page. A separate access check must pass before
the restored profile can become a leader. No social posting is supported.

## Data path

OpenCode uses browser-harness for page HTML, screenshots, navigation, submission,
and judging the result. Its `private_login` MCP exposes three scoped tools:
`request_placeholder`, `act`, and `follow_verification_link`. It does not expose
vault browsing, raw mailbox reads, arbitrary 1Password commands, or messaging.

The broker resolves a short-lived opaque placeholder locally. Stagehand **3.7.3**
receives its value through the native `variables` argument, while its action
instruction contains `%variable%`. Both inference loops use **OpenAI
gpt-6.1-sol**. Stagehand handles one field action per call; browser-harness then
inspects the result. No site-specific login selectors are hardcoded.

Known values are encrypted locally, redacted from subsequent tool text, and
masked in browser-harness screenshots. Stagehand's inference middleware removes
known reflected values before a model request. Raw screencasts are disabled
during credential entry; the UI shows masked step screenshots instead. HTML and
screenshots remain available to the agent. Protection is **best effort**: unknown
PII, canvas text, QR codes, cross-origin frames, and transformed values may remain
visible. The browser and destination site necessarily receive the credential.

Bindings contain references and routing rules, not passwords. They live in
`~/.config/account-checker/recovery.json`. One-time messages are matched by source,
freshness, sender, recipient, and subject/conversation. Ambiguous matches stop;
consumed message identities have keyed local replay receipts. Remote IMAP uses
TLS and requires the receiving mailserver's trusted DKIM authentication result.
These connectors never mark mail read or send messages.

## Current connections

| Source | Implemented | Verified on this installation |
|---|---|---|
| 1Password | Vault-scoped `op read` for username/password and OTP references | ArchiveBox vault connected. The selected LinkedIn item's username/password resolve through local single-use placeholders; both LinkedIn checks inherit the binding. HN has no credential access. No OTP field is configured. |
| IMAP | Read-only recent-message retrieval and local code/link extraction | Real Postfix/Dovecot inbox, SMTP delivery, replay prevention and browser login tested. No personal mailbox connected. |
| iMessage | Local `imsg` reads for an exact conversation and allowed senders | Official signed binary installed. macOS Messages database access is blocked pending Full Disk Access. No messages read. |
| Google Voice | Read-only upstream MCP bridge with login/history tools | Upstream code installed privately; disabled until an isolated test-account browser is signed in. Timestamp/output compatibility still needs authenticated verification. |

The Google Voice adapter currently requires ISO timestamps with timezone offsets;
human-formatted upstream timestamps are rejected. It must not be called fully
supported until a real account test establishes reliable freshness and sender
metadata. Authentication to the other three sources cannot be inferred from
the passing IMAP test.

For 1Password, unlock the app and enable its CLI integration, then select the
vault and account in **Integrations**. Use item references
such as `op://<vault-id>/<item-id>/password` and
`op://<vault-id>/<item-id>/one-time password?attribute=otp`. The OTP seed must never
be returned as the code. The connection probe reads the selected vault directly:
app-integrated reads can succeed even when `op whoami` reports no CLI session.
The real linked-item acceptance test is `uv run pytest tests/test_onepassword_binding.py -q`.

## Reproduce the local test

```bash
uv run python bin/setup_test_mail.py
uv run pytest tests/test_secret_broker.py tests/test_local_browser_shutdown.py -q
uv run python bin/test_login_recovery.py
# After the worker finishes recovery and its automatically queued access check:
ACCOUNT_CHECKER_RECOVERY_RUN=<printed-run-id> uv run pytest tests/test_login_recovery.py -q
```

The mailbox uses docker-mailserver 15.1.0 on loopback SMTP **54025** and IMAP
**54143**, with a generated encrypted local password. The E2E command expects
this installation's **Test researcher** persona, **Local test browser** host
connection, `researcher@account-checker.test` app user, and local workspace check.
It rotates only that disposable app user's password, sends it through real
SMTP, and starts recovery from the persona's original empty checkpoint. The
test uses the actual app login; there is no simulated login server or canned
browser result. The local app user has admin access to this disposable test
installation, so do not run this test against a collection containing research
accounts. IMAP code extraction is tested independently with real messages.

The personal collection was removed from active storage at the user's request.
A private local rollback backup is retained outside the active data root. Never
import that backup or the everyday Brave profile into a remote browser test.
Browserbase tests require separate disposable accounts and a reachable test
service; see [the Browserbase implementation plan](browserbase-support.md).

## Retained acceptance evidence

On 2026-10-04, real LinkedIn recovery **62** used OpenCode `gpt-6.1-sol`,
browser-harness, and the local credential MCP to retrieve the selected 1Password
item through two single-use placeholders. Stagehand filled username and password.
Fresh local browser **63** and Browserbase residential-proxy browser **64** both
passed the signed-in feed check, with screenshots and no run issues. Only the
fresh verification runs were eligible for persona-leader promotion. The latest
HN checks, **58** local and **59** Browserbase, also passed.

The initial Docker attempt **61** stopped before submission because the Stagehand
dependency mount did not end in `node_modules`. The corrected mount was verified
with a real container import and recovery **62**. The 1Password acceptance test
also verifies destination restrictions, single-use placeholders, actual MCP and
Stagehand calls, fresh-browser verification, and absence of known raw values in
saved non-profile artifacts. Screenshots and reflected-value masking remain best
effort.

### Earlier disposable-account validation

On 2026-10-03 (America/Los_Angeles), recovery **44** started with zero cookies,
retrieved its disposable password from the real IMAP inbox, filled it through
Stagehand variables and signed in. Fresh browser **45** loaded the saved profile,
passed the English workspace check, saved a final screenshot, and became the
verified site leader. Neither attempt recorded an issue. The recovery attempt
itself is deliberately ineligible for promotion; only its fresh verification can
establish access. The generic internal Run status is currently `failed` for this
unverified candidate, while the recovery UI projects the verified outcome.

Earlier attempt **43** had a real browser-harness argument error. The strict gate
retained its failure and did not create a verification candidate or promote it.
The agent now receives actual function signatures from the installed harness.
The local shutdown test first reproduced Chrome remaining alive after CDP close;
the adapter now waits for process exit before copying its native databases.

Validation: four real-mail tests, one real-browser shutdown test, and two retained
E2E evidence assertions; all passed. The E2E assertions verify fresh-process
authentication, leader selection, screenshot files, real tool transcripts,
Stagehand's inference redaction counters, and absence of known raw secret values
in non-profile run artifacts. This is evidence for the tested secret and paths,
not a guarantee of perfect screenshot or PII removal. Plain preflight, database
schema checks, Ruff, and npm audit passed (zero reported npm vulnerabilities).
