"""Real session API, provider browser and vendor demo. Requires the running broker.

TWOCAPTCHA_TEST_PROVIDER=local uv run pytest -xs tests/test_twocaptcha_live.py
Configure a provider named '2Captcha <kind>' and a '2Captcha acceptance' persona.
"""
import json
import os
import subprocess
import time
from pathlib import Path

import httpx
from plain.runtime import settings

from app.core.models import Persona, Provider, Run
from app.core.providers import host_browser_invocation


def test_autoconfigured_provider_solves_real_captcha():
    kind = os.environ.get('TWOCAPTCHA_TEST_PROVIDER', 'local')
    persona = Persona.query.get(name='2Captcha acceptance')
    provider = Provider.query.get(name='2Captcha ' + kind, enabled=True)
    headers = {'Authorization': 'Bearer ' + (Path(settings.APP_CONFIG_DIR) / 'api-token').read_text().strip(),
               'Prefer': 'respond-async'}
    with httpx.Client(base_url='http://127.0.0.1:8421', headers=headers, timeout=30) as api:
        response = api.post('/api/sessions', json={
            'require_all': [{'type': 'persona', 'id': str(persona.uid)},
                            {'type': 'provider', 'id': str(provider.uid)}],
            'allow_unhealthy': True, 'timeout': 360, 'lifetime': 600,
            'actor': '2Captcha live acceptance',
        })
        assert response.status_code == 202
        url = response.json()['url']
        try:
            deadline = time.monotonic() + 370
            while True:
                response = api.get(url).json()
                request = response.get('request', response)
                if request['status'] in {'ready', 'failed'}:
                    break
                assert time.monotonic() < deadline, 'Browser did not become ready'
                time.sleep(1)
            assert request['status'] == 'ready', request.get('detail')
            run = Run.query.get(id=request['session_id'])
            assert run.runtime['twocaptcha_status']['verified'] is True
            _, _, env, work = host_browser_invocation('alive', run)
            script = r"""
const fs=require('fs');
const v=JSON.parse(fs.readFileSync(0,'utf8'));
const chrome=require(process.env.ACCOUNT_CHECKER_CHROME_HELPER);
(async()=>{
 const browser=await chrome.connectToBrowserEndpoint(chrome.resolvePuppeteerModule(),v.cdp,{defaultViewport:null});
 try {
  const page=await browser.newPage();
  let stage='navigation';
  try {
  await page.goto('https://2captcha.com/demo/recaptcha-v2',{waitUntil:'domcontentloaded'});
  // Only the vendor extension may solve. The test waits, then submits the vendor demo.
  stage='extension solve';
  await page.waitForFunction(()=>Boolean(document.querySelector('[name="g-recaptcha-response"]')?.value),{timeout:120000});
  stage='demo verification';
  await page.click('button[type="submit"]');
  await page.waitForFunction(()=>document.body.innerText.includes('Captcha is passed successfully'),{timeout:20000});
  console.log(JSON.stringify({solved:true,verified_by_site:true}));
  } catch(e) {
   console.error(JSON.stringify({stage,error:e.name,text:await page.evaluate(()=>document.body.innerText)}));
   throw e;
  } finally {await page.screenshot({path:v.screenshot,type:'jpeg'});}
 } finally {await browser.disconnect();}
})().catch(e=>{console.error(e.name);process.exitCode=1});
"""
            result = subprocess.run(['node', '-e', script],
                input=json.dumps({'cdp': request['cdp_url'], 'screenshot': str(work / 'twocaptcha-solved.jpg')}),
                env=env, text=True, capture_output=True, timeout=170, check=False)
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout) == {'solved': True, 'verified_by_site': True}
            assert (work / 'twocaptcha-solved.jpg').read_bytes().startswith(b'\xff\xd8\xff')
            print(json.dumps({'provider': kind, 'run': run.id, 'solved': True}))
        finally:
            response = api.post(url, json={'success': False, 'message': '2Captcha acceptance complete; discard test state'})
            assert response.status_code in (200, 202)
            deadline = time.monotonic() + 40
            while True:
                response = api.get(url).json()
                request = response.get('request', response)
                if request['status'] in ('failed', 'closed', 'cancelled'):
                    break
                assert time.monotonic() < deadline, 'Provider cleanup did not complete'
                time.sleep(1)
