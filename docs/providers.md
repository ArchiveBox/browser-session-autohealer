# Browser providers

Browser Session Autohealer implements six browser adapters. The registry in
`app/core/providers.py` defines runtime choices; cloud and borrowed-browser
implementations live in `app/core/provider_backends/`. The
[manifest](../providers/manifest.json) records implementation and verification
status, rather than controlling runtime behavior.

## Implemented adapters

| Provider | Checkout isolation and termination | Current acceptance evidence |
| --- | --- | --- |
| Local Chrome | Fresh native profile on the host or in Docker; wait for the owned process to exit | Existing local app evidence |
| Browserbase | Fresh cloud session; request release and confirm completion | Existing app evidence, including authenticated X access |
| Generic CDP | Fresh owned browser context on an existing endpoint; dispose only that context | Hacker News, LinkedIn and X passed and checked in successfully |
| Kernel | Fresh cloud browser; delete it and confirm deletion | Hacker News and X passed; LinkedIn passed after an automatic recovery session and fresh verification |
| Anchor Browser | Fresh cloud session; delete it and confirm completion | Hacker News and X passed; LinkedIn access confirmed, but a corrected script error kept that session discarded |
| Browserless.io | Fresh reconnectable cloud browser; close and confirm the endpoint is gone | Hacker News and X passed; LinkedIn access confirmed, but a cookie banner and unconfirmed shutdown kept that session discarded |

These are real authenticated checks from October 4, 2026, using OpenCode and
browser-harness rather than scripted site fixtures. Access and session completion
are separate results: a corrected execution error still prevents adoption.
Browserless shutdown confirmation was subsequently fixed and verified on isolated
real cloud sessions; the earlier failed session remains discarded.

The focused suite (`test_provider_acceptance.py`, `test_generic_cdp.py`,
`test_kernel_provider.py`) checks retained
real evidence, truthful failure/adoption state, canonical settings preservation,
image response types, isolated contexts and concurrent CDP clients. A passing
suite does not turn the recorded LinkedIn challenge into a successful login.

