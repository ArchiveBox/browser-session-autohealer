"""Live location acceptance; uses configured accounts and real browser providers.

Run only with the HTTP server and broker already running:
SESSION_TEST_PROVIDER=browserbase SESSION_TEST_COUNTRY=US \
    uv run pytest tests/test_session_locations.py -q -s

No provider, browser, network observation, task result, or DB row is substituted.
"""
import ipaddress
import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from plain.runtime import settings

from app.core import network
from app.core.models import Check, CheckRun, IPUsage, Run, SessionRequest
from app.core.providers import host_browser_invocation
from app.core.session_conditions import evaluate, validate


@pytest.fixture
def location_api():
    token = (Path(settings.APP_CONFIG_DIR) / 'api-token').read_text().strip()
    with httpx.Client(base_url='http://127.0.0.1:8421',
                      headers={'Authorization': 'Bearer ' + token}, timeout=30) as client:
        yield client


def task_and_spec(provider=None):
    provider = provider or os.environ.get('SESSION_TEST_PROVIDER', 'cdp')
    task = Check.query.filter(provider__kind=provider,
        account__site__domain='news.ycombinator.com', mode='check', enabled=True).first()
    assert task, 'Configure an enabled real HN check for SESSION_TEST_PROVIDER'
    country = os.environ.get('SESSION_TEST_COUNTRY', 'US')
    assert len(country) == 2 and country.isupper(), 'Set SESSION_TEST_COUNTRY to an ISO country code'
    options = json.loads(os.environ.get('SESSION_TEST_PROVIDER_OPTIONS', '{}'))
    assert isinstance(options, dict), 'SESSION_TEST_PROVIDER_OPTIONS must be a JSON object'
    return task, country, {
        'require_all': [
            {'type': 'persona', 'id': str(task.account.persona.uid)},
            {'type': 'provider', 'id': str(task.provider.uid)},
            {'type': 'task', 'site': task.account.site.domain, 'tasks': [str(task.uid)]},
        ],
        'recheck': True, 'timeout': 300, 'lifetime': 120, 'provider_options': options,
        'actor': 'Location API acceptance',
    }


def poll(api, url, statuses, timeout=320):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = api.get(url)
        assert response.status_code in {200, 202, 409}
        data = response.json()
        request = data.get('request', data)
        expected = 409 if request['status'] == 'failed' else 200 if request['status'] in {
            'ready', 'closed', 'cancelled'} else 202
        assert response.status_code == expected
        if request['status'] in statuses:
            return request
        time.sleep(2)
    raise AssertionError('Session did not reach ' + ', '.join(sorted(statuses)))


def cancel(api, url):
    data = api.get(url).json()
    row = data.get('request', data)
    if row['status'] not in {'failed', 'closed', 'cancelled'}:
        api.post(url, json={})
        poll(api, url, {'failed', 'closed', 'cancelled'})


def direct_probe(request, run):
    """A separate real client connects directly and probes its own browser context."""
    script = """
const fs = require('node:fs');
const v = JSON.parse(fs.readFileSync(0, 'utf8'));
const chrome = require(v.helper);
(async () => {
 const browser = await chrome.connectToBrowserEndpoint(chrome.resolvePuppeteerModule(), v.cdp_url, {defaultViewport:null});
 try {
   const target = browser.targets().find(t => t._targetId === v.targets[0].targetId);
   if (!target) throw Error('Prepared target missing');
   const context = target.browserContext();
   if (v.browser_context_id && context.id !== v.browser_context_id) throw Error('Wrong context');
   const page = await context.newPage();
   try {
     await page.goto(v.probe_url, {waitUntil:'domcontentloaded', timeout:15000});
     const result = JSON.parse(await page.evaluate(() => document.body.innerText));
     console.log(JSON.stringify({connected:true, ip:result.ip}));
   } finally { await page.close(); }
 } finally { await browser.disconnect(); }
})().catch(e=>{console.error(e.name);process.exitCode=1});
"""
    _, _, env, _ = host_browser_invocation('alive', run)
    document = {**request, 'helper': env['ACCOUNT_CHECKER_CHROME_HELPER'],
        'probe_url': os.environ.get('SESSION_IP_PROBE_URL', 'https://api64.ipify.org?format=json')}
    result = subprocess.run(['node', '-e', script], input=json.dumps(document), text=True,
        capture_output=True, timeout=30, check=False, env=env)
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout.strip().splitlines()[-1])
    assert actual['connected']
    assert ipaddress.ip_address(actual['ip']).is_global
    return actual, network.geolocate(actual['ip'])


