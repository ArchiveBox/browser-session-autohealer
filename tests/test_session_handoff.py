"""Live acceptance: requires the app, broker, and an enabled real HN task.

SESSION_TEST_PROVIDER=browserbase uv run pytest tests/test_session_handoff.py -q -s
No browser, task result, provider response, or database record is substituted.
"""
import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
from plain.runtime import settings

from app.core.models import Check, CheckRun, IPUsage, Run, SessionRequest
from app.core.providers import host_browser_invocation


def test_real_direct_session_and_checkin():
    provider = os.environ.get('SESSION_TEST_PROVIDER', 'cdp')
    task = Check.query.filter(provider__kind=provider, account__site__domain='news.ycombinator.com',
        mode='check', enabled=True).first()
    assert task, 'Configure a real Hacker News task for SESSION_TEST_PROVIDER'
    token = (Path(settings.APP_CONFIG_DIR) / 'api-token').read_text().strip()
    with httpx.Client(base_url='http://127.0.0.1:8421', headers={'Authorization': 'Bearer ' + token}, timeout=30) as api:
        body = {'require_all': [{'type': 'persona', 'id': str(task.account.persona.uid)},
            {'type': 'provider', 'id': str(task.provider.uid)},
            {'type': 'task', 'site': task.account.site.domain, 'tasks': [str(task.uid)]}],
            'recheck': True, 'timeout': 300, 'lifetime': 120, 'actor': 'API acceptance'}
        key = str(uuid4())
        response = api.post('/api/sessions', json=body, headers={'Prefer': 'respond-async', 'Idempotency-Key': key})
        assert response.status_code == 202, response.text
        request = response.json()
        url = request['url']
        repeat = api.post('/api/sessions', json=body, headers={'Prefer': 'respond-async', 'Idempotency-Key': key})
        assert repeat.json()['id'] == request['id']
        changed = api.post('/api/sessions', json={**body, 'lifetime': 90}, headers={'Idempotency-Key': key})
        assert changed.status_code == 400
        try:
            deadline = time.monotonic() + 320
            while request['status'] not in {'ready', 'failed', 'closed', 'cancelled'} and time.monotonic() < deadline:
                time.sleep(2)
                response = api.get(url)
                data = response.json()
                request = data.get('request', data)
            assert request['status'] == 'ready', {k: v for k, v in request.items() if k != 'cdp_url'}
            assert request['detail']['fresh'] and request['detail']['verified']
            assert request['results'] and all(r['status'] == 'success' for r in request['results'])
            run = Run.query.get(id=request['session_id'])
            assert request['cdp_url'] == run.runtime['cdp']
            assert request['browser_context_id'] == run.runtime.get('browser_context_id')
            # A separate application connects to the returned provider URL, not an app proxy.
            script = """
const fs = require('node:fs');
const v = JSON.parse(fs.readFileSync(0, 'utf8'));
const chrome = require(v.helper);
(async () => {
 const browser = await chrome.connectToBrowserEndpoint(chrome.resolvePuppeteerModule(), v.cdp_url, {defaultViewport:null});
 try {
   const target = browser.targets().find(t => t._targetId === v.targets[0].targetId);
   if (!target) throw Error('Prepared target missing');
   if (v.browser_context_id && target.browserContext().id !== v.browser_context_id) throw Error('Wrong context');
   const page = await target.page();
   const settings = await page.evaluate(() => ({language:navigator.language, timezone:Intl.DateTimeFormat().resolvedOptions().timeZone, width:innerWidth}));
   console.log(JSON.stringify({connected:true, settings}));
 } finally { await browser.disconnect(); }
})().catch(e=>{console.error(e.name);process.exitCode=1});
"""
            _, _, env, _ = host_browser_invocation('alive', run)
            document = {**request, 'helper': env['ACCOUNT_CHECKER_CHROME_HELPER']}
            direct = subprocess.run(['node', '-e', script], input=json.dumps(document), text=True, capture_output=True, timeout=30, check=False, env=env)
            assert direct.returncode == 0, direct.stderr
            actual = json.loads(direct.stdout.strip().splitlines()[-1])
            assert actual['connected']
            if provider == 'cdp':
                assert actual['settings']['timezone'] == run.runtime['settings']['timezone']
                assert actual['settings']['width'] == run.runtime['settings']['viewport']['width']
            print(json.dumps({'provider': provider, 'run': run.id, 'request': request['id'], 'direct': actual}))
            # The successful report triggers asynchronous postflight checks and export.
            released = api.post(url, json={'success': True, 'message': 'Finished reading the prepared page'})
            assert released.status_code == 202
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                time.sleep(2)
                request = api.get(url).json()
                if request['status'] == 'closed':
                    break
            assert request['status'] == 'closed'
            assert 'cdp_url' not in request
            final = Run.query.get(id=run.id)
            assert final.checked_in_at and final.finished_at and final.tip
            assert final.promoted, (final.promotion_reason, final.issues)
            assert request['detail']['client_message'] == 'Finished reading the prepared page'
            assert CheckRun.query.filter(run=final, status='success').count() == 2
            assert IPUsage.query.filter(run=final).exists()
            count = IPUsage.query.filter(run=final).count()
            assert api.post(url, json={'success': True}).status_code == 200
            assert IPUsage.query.filter(run=final).count() == count
        finally:
            row = SessionRequest.query.get(uid=request['id'])
            if row.status not in {'failed', 'closed', 'cancelled'}:
                api.post(url, json={})


def test_client_closes_owned_context_without_reporting_success():
    """Automatic check-in must not close the shared CDP browser or adopt unknown work."""
    from app.core import services
    from app.core.providers import browser_command
    task = Check.query.filter(provider__kind='cdp', account__site__domain='news.ycombinator.com', mode='check').first()
    assert task
    before = services.leader_for(task.account.persona).checkpoint.digest
    token = (Path(settings.APP_CONFIG_DIR) / 'api-token').read_text().strip()
    with httpx.Client(base_url='http://127.0.0.1:8421', headers={'Authorization': 'Bearer ' + token}, timeout=30) as api:
        response = api.post('/api/sessions', json={'require_all': [
            {'type': 'persona', 'id': str(task.account.persona.uid)}, {'type': 'provider', 'id': str(task.provider.uid)},
            {'type': 'task', 'site': task.account.site.domain}], 'allow_unhealthy': True, 'timeout': 30})
        assert response.status_code == 200, response.text
        request = response.json()
        assert request['status'] == 'ready' and not request['detail']['verified']
        run = Run.query.get(id=request['session_id'])
        try:
            assert not CheckRun.query.filter(run=run).exists()  # No inference during ordinary provisioning.
            browser_command('dispose_context', run)  # A real consumer closes only its owned context.
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                request = api.get(request['url']).json()
                if request['status'] == 'closed':
                    break
                time.sleep(1)
            assert request['status'] == 'closed'
            final = Run.query.get(id=run.id)
            assert final.checked_in_at and not final.promoted
            assert services.leader_for(task.account.persona).checkpoint.digest == before
            assert browser_command('alive', run)['alive']  # Shared upstream browser survives.
            assert IPUsage.query.filter(run=run).exists()
        finally:
            api.post(request['url'], json={})