Configuration and lifecycle details:
[local setup](development.md#run-locally), [Kernel](providers-kernel.md),
[Anchor Browser](providers-anchor.md),
[Browserless.io](providers-browserless.md).

For Generic CDP, configure `{"endpoint_env":"CDP_BROWSER_URL"}` and keep the actual
browser endpoint in the ignored `.env` file or process environment. The endpoint
must support isolated contexts that survive client disconnects and concurrent
CDP clients. Launch creates a fresh context with `disposeOnDetach: false`; every
checkout stores its own context ID. Cookies, targets, exports, captures and
screenshots are scoped to that context. Stop disposes the context and confirms
its removal; it never closes the borrowed browser. Real empty-context acceptance
is covered by `uv run pytest tests/test_generic_cdp.py -q`.

## 2Captcha

In **Integrations → 2Captcha**, save the API key and use **Test connection**.
The key is encrypted in private configuration. Leaving the password field blank
preserves it; **Remove saved key** removes the stored value. Environment variables
`TWOCAPTCHA_API_KEY` and `API_KEY_2CAPTCHA` are fallbacks. Keys are never included
in provider extension uploads or run settings.

Local Chrome and Docker enable 2Captcha by default when a key is configured.
Browserbase, Kernel, Anchor, and Browserless default to **Off** because they offer
native solving. Their native settings stay unchanged unless 2Captcha is explicitly
enabled with the provider switch. Scope permissions must also allow it. Retry count,
retry delay, and automatic form submission inherit from shared → persona → site →
check settings. Auto-submit defaults to off. Changes apply to new sessions.

When enabled, the app installs the vendor extension through `abxpkg`, uploads a
cached key-free ZIP where needed, and performs the extension's popup login before
site navigation. This initializes the background API client; storage writes alone
are insufficient. Installation, login, and settings verification failures stop
the session. The session page and session API report the resulting solver status.

| Provider | Explicit 2Captcha setup |
| --- | --- |
| Local Chrome / Docker | Load the unpacked extension into the isolated profile |
| Browserbase | Upload ZIP, set `extensionId`, disable the native solver |
| Kernel | Upload ZIP, set `extensions`, use non-stealth mode to avoid competing solvers; select an explicit proxy if needed |
| Anchor | Upload ZIP, set `browser.extensions`, disable the native solver |
| Browserless | Upload through the extension API and use Chromium; accounts without this API can upload the ZIP in the dashboard and configure `twocaptcha_extension` with its name |
| Generic CDP | Unsupported: a borrowed isolated context cannot configure a browser-wide extension |

Provider extension APIs: [Browserbase](https://docs.browserbase.com/platform/browser/core-features/browser-extensions),
[Kernel](https://kernel.sh/docs/browsers/extensions),
[Anchor](https://docs.anchorbrowser.io/advanced/extensions),
[Browserless](https://docs.browserless.io/baas/features/browser-extensions).
Kernel's [stealth configuration](https://kernel.sh/docs/browsers/bot-detection/stealth)
couples its default proxy and native solver to stealth mode.

`uv run pytest -q tests/test_twocaptcha.py` exercises the running app's settings,
encrypted key handling, provider defaults and scoped checkout settings.
`TWOCAPTCHA_TEST_PROVIDER=local uv run pytest -xs tests/test_twocaptcha_live.py`
uses the real session API and verifies a solve on the vendor demo. It requires a
running broker, a persona named `2Captcha acceptance`, a provider named
`2Captcha <kind>`, and an enabled solver for that provider. Cloud tests consume
provider time and solver credit. Docker Desktop must share the configured data
directory as well as the checkout for Docker sessions.

## Actual adapter interface

Adapters derive from `CDPAdapter`. Browser behavior stays in `browser.cjs` and
`browser_state.cjs`, and access checks use the same browser-harness flow across
providers.

| Member | Responsibility |
| --- | --- |
| `validate_config(config)` | Validate the provider's connection preferences |
| `launch(run)` | Create an isolated owned resource and return its private runtime connection data |
| `stop(run)` | End the owned resource and confirm termination |
| `native_path(run)` | Return the stopped local native profile, or `None` when native transfer is unavailable |
| `live_url(run)` | Return the provider's interactive viewer when available; otherwise use the shared CDP viewer |
| `capabilities` | Declare supported storage and viewing capabilities |
| `settings_support(config)` | Report known supported and unsupported persona settings |
| `browser_invocation(action, run, **payload)` | Supply the shared driver's command, document, environment and work directory |

The worker restores state, runs checks, exports portable state, stops the browser,
and saves a candidate checkpoint. Only a successful, completely finalized
candidate can become the canonical leader. Failed evidence remains available.
Disconnecting a CDP client is distinct from completing that lifecycle.

## Best-effort settings and state transfer

Each run retains the persona's canonical desired settings. The driver applies
supported CDP emulation to the check tab and records unsupported optional
overrides instead of aborting the check. Broken connections and failed storage or
browser operations still surface as errors. Provider reports identify known
restrictions: Kernel cannot reproduce native platform/window values; Anchor's
stealth browser preserves some dimensions and locale settings but supplies its
own browser version, pixel ratio, available screen height, memory and media
preferences. Observed browser environments are retained with check
evidence.

Cookies and visited-origin `localStorage`/`sessionStorage` use the shared CDP
restore/export path. The Local adapter additionally preserves an isolated native
profile. Generic CDP and cloud adapters do not transfer native profiles,
IndexedDB, or OPFS. Export merges visited-origin observations into the canonical
state so unsupported saved fields survive omission; retaining those fields does
not claim they were restored or refreshed in the current browser. Explicit
required storage capabilities remain enforced before a check starts.

Provider stealth presets, emulation and proxy routing do not establish an
identical fingerprint or IP address across providers. Credentials and bearer
connection/viewer URLs belong in the ignored environment or private runtime,
never the public provider configuration or source.

Anchor's tested stealth browser returns JPEG bytes for CDP screenshot requests
specifying PNG, JPEG or WebP, including Puppeteer's explicit PNG request. The app
retains those original bytes and detects the actual image format for evidence
HTTP responses and browser-tool images, even when the tool names the file `.png`.
