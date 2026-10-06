# Browserless.io and ZenRows

Both adapters start an isolated browser for each run. Browser Session Autohealer restores
cookies and portable origin storage through its shared CDP driver, exports them
before stopping, and owns the canonical persona history. Neither adapter transfers
a native Chrome profile, IndexedDB, or OPFS. The shared viewer uses CDP screenshots,
screencasts, and input; these adapters do not create a separate vendor live-view URL.

API keys belong in the ignored `.env` file:

```dotenv
BROWSERLESS_API_KEY=...
ZENROWS_API_KEY=...
```

The runtime CDP endpoint is a credential. Cloud endpoints and the ZenRows keeper's
local connection token must stay out of public responses, screenshots, and logs.
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

## ZenRows

Example connection settings:

```json
{"proxy_country":"us","session_ttl_minutes":15}
```

`proxy_country` accepts a two-letter country code. Alternatively, use `proxy_region`
with `eu`, `na`, `ap`, `sa`, `af`, or `me`. Do not configure both. TTL defaults to
15 minutes and accepts 1–15. Residential proxy routing is vendor managed.

Required session geography supplies a country hint and clears an inherited
continent (`proxy_region`). Browser Sessions documents no state, city or arbitrary
IP targeting; those conditions remain independently checked against observed
egress. The official CLI's managed REST browser API returns a session ID and
expiration, without a reconnectable CDP endpoint. Its `browser connect` command
constructs the direct launch WebSocket instead. Consequently direct session
handoff remains unavailable: the current adapter needs its connection keeper to
share one owned browser, and no new CDP proxy is introduced for session handoff.

The direct ZenRows endpoint launches a new browser for every WebSocket connection;
it does not supply a reconnectable CDP URL. A run therefore owns one connection
keeper process. Its upstream WebSocket remains connected between app commands.
Each local CDP client receives its own `Target.attachToBrowserTarget` session;
request IDs and target-session ownership are tracked so responses and CDP events
return to the correct client. An authenticated loopback WebSocket exposes this
single browser to the shared driver and harness. `Browser.close` ends the keeper,
closes its upstream, and releases the run's browser. The adapter confirms the keeper
process exited before returning from stop.
If the downstream CDP close fails, stop signals the owned keeper so it closes the
upstream independently. Failed startup also terminates the owned process group,
including failures writing stdin or reading readiness metadata.

ZenRows manages its browser fingerprint. Live tests confirmed that viewport/device
metrics, user agent, platform, and touch overrides were acknowledged but ignored.
The adapter reports viewport, window, screen, mobile, user agent/client hints,
platform, accept-language, and touch settings as unsupported and omits those
overrides. Canonical persona settings remain intact. Live timezone, locale,
hardware-concurrency, and preferred-color-scheme overrides did apply. The locale
override changes `Intl` formatting; navigator language and Accept-Language remain
provider controlled. Storage,
screenshots, and CDP navigation operate in the same isolated browser. A capability
report does not promise an identical cross-provider browser fingerprint.

References: [setup](https://docs.zenrows.com/browser-sessions/setup),
[CDP and fingerprint restrictions](https://docs.zenrows.com/browser-sessions/faq),
[session TTL](https://docs.zenrows.com/browser-sessions/features/session-ttl),
[official CLI browser API source](https://github.com/ZenRows/cli/blob/main/src/core/browser-api.ts).

## Lifecycle acceptance evidence

Empty cloud browsers were tested on October 4, 2026 without importing account
cookies or changing the app database. Real Puppeteer clients navigated to
`https://example.com`, wrote and read localStorage/sessionStorage, connected two
clients to the same target concurrently, disconnected both, then reconnected and
verified that target and storage remained. Browserless's default stealth and
residential-proxy settings passed; ZenRows passed through its connection keeper.
Both browsers returned real page screenshots and screencast frames. Screencast
events stayed on the requesting CDP target session while a second client was
connected. Browserless returned HTTP 404 after
close; ZenRows acknowledged close and its keeper exited. Account access checks are
separate acceptance work and are not established by these lifecycle tests.

The adapters also passed the app's actual shared sequence: launch, empty-cookie
seed, prepare target, concurrent watcher and navigation, environment capture,
portable-state export, watcher disconnect, and verified adapter stop. Restored
example.com localStorage/sessionStorage values were recovered by the shared export.
Additional real failure-path checks confirmed that a rejected ZenRows API key
left no new keeper process, an invalid local keeper token still allowed adapter
stop to tear down its owned browser, and Browserless stop confirmed HTTP 404 for
an already-terminated session whose CDP close could no longer be acknowledged.
