# Browserless.io

The adapter starts an isolated browser for each run. Browser Session Autohealer restores
cookies and portable origin storage through its shared CDP driver, exports them
before stopping, and owns the canonical persona history. The adapter does not transfer
a native Chrome profile, IndexedDB, or OPFS. The shared viewer uses CDP screenshots,
screencasts, and input; this adapter does not create a separate vendor live-view URL.

API keys belong in the ignored `.env` file:

```dotenv
BROWSERLESS_API_KEY=...
```

The runtime CDP endpoint is a credential. Cloud endpoints must stay out of public responses, screenshots, and logs.
Transport errors report safe status information without printing request URLs.

## Browserless.io

Example connection settings:

```json
{"region":"sfo","stealth":true,"residential_proxies":true,"proxy_country":"us","session_timeout_ms":120000}
```

`region` accepts `sfo`, `lon`, or `ams`. Stealth and residential proxies default to
enabled. `proxy_country` is optional and requires residential proxies. The session
timeout defaults to 120,000 milliseconds and must fit the account's plan; selecting
a longer timeout does not bypass the vendor's limit.

Required session locations automatically enable residential proxies and replace
the saved proxy geography for that launch. Country routing uses `proxyCountry`;
city and state/province routing use `proxyCity` and `proxyState`, with lowercase
names and spaces removed. Subdivision codes need a resolved full name; unresolved
codes leave country/city routing as the available hint. Changing the requested
country clears any previous state and city. Routing cannot select an arbitrary
exact IP; session requirements still check the observed exit IP and geography.

City and state/province targeting require the provider's Scale plan. The
development account's read-only `/proxy/cities?country=US` request returned HTTP
400 on October 6, 2026 because city routing is unavailable on its plan. Country
routing and fine geography are separate capabilities. For launches requesting
state or city routing, the adapter checks this read-only endpoint first. An
explicit plan rejection removes finer routing while retaining the requested
country and records `fine_location_unavailable` in runtime
`provider_location_diagnostics`. Other API failures surface as safe errors.
Form and candidate evaluation do not make this network request. The requested
session conditions still require the actual observed location to match; the
country fallback does not mark a state/city requirement satisfied. See the provider's
[proxy routing documentation](https://docs.browserless.io/baas/bot-detection/proxies).

Launch opens a standard browser WebSocket and calls `Browserless.reconnect` on an
attached page before disconnecting. Every subsequent command, watcher, and harness
uses the returned endpoint for the same browser. The persisted `/session` API is
not used: it allows one attached client, which does not suit a concurrent watcher
and harness. On stop, the shared driver sends `Browser.close`; the adapter waits
until reconnecting returns HTTP 404 before confirming termination.

The supplied development account returned a maximum of two minutes on October 4,
2026. This is an absolute browser lifetime, including checks and export. Reconnecting
cannot extend it. Runs that exceed the limit fail rather than creating a replacement
browser with missing session state. Other plans can configure a longer timeout.

The default stealth route initially accepted live CDP changes that it subsequently
replaced after navigation. Real empty-browser navigation confirmed that platform,
hardware concurrency, dark color scheme, outer window, and some screen values
were replaced by stealth's fingerprint. The capability report declares these
limits. Requested viewport metrics of 1111×777 were exposed as 1111×778, with pixel
ratio rounding. Timezone, user agent/client hints, language, touch points, reduced
motion, contrast, forced colors, and reduced transparency were retained in tested
navigation. The adapter still attempts the desired CDP settings because plain
Chromium can differ from the default stealth route; it records observed values
without claiming identical fingerprint fidelity.

References: [standard sessions](https://docs.browserless.io/baas/session-management/standard-sessions),
[persisted sessions and their single-client limit](https://docs.browserless.io/baas/session-management/persisting-state),
[proxies](https://docs.browserless.io/baas/bot-detection/proxies),
[closing sessions](https://docs.browserless.io/examples/close-session).
