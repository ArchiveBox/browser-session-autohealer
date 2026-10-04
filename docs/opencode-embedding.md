# Embedded OpenCode

`/agents` embeds OpenCode using Session Tender's normal admin session. There is
no separate OpenCode login or copy-password route. The loopback upstream keeps
its private service credential, injected server-side.

## Reused implementation

`app/core/opencode_proxy.py` vendors HTTP forwarding, SSE event streaming,
WebSocket forwarding, origin checks, redirect handling, asset rewriting and
caching from `abx-plugins/abx_plugins/plugins/opencode/runtime.py`, revision
`ad1847a2b1a47d327b1f9b1fd3c357469dec815c`. Its MIT license is alongside the copy.
`opencode_seed.html` reuses that plugin's `templates/agent.html` storage setup.
The routing/authentication boundary follows `archivebox/opencode/views.py` at
`7abb0fe69d7ba834f796226a72b976c4b9f346ff`.

Local differences:

- Mount prefix is `/agents/opencode`.
- Lifecycle uses the existing Session Tender launcher and model settings.
- The wrapper selects retained sessions instead of creating a default session.
- HTTP, SSE and WebSockets inject the private upstream Authorization header.
- Plain AuthView replaces the Django adapter. Plain's native socket messages
  adapt to the copied ASGI receive/send contract. AsyncStreamingResponse carries
  the same SSE generator, without another server or gateway.
- Backend credential failures become service errors, never Basic Auth dialogs.
  App login and origin checks run before forwarding requests.

No sibling repositories or upstream images were changed. OpenCode upgrades still
require real browser validation of ArchiveBox's compiled-JavaScript rewrites.

## Acceptance

`uv run pytest tests/test_opencode_embed.py -q` exercises the real app and OpenCode:
embedded HTML, JavaScript assets, health, auth/origin rejection, live SSE, and a
real temporary PTY round trip over WebSocket. The PTY is removed afterward; no
account, persona, check, or conversation fixtures are manufactured.

In Brave, a retained LinkedIn session was opened and a follow-up sent to GPT-6.1
Sol. Its answer streamed into the embedded conversation without another login.
The recorded check verdict did not change.
