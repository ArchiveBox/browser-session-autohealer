# Check history

The check subsystem uses an append-only change log and an on-demand projection.
`Check` and `CheckRun` are convenient current-state rows; `CheckEvent` retains
recorded definition edits, schedule changes, execution start/finish, source
versions, and individual browser-harness invocations. Event writes and their
current-state updates share short database transactions. No browser/inference
work runs inside those transactions.

`CheckType` is an immutable version of a script, with language, exact
source, domain patterns, author, creation time, digest, and previous-version link. `CheckRunStep`
connects each execution to the version actually invoked and its exact retained
arguments/output. Scripts may have human or agent authors; the current worker
executes agent-generated browser-harness programs. The **Check Types** view
provides Plain forms for creating scripts and saving new revisions. Editing a
script never changes a previous invocation's source. A saved revision with no
invocations shows no executions; saving alone does not run it. A separate manual
script execution mode is not implemented.

`domain` is a normalized CSV of host patterns, for example
`google.com, *.google.com`. The wildcard matches subdomains; include the bare
domain separately when needed. Linked checks must match the patterns. The domain
filter matches an input host against each current revision, and patterns appear
beside every Check Type title. The initial domain migration appends metadata
revisions without rewriting historical source records.

A condition execution (`CheckRun`) has a UUID7 public identifier, UTC start/end,
running/success/failure status, artifact directory, final screenshot path,
transcript path, evidence summary/error, and structured findings. A browser
session can contain several condition executions. Passing conditions do not
force the browser session to succeed or promote: any session-level issue still
blocks promotion.

`services.check_projection(check_uid, as_of=None)` folds the event stream and
returns the definition, schedule, executions, versions, and exact invocations
known at that timestamp. The frozen execution inputs include the condition,
target account label and URL, inference model, browser settings, provider
configuration, and base-checkpoint digest. Current source and revision periods
are derived rather than updating previous source rows to close them.

PostgreSQL rejects UPDATE and DELETE on events, source versions, and invocation
records. Public UUID7 IDs are separate from the framework's integer primary keys,
so existing links and retained evidence remain valid.

## Inspecting history

- Open a persona's Credentials table and click **History** on a check.
- Select a timestamp or follow an entry in the expandable change log.
- Inspect screenshots and results, then expand exact inputs/output or source data.
- Integrations can GET `/api/check-history?check=<check-uuid>&at=<ISO-UTC-time>`
  with the existing private API bearer token. Omit `at` for current recorded state.

The log's boundary is **when this app recorded a fact**. Original executions
predating log adoption retain their actual artifacts and recoverable execution
times, with provenance labels. Unknown earlier edits and unavailable timings
remain unknown. Importing an older failure never makes it the newest access
result, and importing older source never replaces a newer current source.

This event stream currently covers checks. Persona checkpoints remain in the
existing immutable state store; it does not imply that all historical persona
settings, accounts, or provider configuration edits have been event-sourced.
