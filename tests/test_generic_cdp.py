"""Real empty-context isolation against the configured borrowed CDP browser.

Uses completed application records without changing the database, importing
personal state, or writing over their retained browser evidence.
"""

import json
import os
import subprocess

import pytest

from app.core.models import Provider, Run
from app.core.providers import browser_invocation


def invoke(action, run, work, **payload):
    command, document, env, _ = browser_invocation(action, run, **payload)
    document["work"] = str(work)
    result = subprocess.run(
        command, input=json.dumps(document), capture_output=True, text=True,
        check=False, timeout=90, env=env,
    )
    (work / f"{action}.stderr.log").write_text(result.stderr)
    if result.returncode:
        # The subprocess diagnostic stays private; endpoints never enter an assertion.
        raise RuntimeError("Browser driver rejected the operation")
    return json.loads(result.stdout)


def observe(run, work):
    """Observe real browser ownership and hash cookies without exposing their values."""
    _, document, env, _ = browser_invocation("environment", run)
    script = r"""
const fs=require('node:fs'), crypto=require('node:crypto');
const chrome=require(process.env.ACCOUNT_CHECKER_CHROME_HELPER);
const input=JSON.parse(fs.readFileSync(0,'utf8'));
(async()=>{
  const browser=await chrome.connectToBrowserEndpoint(chrome.resolvePuppeteerModule(),input.cdp,{defaultViewport:null});
  try {
    const cdp=await browser.target().createCDPSession();
    const {browserContextIds}=await cdp.send('Target.getBrowserContexts');
    const {cookies}=await cdp.send('Storage.getCookies');
    cookies.sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b)));
    console.log(JSON.stringify({contexts:browserContextIds,default_cookie_count:cookies.length,
      default_cookie_digest:crypto.createHash('sha256').update(JSON.stringify(cookies)).digest('hex')}));
  } finally {await browser.disconnect()}
})().catch(()=>{process.stderr.write('Browser ownership observation failed\n');process.exit(1)});
"""
    result = subprocess.run(
        ["node", "-e", script], input=json.dumps({"cdp": document["cdp"]}),
        capture_output=True, text=True, timeout=90, check=False, env=env,
    )
    (work / "observe.stderr.log").write_text(result.stderr)
    assert result.returncode == 0, "Real browser ownership observation failed"
    return json.loads(result.stdout)


@pytest.fixture
def contexts(tmp_path):
    provider = Provider.query.get(kind="cdp", enabled=True)
    records = list(Run.query.filter(provider=provider, status__in=["success", "failed"])
                   .order_by("-id")[:2])
    assert len(records) == 2, "Two completed real Generic CDP runs are required"
    endpoint = os.environ[provider.config["endpoint_env"]]
    owned = []
    for index, run in enumerate(records):
        # These are actual application records, with only in-memory runtime changes.
        run.runtime = {
            "provider_kind": "cdp", "provider_config": provider.config,
            "cdp": endpoint, "settings": {},
        }
        work = tmp_path / str(index)
        work.mkdir(mode=0o700)
        owned.append((run, work))
    before = observe(*owned[0])
    created = []
    context_ids = []
    try:
        for run, work in owned:
            run.runtime.update(invoke("create_context", run, work))
            created.append((run, work))
            context_ids.append(run.runtime["browser_context_id"])
        assert owned[0][0].runtime["browser_context_id"] != owned[1][0].runtime["browser_context_id"]
        yield created
    finally:
        for run, work in reversed(created):
            invoke("dispose_context", run, work)
        after = observe(*owned[0])
        assert before["default_cookie_count"] == after["default_cookie_count"]
        assert before["default_cookie_digest"] == after["default_cookie_digest"], (
            "Owned-context lifecycle changed the borrowed browser's default cookies"
        )
        for context_id in context_ids:
            assert context_id not in after["contexts"]


def test_owned_contexts_seed_export_screenshots_and_disposal(contexts):
    state = {"cookies": [], "origins": []}  # Fresh empty session, no imported cookies.
    targets = []
    for (run, work), url in zip(contexts, ("https://example.com", "https://example.org"), strict=True):
        assert invoke("seed", run, work, state=state) == {"cookies": 0}
        target = invoke("prepare", run, work)
        targets.append(target)
        assert invoke("navigate", run, work, target=target["targetId"], url=url)["ok"]
    for (run, work), target, origin in zip(
        contexts, targets, ("https://example.com", "https://example.org"), strict=True,
    ):
        exported = invoke("export", run, work, state=state)
        assert [item["origin"] for item in exported["origins"]] == [origin]
        captured = invoke("capture", run, work, sites=["example.com", "example.org"])
        assert [item["origin"] for item in captured["origins"]] == [origin]
        screenshot = work / "own-page.png"
        assert invoke("screenshot", run, work, target=target["targetId"], screenshot=str(screenshot)) == {
            "screenshot": screenshot.name,
        }
        assert screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert screenshot.stat().st_size > 1000
    first, work = contexts[0]
    invoke("dispose_context", first, work)
    remaining, remaining_work = contexts[1]
    current = observe(remaining, remaining_work)
    assert first.runtime["browser_context_id"] not in current["contexts"]
    assert remaining.runtime["browser_context_id"] in current["contexts"]
    # Remove the already-disposed context from the fixture's cleanup list.
    contexts.pop(0)


@pytest.mark.parametrize("action", ["environment", "navigate", "control", "screenshot", "watch"])
def test_foreign_context_target_is_rejected(contexts, action):
    first, work = contexts[0]
    second, second_work = contexts[1]
    foreign = invoke("prepare", second, second_work)
    # A regression that wrongly accepts the foreign watch must also exit promptly.
    (work / f"watch-{foreign['targetId']}.stop").touch()
    with pytest.raises(RuntimeError, match="Browser driver rejected"):
        invoke(
            action, first, work, target=foreign["targetId"],
            url="https://example.com", operation="select", screenshot=str(work / "foreign.png"),
            state={"cookies": [], "origins": []}, capture=False,
        )
    expected = {
        "screenshot": "The check page is no longer available for a screenshot",
        "watch": "The check browser tab is no longer available",
    }.get(action, "Browser tab unavailable")
    assert (work / f"{action}.stderr.log").read_text().strip() == f"Browser action failed: {expected}"
    assert not (work / "foreign.png").exists()
