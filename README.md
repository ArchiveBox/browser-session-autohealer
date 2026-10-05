<div align="center">

<img src="app/assets/favicon.svg" width="56" height="56" alt="Session Tender">

# Session Tender

<sub>ARCHIVEBOX</sub>

### Keep all your browser sessions logged-in and popup-free using AI to auto-heal common problems.

[![Status: early alpha](https://img.shields.io/badge/status-early_alpha-aa1e55?style=flat-square)](#try-it)
[![Seven browser adapters](https://img.shields.io/badge/browsers-7_adapters-334155?style=flat-square)](#shared-personas-across-browser-providers)
[![Built with ArchiveBox](https://img.shields.io/badge/ArchiveBox-ecosystem-aa1e55?style=flat-square)](https://github.com/ArchiveBox/ArchiveBox)
[![Checks and recovery](https://img.shields.io/badge/tasks-checks_%2B_recovery-334155?style=flat-square)](#from-blocked-to-verified)

[Why it exists](#why-browser-sessions-need-maintenance) · [Screenshots](#see-what-your-accounts-can-see) · [Session history](#shared-personas-across-browser-providers) · [Get started](#try-it)

</div>

<img width="100%" alt="Many browser providers all logged into accounts and able to access content." src="https://github.com/user-attachments/assets/6f275900-d768-49af-8fed-f103f52bd215" />

## Why browser sessions need maintenance

Scraping at scale often involves dealing with tricky situations including login links sent to an email, captchas, SMS codes, and annoying promotional and cookie consent banners.

You also have to religiously track your browser fingerprint and IP addresses used and keep them in sync with the right cookies to make sure your accounts don't get rate-limited, shadow-banned, or blocked altogether.

This app sits alongside your scraping tool of choice (anything that uses a chrome-based browser, including ArchiveBox, Webrecorder, playwright, and more), and handles monitoring+fixing your browser profiles and sessions so they are warm and ready to use at all times.

It keeps known-good browser fingerprints in sync with their cookies, LocalStorage, IndexedDB, and more. It also handles auto-fixing logged-out sessions by using AI to fill passwords and auth codes from 1Password, SMS, email, and captcha solving providers (via MCP). As the built-in agent (opencode) learns how to check & fix each site over time, it saves re-usable automation scripts (with `browser-use` & `stagehand`) for every fix so the next time, no LLM or token spend is needed when that situation is encountered.

| Again? | What gets in the way |
| :--- | :--- |
| 🔑 **“Please sign in”** | Expired sessions, new-device challenges, the wrong account selected |
| 📬 **“Check your inbox”** | Email links, SMS codes, authenticator prompts, OTPs |
| 🍪 **“Accept cookies”** | Consent banners covering the content you came for |
| ✨ **“Meet our new feature”** | Product tours, newsletter popups, subscription offers |
| ⏳ **“Try again later”** | Rate limits, CAPTCHAs, access restrictions |

Session Tender monitors accounts continuously and runs known recovery fix scripts when something gets in the way, or uses AI for new situations. It saves screenshot proof when checks pass or fail, and pings you only if human intervention is really needed when an AI is unable to solve the problem (e.g. if an account gets perma-banned or contacting support is needed).

```mermaid
flowchart LR
    A[Check access] --> B{Content available?}
    B -->|Yes| C[Save working session]
    B -->|No| D[Run recovery task]
    D --> E[Check again]
    E -->|Passed| C
    E -->|Still blocked| F[Flag for help]
    C --> G[Ready for collection]
    style C fill:#e3f3ed,stroke:#16836b,color:#145c49
    style D fill:#f8e9ef,stroke:#aa1e55,color:#881844
    style F fill:#fff2d9,stroke:#b78126,color:#785318
```

## Ensure browser sessions are warm and ready for use across any provider

- **Screenshot evidence** shows the feed, login wall, or obstruction each browser encountered.
- **Flexible grouping** organizes accounts by persona, site, task, browser session, or AI session.
- **Plain-English checks** describe what you need, such as being signed in to a particular account and able to read its feed.
- **Access history** records failures, recovery attempts, and subsequent results.
- **Separate checks and fixes** let you monitor content without changing it, while allowing recovery tasks to update the session.
- **Integrates with many browser providers:** Browserbase, Kernel, Anchor Browser, Browserless, Zenrows, local Chrome/Brave, and more...

![Session Tender showing real LinkedIn, Hacker News, and X accounts, with screenshot evidence and Local and Browserbase results](docs/images/accounts.png)


## From blocked to unblocked

<table>
<tr>
<th width="50%">① Access blocked</th>
<th width="50%">② Unblocked</th>
</tr>
<tr>
<td><a href="docs/images/x-blocked.png"><img src="docs/images/x-blocked.png" alt="Real X session 80: login attempt blocked; access not confirmed" width="100%"></a></td>
<td><a href="docs/images/x-verified.png"><img src="docs/images/x-verified.png" alt="Real X session 82: ArchiveBoxApp selected and readable timeline confirmed on Browserbase" width="100%"></a></td>
</tr>
<tr>
<td><strong>Local · session #80</strong><br>X limited the login attempt, so the agent stopped and the failed session was discarded.</td>
<td><strong>Browserbase · session #82</strong><br>After importing a Brave session, the agent selected @ArchiveBoxApp from the signed-in accounts and ran a separate check to confirm the timeline was readable.</td>
</tr>
</table>

- **Versioned tasks** keep prompts, scripts, revisions, runs, and evidence together for both checks and fixes.
- **Recovery rules** connect recognized failures to an appropriate fix and a follow-up check.
- **Credential placeholders** let scoped integrations supply values to browser tools, with best-effort redaction before model calls.
- **Browser and agent visibility** includes live views, screenshot timelines, and embedded OpenCode conversations.

## Shared personas across browser providers

A **persona** is a saved browser identity: its site data, account sessions, and browser preferences.

![Real horizontal lineage: Local and Browserbase sessions fork from the same persona and return successful checkpoints to the canonical track](docs/images/lineage-providers.png)

- **Shared persona collection** for ArchiveBox, abx-dl, and other browser automation tools.
- **Isolated browser copies** for parallel sessions on Local Chrome, Browserbase, Generic CDP, Kernel, Anchor Browser, Browserless.io, and ZenRows.
- **A single current leader** updated by eligible successful sessions, with failed sessions excluded.
- **Branch history** showing providers, task results, screenshots, and cookie additions or removals.
- **Checkpoint comparisons** showing what changed, where it came from, and the associated evidence.
- **Selective imports** for the sites you want to use, with browser preferences applied according to provider and CDP support.

## Try it

**Early alpha · self-hosted**

Requires **uv · Docker · Node.js · OpenCode · an OpenAI API key**.

```bash
cp -n .env.example .env
# Set OPENAI_API_KEY in .env before running bootstrap.
uv sync
npm ci
uv run python bin/bootstrap.py
uv run plain server --bind 127.0.0.1:8421 --workers 1
```

Open **[localhost:8421](http://127.0.0.1:8421)**. In another terminal:

```bash
uv run plain accounts worker
```

[Setup, credentials & configuration →](docs/development.md#run-locally)


---

[Development](docs/development.md) · [Architecture](docs/architecture.md) · [Recovery](docs/login-recovery.md) · [Browser providers](docs/providers.md) · [Screenshot sources](docs/images/README.md)

<sub>Screenshots captured from the running app on October 4, 2026. Details are in the <a href="docs/images/README.md">screenshot sources</a>.</sub>
