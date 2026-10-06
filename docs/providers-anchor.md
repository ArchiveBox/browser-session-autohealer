# Anchor Browser

The `anchor` adapter launches one fresh cloud Chromium session per checkout.
It does not attach an Anchor profile or managed identity. The central persona
collection owns the saved state, and the shared CDP driver restores and exports
cookies and visited-origin local/session storage.

Store `ANCHOR_BROWSER_API_KEY` in the ignored `.env` file or process environment.
The adapter sends it only in the `anchor-api-key` API header. Provider configuration
contains connection preferences, not credentials:

```json
{
  "proxy": true,
  "extra_stealth": true,
  "captcha_solver": false,
  "country_code": "us",
  "headless": false,
  "max_duration": 30,
  "idle_timeout": 5
}
```

These are the defaults. Both timeouts are positive integer **minutes**. The idle
timer starts after the final CDP/live-view connection disconnects; reconnecting
before that timeout reuses the same session. A checkout keeps its owned session
ID and CDP endpoint throughout its work. Extra stealth and CAPTCHA solving require
an active proxy. Disabling the proxy requires explicitly disabling extra stealth;
`country_code` must then be omitted. Unsupported plans or countries fail through
the provider API without a silent change of browser mode.

Required session locations enable the built-in proxy and replace inherited
country, region and city routing for that launch. Country and normalized
subdivision codes are lowercase (for example `us`, `ca`); city names may retain
spaces. Anchor requires a region alongside a city, so a city-only request with no
region supplies the country hint and leaves exact city matching to the independent
observed-IP check. Changing the requested country clears previous granular
geography. These are best-effort routing hints and cannot select an arbitrary
exact IP. See [Anchor proxy localization](https://docs.anchorbrowser.io/advanced/proxy).

Anchor proxy selection does not establish continuity with the originating
browser's IP address. Extra stealth uses Anchor's Chromium build; neither it nor
best-effort CDP settings establish equality with the original Brave/Chrome
fingerprint. Persona settings remain in the canonical collection. The shared
driver applies the CDP settings supported by the running browser.

Provider video recording and browser tracing are disabled. Ad blocking and popup
blocking are also disabled so a check observes the site's actual obstacles.
Headful sessions expose Anchor's interactive live-view URL; headless sessions use
the shared CDP viewer. Connection and live-view URLs grant access to the owned
browser and belong in the private runtime, never source or diagnostic output.

`stop()` sends `DELETE /v1/sessions/{session_id}`, requires its success
acknowledgement, then polls that specific session until `GET` reports
`completed`. An acknowledgement alone does not authorize checkpoint completion.
The adapter provides no native profile path and reports no IndexedDB or OPFS
round-trip support. Remote profiles and provider recordings are not canonical
state-transfer mechanisms.

## Verified empty-session acceptance

On October 4, 2026, a real session with the default proxy/stealth settings returned
HTTP 200 and `running`. The implemented adapter was then exercised without
personal state or any database writes:

- Two simultaneous Puppeteer/CDP connections reached the same `example.com` tab.
- Disconnecting one client preserved the other client's tab and browser state.
- A separate earlier disconnect/reconnect preserved its target and page.
- A real disposable cookie retained Secure, HttpOnly, SameSite=Lax, session-cookie
  semantics and its value through CDP.
- Disposable localStorage and sessionStorage values survived client disconnect.
- CDP viewport emulation produced a 1440 × 1000 viewport; the retained screenshot
  showed the Example Domain page.
- Adapter termination returned only after Anchor reported `completed`; all three
  empty sessions created during these lifecycle checks were explicitly ended.

The screenshot and diagnostics are outside the source checkout in the private
temporary directory `anchor-adapter-deo7fh9r`. This is lifecycle and protocol
evidence; it does not claim authenticated Hacker News, LinkedIn or X acceptance.
Those checks must run through the real application using an authorized persona.

On October 6, 2026, real required-US and preferred-US session requests passed the
API workflow with an authorized HN persona (runs 193 and 195). Each exposed the
owned browser directly to an independent client, observed US egress in that client
and three saved observations, passed HN checks before and after handoff, exported
portable state, promoted the checked-in tip, and confirmed session closure. These
country tests do not establish state/city targeting acceptance.

## API references

- [Start Browser Session](https://docs.anchorbrowser.io/api-reference/browser-sessions/start-browser-session)
  documents `POST /v1/sessions`, configuration and `id`, `cdp_url`, `live_view_url`.
- [Get Browser Session](https://docs.anchorbrowser.io/api-reference/browser-sessions/get-browser-session)
  documents the per-session status and configuration lookup.
- [End Browser Session](https://docs.anchorbrowser.io/api-reference/browser-sessions/end-browser-session)
  documents the owned-session DELETE endpoint.
- [Session Timeout](https://docs.anchorbrowser.io/advanced/session-timeout)
  documents minute units and connection-driven idle termination.
- [Extra Stealth Mode](https://docs.anchorbrowser.io/essentials/stealth)
  documents proxy requirements and the provider's patched Chromium build.

The status strings `running`, DELETE acknowledgement `success`, and terminal
`completed` were verified against the live API rather than inferred from the
documentation's unenumerated status field.
