# Local renderer crash investigation

Local X sessions 84, 86 and 87 reproduced renderer crashes, followed by
`Runtime.evaluate` / `Accessibility.getFullAXTree` timeouts and screenshot
internal errors. Docker reported no OOM events and unused shared memory.
A separate diagnostic CDP connection also received `Target crashed`.

Chrome's original launcher outlived its parent driver's logging pipes. The
Session Tender launcher now directs the same Chrome executable's output to
the session's private `chrome.log` for its entire lifetime. Session 87 retained
three identical SIGILL crashes. The program counter resolved to ELF offset
`0xb8f3850`: `cntd x9`, before `smstart sm` in an SME routine's prologue.

The Linux ARM64 VM exposes SME/SME2 but not non-streaming SVE. The instruction
requires SVE outside streaming mode. This matches the compiler failure described
in [LLVM issue 204853](https://github.com/llvm/llvm-project/issues/204853).
The stripped binary does not establish a precise source-level function name.

## Compatibility guard

The Session Tender derived image builds a small `getauxval` interposer, loaded
only by its Chrome launcher. On ARM64 **without SVE**, it clears only the SME and
SME2 hardware capability bits. All other bits remain unchanged. This prevents
native CPU dispatch from choosing routines with unsupported prologues; existing
Neon and other supported implementations remain available. Other architectures
and ARM64 CPUs with SVE retain their original capabilities.

The abx-dl image, Chrome binary, launch flags, timeouts and sibling repositories
are unchanged. The Chrome SHA256 before and after is
`839efe5fd8b6a773dd81b2e10afdc15f3c0a17316fb82b5908c0533306c4ed9e`.
This is a runtime compatibility workaround, not a fix to LLVM or Chromium.
Remove it only after a replacement browser build passes the affected workloads
on SME-without-SVE hardware without the guard.

## Verification

`tests/test_chrome_cpu_guard.py` loads the compiled library inside the real Docker
image and compares its capabilities with libc's actual values. The original
failing acceptance test is `tests/test_browser_stability.py`, which checks the
latest two real Local X sessions: every browser program, screenshot, final
result, issue list, live frames and native crash log must be clean.

```sh
docker build -f Dockerfile.browser -t account-checker-browser:dev .
uv run pytest tests/test_chrome_cpu_guard.py tests/test_browser_stability.py -q
```

Site checks continue to run through OpenCode and browser-harness. No site
selectors, synthetic account results or longer timeout limits were added.

### Observed results after the guard

Real Local sessions on October 4, 2026:

| Session | Site | Browser programs | Result |
| --- | --- | ---: | --- |
| 88 | X | 4 | Passed |
| 89 | X | 4 | Passed |
| 90 | Hacker News | 3 | Passed |
| 91 | LinkedIn | 4 | Passed |

All four checked in successfully with no recorded issues and were adopted at
completion. All 15 browser programs exited successfully and saved screenshots;
each session recorded live frames and had no native crash in `chrome.log`.
Sessions 90 and 91 also exercised concurrent, isolated browser copies. Session
89's result and screenshot were visually verified in the app. Both focused
tests and all 15 Plain preflight checks passed.

These runs validate the reproduced failure and this hardware combination;
they do not establish that arbitrary sites or browser builds cannot fail.
