# Kernel browsers

Browser Session Autohealer's Kernel adapter launches a fresh cloud browser for each checkout.
The application's shared CDP driver restores portable persona state, supplies the
live screenshot stream, and exports changed state before the browser is deleted.
Kernel profiles, browser pools, managed authentication, and native profile transfer
are not used by this adapter.

## Configure

Save `KERNEL_API_KEY` in the ignored `.env` file or the server and worker process
environment. Restart those processes after changing their environment. Keep the
key out of the provider's connection JSON.

Select **Kernel** when creating a browser provider. An empty connection config uses
a headful browser with stealth enabled and a 1,800-second inactivity timeout:

```json
{}
```

For an existing Kernel proxy and a supported region:

```json
{
  "stealth": true,
  "headless": false,
  "timeout_seconds": 1800,
  "region": "us-east",
  "proxy": {"id": "your-existing-proxy-id"}
}
```

| Connection field | Accepted values | Default |
| --- | --- | --- |
| `headless` | `true` or `false` | `false` |
| `stealth` | `true` or `false` | `true` |
| `timeout_seconds` | Integer from 10 to 259200 | `1800` |
| `region` | `us-east`, `eu-west`, `ap-southeast` | Omitted; Kernel chooses its default |
| `proxy` | Exactly one of `{"mode":"direct"}`, `{"mode":"default"}`, `{"id":"…"}`, or `{"name":"…"}` | Omitted |
| `proxy_country` | Uppercase two-letter country code; selects managed residential routing instead of `proxy` | Omitted |
| `proxy_state` | Two-letter uppercase state code; requires `proxy_country` | Omitted |
| `proxy_city` | City name without spaces; requires `proxy_country` | Omitted |

With stealth enabled and `proxy` omitted, Kernel uses its default static ISP proxy,
which keeps one exit IP for the session.
An explicit proxy ID or name selects an existing proxy in the browser's project;
without geographic overrides. Alternatively, managed geography causes the adapter
to look up a deterministically named residential proxy with exactly matching
configuration, then create it through `POST /proxies` if absent. Its stable ID is
passed to `POST /browsers`. This proxy configuration is retained for reuse after
browser termination; it is not a new proxy per session. Kernel documents that residential
proxy exit IPs rotate per connection. `{"mode":"direct"}` forces direct egress while preserving the selected
stealth setting. Region selection and some browser features depend on the Kernel
plan. Explicit saved proxy selections preserve API failures. For automatically
requested managed location routing, an explicit HTTP 403 `insufficient_plan`
falls back to Kernel's default route with a stored `location_routing_unavailable`
diagnostic and clears the unapplied routing fields from runtime configuration.
The request's desired location remains intact and independently enforced.

On October 6, 2026, the development account accepted managed residential proxy
configuration but rejected browser creation with that proxy using HTTP 403
`insufficient_plan`. This account therefore cannot prove managed residential
country/state/city targeting. Observed default-route geography can satisfy a
request, but does not establish paid routing support.
Real required-US and preferred-US requests both passed direct client connection,
independent observed-country verification, two authenticated HN checks, state
export, leader promotion and confirmed closure (runs 184 and 189). A required-CA
request observed US, returned `conditions_unmet`, ran no account checks, exported
and closed without promotion (run 194). Its unapplied routing configuration was
cleared and the plan diagnostic retained.

Required session locations replace inherited proxy geography and saved proxy
selection. Country, two-letter state and city (lowercase without spaces) become
managed residential proxy hints. Longer subdivision codes cannot be represented
by Kernel's two-letter state field and are omitted; country and city remain best
effort. Kernel's browser `region` selects infrastructure, not egress geography.
Arbitrary exact IP selection is unavailable. Required conditions still compare
the browser's observed exit IP and independently resolved geography; routing hints
do not prove those conditions passed.

The persona's viewport and other supported browser settings are applied to check
tabs through CDP, independently of Kernel's desktop live-view resolution. The
adapter does not pass arbitrary persona resolutions to Kernel's restricted
desktop viewport API.

Kernel retains the cloud browser's `navigator.platform`; a saved `MacIntel`
override was accepted by CDP but still observed as `Linux x86_64`. The adapter
reports this field as unsupported. Native window bounds also did not match their
saved dimensions and are reported unsupported. Other observed overrides included
the user agent and its metadata, viewport, timezone, hardware concurrency, touch points, color scheme,
motion, contrast, and transparency preferences. Saved persona settings remain
available even when the cloud host cannot apply a field.

## State and lifecycle

| Capability | Implementation |
| --- | --- |
| Cookies | Shared driver restores and exports the isolated browser's cookies |
| `localStorage` | Shared driver restores and exports visited origins |
| `sessionStorage` | Shared driver restores and exports check tabs |
| Live screenshots | Shared CDP screencast and screenshot driver |
| Kernel live view | Retrieved for headful sessions; absent for headless sessions |
| IndexedDB, OPFS, native profile | Transfer unsupported |

Each launch calls `POST /browsers` without a saved Kernel profile or browser pool.
Its session ID and credential-bearing CDP URL belong to that checkout alone. The
watch process, browser-harness, and short driver commands may attach to the same
browser independently. Disconnecting a client leaves the owned browser running;
the inactivity timeout is a backup after all clients disconnect.

After state export, `stop` calls `DELETE /browsers/{session_id}` and checks
`GET /browsers/{session_id}?include_deleted=true` for deletion or absence. It fails
if termination cannot be confirmed. A failed run's browser is deleted using the
same lifecycle. Calling CDP `Browser.close` alone does not delete a Kernel browser.

API transport errors expose a generic diagnostic. API response errors expose the
HTTP status and a bounded machine-readable error code, excluding response messages,
credential values, and connection URLs. Live-view and CDP links are bearer secrets
and must remain in the application's private runtime state.

## Verification

Configuration boundary checks:

```bash
uv run pytest tests/test_kernel_provider.py -q
```

Cloud acceptance requires a real configured key and a fresh empty browser. Verify
creation, independent concurrent watch and browser-harness connections, portable
state export, client disconnect and reconnect, saved screenshots, and confirmed
termination. Successful lifecycle checks alone do not prove authenticated access
to Hacker News, LinkedIn, or X; those require separate checks and saved evidence
through the app's account workflows.

On October 4, 2026, real empty headful stealth sessions passed simultaneous watch,
browser-harness, and driver-export checks, cookie/local/session storage export,
harness disconnect and reconnect, continued ownership after watch disconnect, and
API-confirmed deletion. Browser-harness saved the intended Example Domain tab in
0.40–0.44 seconds with the watch connection still attached; the saved PNGs and live
frames were visually inspected. Authenticated site
checks remain separate acceptance requirements.

## Official API references

- [Live OpenAPI schema](https://api.onkernel.com/spec.json)
- [Create a browser session](https://kernel.sh/docs/api-reference/browsers/create-a-browser-session)
- [Get browser session details](https://kernel.sh/docs/api-reference/browsers/get-browser-session-details)
- [Delete a browser session](https://kernel.sh/docs/api-reference/browsers/delete-a-browser-session-by-id-or-name)
- [Termination and timeouts](https://kernel.sh/docs/browsers/termination)
- [Headless mode](https://kernel.sh/docs/browsers/headless)
- [Proxy configuration](https://kernel.sh/docs/proxies/overview)
- [Stealth mode and default ISP proxy](https://kernel.sh/docs/browsers/bot-detection/stealth)
