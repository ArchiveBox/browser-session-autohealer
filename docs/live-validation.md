# Live validation · 2026-10-04

All execution history and old artifacts were deleted at the user's request. The
current persona state and real HN/LinkedIn task configuration were retained.

| Session | Site | Provider | Result |
| --- | --- | --- | --- |
| 65 | Hacker News | Local abx-dl | Passed |
| 66 | Hacker News | Browserbase | Passed |
| 67 | LinkedIn | Local abx-dl | Passed |
| 68 | LinkedIn | Browserbase | Passed |
| 69 | LinkedIn fix + read-only verification | Local abx-dl | Both tasks passed; adopted |
| 70 | Interactive Hacker News browser | Local abx-dl | Check passed; closed and discarded |
| 71 | Interactive Hacker News browser | Local abx-dl | Closing check passed; adopted |

Sessions 65 and 66 forked the same canonical checkpoint before either finished.
Later sessions used the latest adopted checkpoint. Session 69 preserved the
already-signed-in account and verified it; it did not need to fill credentials.
Session 70 exposed an incorrect blanket rejection of interactive sessions. Its
recorded rejection is retained. Session 71 was opened, used, and closed through
the real UI after the fix: no check ran during interaction; its HN check started
after interaction ended, passed, exported, and became the persona leader.

All checks used real OpenCode `openai/gpt-6.1-sol` and browser-harness. Browserbase
used residential proxies without Verified, which this account cannot enable.
No mocked sites, simulated browser results, or deliberately broken logins were
used. Only HN/LinkedIn site data plus browser settings were transferred.

Validation: `tests/test_usage.py`, `tests/test_fixes_lineage.py`, and
`tests/test_provider_color.py` inspect these real runs, real cached provider token
counts, actual leader-change history, and the HTTP forms. Other older acceptance
tests contain deleted historical run IDs and are not evidence for this reset.
