# Persona checkouts, checkpoints, and successful check-ins

Current design decision, 2026-10-03. This supersedes the original unconditional
“last-ended session leads” proposal. The interactive interface contains example
data only; neither a version store nor provider lifecycle is implemented yet.

## The user-facing model

Browser Session Autohealer owns the central collection. A provider checks out a specific
checkpoint into an isolated writable copy. It can record checkpoints and check
events on that run's branch. When finished, it checks the copy back in with an
explicit outcome and evidence. Storing the return and promoting it are separate
operations. Failed runs remain visible and comparable.

Show, per provider and persona:

- Last checkout time and the exact base checkpoint.
- Number of copies still checked out, with their owners and heartbeat state.
- Last successful check-in's checkpoint, completion time, and verified scope.
- Latest attempted check-in and its failure/unknown/pending reason, even when
  the latest successful return is much older.

Record consumer and runtime separately. ArchiveBox can borrow a persona through
the Local Chrome adapter; ArchiveBox is not a third browser runtime. A profile
import from a human's Chrome path or CDP endpoint is a separate ingress event,
not a claim that the human browser has been leased or may be stopped.

## Promotion policy

Default automatic eligibility requires all of:

1. The entire run reports success.
2. No issue occurred anywhere in that run. A later successful retry inside the
   same run does not erase the issue. A fresh checkout can establish new success.
3. Every required check for the run's predeclared scope passed, including identity
   and content checks against the final state being returned.
4. The final export is complete, validated, and durable; required coverage and
   observed configuration match the pinned contract.
5. The session's authorization/configuration epoch is still valid and its result
   has not already been processed or superseded.

An error, crash, timeout, failed capture, missing proof, unknown outcome, or export
failure prevents automatic promotion, even if cookies appear useful. Preserve
coherent checkpoints; label incomplete artifacts explicitly. Do not silently
“recover” a failed run's status by clearing its recorded error.

Among eligible check-ins for the same scope, the most recently ended run wins,
using coordinator-assigned completion order. Upload arrival order cannot reverse
that decision. A later-ended pending return does not advance a pointer until it
passes the full gate. Lease expiry means lost/unknown, never implicit success.

The UI separates three facts: **checkpoint stored**, **run succeeded**, and
**leader advanced**. Older successful runs remain successful even when another
eligible run has since become leader.

## One persona leader

Each persona has one leader pointing to a whole immutable checkpoint. Every
provider and site starts from that checkpoint unless a caller explicitly selects
a historical base. The latest eligible successful session replaces the pointer.
The database enforces uniqueness per persona; there are no site leaders.

A session declares its checks before checkout. Any issue blocks promotion, and
the required checks must pass with a complete export. Check scope cannot be
narrowed after a failure. A single-site success can advance the persona's state,
but does not establish access to other sites. Access badges and screenshots come
from each site's own latest checks, independently of leadership. Concurrent
SQLite/LevelDB databases are never merged.

Initial empty/imported checkpoints are bootstrap bases, labeled unverified until
checks pass. A deliberate human import can stage a new branch and launch its
validation; it must not silently overwrite an existing verified leader. Desired
configuration is separately versioned: a provider's observed setting change must
not overwrite a newer manual preference or resurrect revoked credentials.

## History and universal comparison

The persona's vertical graph includes creation, imports, checkout edges, state
checkpoints, check events, check-in outcomes, leader advancement, and active or
abandoned branches. Show state and outcome using labels and shapes as well as
color. A check event can reference an existing state checkpoint; it need not
create a fake state commit. Returning a branch to the main line means the whole
eligible snapshot was selected, not that concurrent databases were merged.

Imports show actor, hostname, profile path or CDP endpoint, time, coverage, and
counts added/updated/removed. Redact tokens embedded in endpoints. A checkpoint
has a stable full ID, a short display prefix, parent checkpoint(s), source run,
persona, desired-config version, format version, and state manifest. Provider
context IDs and session IDs are metadata, never checkpoint identity.

Use one Compare view, addressable by explicit `base` and `head` checkpoint IDs.
Every hash link, branch comparison, import review, and check-in review opens it.
Support unrelated branches and reverse direction, not just parent-child diffs.
Cross-persona comparison is read-only and must not offer an automatic merge.

Compare cookies by full identity (name, domain/host-only scope, path, partition
key), settings by schema field, and storage by origin/storage-key coverage.
Show additions, updates, explicit removals, and unsupported/unknown surfaces.
Missing export coverage must not look like deletion. Cookie and storage values
remain protected; compare sensitive values inside the trusted boundary and
return redacted change indicators. An unkeyed hash of a low-entropy secret is
not safe redaction. Native files have binary hashes/sizes and inspectable logical
projections where supported; they are not automatically mergeable.

Restoring a past checkpoint starts a new checkout and revalidates it. Old cookie
bytes cannot undo server-side token revocation. Retention and deletion must cover
history, object blobs, backups, and provider copies; a hidden ref is not erasure.

## Git / Git LFS as a storage option

Keep the public checkpoint/compare contract independent of the backend. A
content-addressed checkpoint manifest and append-only events can provide the
desired hashes, parents, branch graph, and comparisons with or without Git.

Git is a plausible implementation for manifest history and refs. Git LFS stores
large file contents outside Git while committing pointers, making it a candidate
for native profile snapshots. It does not itself provide the semantic cookie and
setting comparisons above; those remain application features. Evaluate object
growth and retention using representative profiles before selecting it.
Source: [Git LFS](https://git-lfs.com/).

Snapshot correctness comes first. SQLite's backup API can create a consistent
snapshot of an individual database. A whole Chrome profile spans more than one
database and storage format, so this alone does not provide a coherent live
profile snapshot. Quiesce the owned browser for final native check-in, or use an
explicitly qualified capture mechanism. Sources:
[SQLite backup API](https://www.sqlite.org/backup.html),
[SQLite WAL lifecycle](https://www.sqlite.org/wal.html).

The WAL is persistent database state: copying only the main database can omit
committed data or yield corruption. Do not run Git add/copy over a live mutable
profile and call that a validated checkpoint. Individual SQLite backups also do
not handle LevelDB, OPFS, or in-memory browser state. Live intermediate checkpoints
need an explicit coverage/coherence level and need not qualify for promotion.
Source: [SQLite WAL documentation](https://www.sqlite.org/wal.html).

Initial spike: compare a private Git manifest store with encrypted native blobs
(optionally LFS) against a small content-addressed filesystem plus SQLite event
index. Measure checkpoint latency, disk amplification, clone/materialization
cost, logical diff cost, restart recovery, and targeted history deletion. Do not
automatically publish profile history to the source repository or a Git remote.
No Git/LFS dependency is selected or installed by this design change.

## Real acceptance gates

- Five actual concurrent forks; four clean successes and one failure. Preserve
  every lineage and show correct active counts throughout.
- Successful older-base completion can lead when it is latest eligible. A failed
  run finishing later cannot displace it, even if its state export is valid.
- Failed, unknown, timed-out, incomplete-export, stale-epoch, and internally
  recovered-but-errored runs do not advance the persona leader.
- Site-only success can advance the one persona reference. Subsequent checkouts
  by every provider inherit it; unrelated sites retain their own check results.
- Delayed/duplicate check-in callbacks cannot reorder completion or double-count
  active copies. Expired leases are shown as unknown until reconciled.
- Graph nodes, displayed counts, checkpoint metadata, and the universal diff all
  derive from the same real manifests/events. Compare any two checkpoints,
  reverse them, and verify additions/removals against real profile state.
