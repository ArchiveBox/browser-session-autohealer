# Screenshot sources

[← Session Tender](../../README.md)

Captured October 4, 2026 from the running local Session Tender server at
`http://127.0.0.1:8421`. These are screenshots of the actual UI and its retained
browser evidence. Crops frame existing content; no labels, outcomes, accounts,
or browser content were fabricated or composited.

| Image | Source | What it establishes |
| --- | --- | --- |
| [accounts.png](accounts.png) | `/personas/4` | Nick's real HN, LinkedIn, and X accounts, with final evidence thumbnails and latest recorded Local / Browserbase results. Local runs 90, 91, 89; Browserbase runs 66, 68, 82. |
| [x-blocked.png](x-blocked.png) | `/runs/80/checks/18` | Local X recovery failed. The recorded agent result reports a temporary login restriction; the agent stopped without submitting a password. |
| [x-verified.png](x-verified.png) | `/runs/82/checks/19` | Browserbase X check confirmed @ArchiveBoxApp and readable home-timeline posts, after the recovery task selected that existing signed-in account. |
| [lineage-providers.png](lineage-providers.png) | `/personas/4/lineage`, beginning of horizontal scroll | Local run 65 and Browserbase run 66 branch from the initial persona and return successful checkpoints. |
| [lineage-parallel.png](lineage-parallel.png) | `/personas/4/lineage`, end of horizontal scroll | Concurrent Local HN / LinkedIn runs 90 and 91 return successful checkpoints. Run 91 becomes the current leader. An earlier discarded branch also remains visible. |

## Recovery sequence

The two X images are **different runs, on different providers**, not an
uninterrupted password-login repair. Between runs 80 and 82, browser state was
imported from the user's signed-in Brave profile. In run 82, task 20 selected
@ArchiveBoxApp from the existing signed-in accounts without submitting
credentials. Task 19 then independently confirmed the account and readable feed.
The stored recovery result explicitly says repeat repair stability is untested.

These captures demonstrate specific recorded outcomes, not uptime, universal
site support, CAPTCHA solving, or indefinitely durable authentication. The
screenshots include the user's real account identities at their request.