@pytest.mark.parametrize('selection', ['required', 'preferred'])
def test_fresh_country_selection_and_direct_handoff(location_api, selection):
    task, country, body = task_and_spec()
    condition = {'type': 'ip', 'country': country}
    if selection == 'required':
        body['require_all'].append(condition)
    else:
        body['prefer'] = [condition]
    response = location_api.post('/api/sessions', json=body,
        headers={'Prefer': 'respond-async'})
    assert response.status_code == 202
    request = response.json()
    url = request['url']
    try:
        request = poll(location_api, url, {'ready', 'failed', 'closed', 'cancelled'})
        assert request['status'] == 'ready', request['detail']
        assert request['detail']['fresh'] and request['detail']['verified']
        assert request['provider_id'] == str(task.provider.uid)
        assert request['results'] and all(r['status'] == 'success' for r in request['results'])
        run = Run.query.get(id=request['session_id'])
        assert request['cdp_url'] == run.runtime['cdp']
        assert CheckRun.query.filter(run=run, check_id=task.id, status='success').count() == 1
        _actual, geo = direct_probe(request, run)
        assert request['ips'] and all(o['source'] == 'browser' for o in request['ips'])
        # A host/upstream browser may be unable to route a preference. Supported
        # cloud providers must route it; every required country must be observed.
        routed = task.provider.kind in {'browserbase', 'anchor', 'browserless', 'kernel'}
        if selection == 'required' or routed:
            assert geo.get('country') == country, 'Direct client browser country did not match'
            assert all(o['geo'].get('country') == country for o in request['ips'])
        released = location_api.post(url, json={'success': True,
            'message': 'Finished direct browser location verification'})
        assert released.status_code == 202
        request = poll(location_api, url, {'closed', 'failed'})
        assert request['status'] == 'closed'
        assert 'cdp_url' not in request
        final = Run.query.get(id=run.id)
        assert final.checked_in_at and final.finished_at and final.tip
        assert final.promoted, (final.promotion_reason, final.issues)
        assert CheckRun.query.filter(run=final, check_id=task.id, status='success').count() == 2
        observations = final.runtime['ip_observations']
        assert len(observations) >= 2
        assert IPUsage.query.filter(run=final).count() == len({o['ip'] for o in observations})
        # Deliberately omit raw IPs, account names, credentials, and bearer endpoints.
        print(json.dumps({'provider': task.provider.kind, 'selection': selection,
            'run': run.id, 'request': request['id'], 'direct_country': geo.get('country'),
            'observations': len(observations)}))
    finally:
        cancel(location_api, url)


def test_failed_latest_probe_invalidates_older_ip_evidence(location_api):
    task, country, body = task_and_spec('cdp')
    body.update(recheck=False, timeout=60, provider_options={})
    body['require_all'][2]['max_age'] = 86400
    body['require_all'].append({'type': 'ip', 'country': country})
    response = location_api.post('/api/sessions', json=body,
        headers={'Prefer': 'respond-async'})
    assert response.status_code == 202
    request = response.json()
    url = request['url']
    try:
        request = poll(location_api, url, {'ready', 'failed', 'closed', 'cancelled'})
        assert request['status'] == 'ready', request['detail']
        assert not request['detail']['fresh'] and request['detail']['verified']
        run = Run.query.get(id=request['session_id'])
        assert not CheckRun.query.filter(run=run).exists()
        assert run.runtime['ip_observations'][-1]['geo']['country'] == country
        spec = validate(body)
        assert evaluate(spec, task.account.persona, task.provider, run=run)[0]
        before = len(run.runtime['ip_observations'])
        original_url = os.environ.get('SESSION_IP_PROBE_URL')
        try:
            # Chrome attempts a real unreachable endpoint. No observation or
            # driver result is supplied by the test.
            os.environ['SESSION_IP_PROBE_URL'] = 'http://127.0.0.1:1'
            failed_observation = network.observe(run)
            assert failed_observation is None
            failed = Run.query.get(id=run.id)
            assert len(failed.runtime['ip_observations']) == before
            ok, _, unmet, _, _ = evaluate(spec, task.account.persona, task.provider,
                run=failed, observation=failed_observation)
            assert not ok
            assert any(u['condition']['type'] == 'ip' and u['reason'] == 'unknown_ip'
                       for u in unmet)
        finally:
            if original_url is None:
                os.environ.pop('SESSION_IP_PROBE_URL', None)
            else:
                os.environ['SESSION_IP_PROBE_URL'] = original_url
        recovered_observation = network.observe(run)
        assert recovered_observation is not None
        recovered = Run.query.get(id=run.id)
        assert len(recovered.runtime['ip_observations']) == before + 1
        assert evaluate(spec, task.account.persona, task.provider,
            run=recovered, observation=recovered_observation)[0]
        assert location_api.post(url, json={}).status_code == 202
        request = poll(location_api, url, {'closed', 'failed'})
        assert request['status'] == 'closed' and 'cdp_url' not in request
        final = Run.query.get(id=run.id)
        assert final.checked_in_at and final.finished_at and not final.promoted
        assert not CheckRun.query.filter(run=final).exists()
        assert IPUsage.query.filter(run=final).exists()
        print(json.dumps({'provider': 'cdp', 'regression': 'failed_latest_probe',
            'run': run.id, 'request': request['id'], 'closed': True}))
    finally:
        cancel(location_api, url)



def test_nonmatching_ip_blocks_before_account_navigation(location_api):
    _task, _, body = task_and_spec()
    body['require_all'].append({'type': 'ip', 'ip': '192.0.2.1'})
    response = location_api.post('/api/sessions', json=body,
        headers={'Prefer': 'respond-async'})
    assert response.status_code == 202
    request = response.json()
    url = request['url']
    try:
        request = poll(location_api, url, {'ready', 'failed', 'closed', 'cancelled'})
        assert request['status'] == 'failed', request['detail']
        assert request['detail']['code'] == 'conditions_unmet'
        assert 'cdp_url' not in request
        assert request['ips'] and all(o['ip'] != '192.0.2.1' for o in request['ips'])
        assert any(u['reason'] == 'not_matched' for u in request['detail']['unmet'])
        run = Run.query.get(id=request['session_id'])
        assert run.checked_in_at and run.finished_at and not run.promoted
        assert not CheckRun.query.filter(run=run).exists()
        assert IPUsage.query.filter(run=run).count() == len({o['ip'] for o in request['ips']})
        assert SessionRequest.query.get(uid=request['id']).status == 'failed'
    finally:
        cancel(location_api, url)
