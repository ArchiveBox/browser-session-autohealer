// Generic lifecycle, persona configuration, and state transfer only.
// All access checks are generated at runtime by the agent using browser-harness.
const fs = require('node:fs');
const path = require('node:path');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const chrome = require(process.env.ACCOUNT_CHECKER_CHROME_HELPER);
const puppeteer = chrome.resolvePuppeteerModule();
const {environment,capture} = require('./browser_state.cjs');

async function connect() {
  return chrome.connectToBrowserEndpoint(puppeteer, input.cdp, {defaultViewport: null});
}

async function configure(page, settings, state) {
  const cdp = await page.createCDPSession();
  if (settings.window) {
    const {windowId}=await cdp.send('Browser.getWindowForTarget',{targetId:page.target()._targetId});
    await cdp.send('Browser.setWindowBounds',{windowId,bounds:{width:settings.window.width,height:settings.window.height}});
  }
  const viewport = settings.viewport || {width: 1440, height: 1000, deviceScaleFactor: 1};
  await cdp.send('Emulation.setDeviceMetricsOverride', {...viewport, mobile: settings.mobile || false,
    ...(settings.screen ? {screenWidth:settings.screen.width,screenHeight:settings.screen.height}: {})});
  if (settings.timezone) await cdp.send('Emulation.setTimezoneOverride', {timezoneId: settings.timezone});
  if (settings.locale) await cdp.send('Emulation.setLocaleOverride', {locale: settings.locale});
  if (settings.userAgent) await cdp.send('Emulation.setUserAgentOverride', {userAgent: settings.userAgent, acceptLanguage: settings.acceptLanguage || settings.locale || 'en-US',
    ...(settings.platform?{platform:settings.platform}:{}), ...(settings.userAgentMetadata?{userAgentMetadata:settings.userAgentMetadata}:{})});
  if (settings.hardwareConcurrency) await cdp.send('Emulation.setHardwareConcurrencyOverride',{hardwareConcurrency:settings.hardwareConcurrency});
  if (settings.maxTouchPoints !== undefined) await cdp.send('Emulation.setTouchEmulationEnabled',{enabled:settings.maxTouchPoints>0,...(settings.maxTouchPoints?{maxTouchPoints:settings.maxTouchPoints}:{})});
  if (settings.geolocation) await cdp.send('Emulation.setGeolocationOverride', settings.geolocation);
  await cdp.send('Emulation.setEmulatedMedia', {features: [
    {name: 'prefers-color-scheme', value: settings.colorScheme || 'light'},
    {name: 'prefers-reduced-motion', value: settings.reducedMotion || 'no-preference'},
    {name: 'prefers-contrast', value: settings.contrast || 'no-preference'},
    {name: 'forced-colors', value: settings.forcedColors || 'none'},
    {name: 'prefers-reduced-transparency', value: settings.reducedTransparency || 'no-preference'},
  ]});
  await page.evaluateOnNewDocument((origins) => {
    const saved = origins.find(o => o.origin === location.origin);
    if (!saved) return;
    // Restore only once in each tab, before the site's JavaScript runs.
    if (!sessionStorage.getItem('__account_checker_restored')) {
      if (saved.sessionStorage) {
        sessionStorage.clear();
        for (const [k,v] of Object.entries(saved.sessionStorage)) sessionStorage.setItem(k,v);
      }
      sessionStorage.setItem('__account_checker_restored', '1');
    }
    if (saved.localStorage && !sessionStorage.getItem('__account_checker_local_restored')) {
      localStorage.clear();
      for (const [k,v] of Object.entries(saved.localStorage)) localStorage.setItem(k,v);
      sessionStorage.setItem('__account_checker_local_restored','1');
    }
  }, state.origins || []);
}

async function main() {
  if (input.action === 'launch') {
    process.env.ACCOUNT_CHECKER_CHROME_BINARY = input.binary;
    process.env.ACCOUNT_CHECKER_CHROME_LOG = path.join(input.work, 'chrome.log');
    const defaults = require(path.join(path.dirname(process.env.ACCOUNT_CHECKER_CHROME_HELPER), 'config.json')).properties;
    const launched = await chrome.ensureChromeSession({
      binary: path.join(__dirname, 'chrome-launch.sh'), outputDir: input.work, extensionsDir: path.join(input.work,'extensions'),
      CHROME_HEADLESS: input.headless !== false, CHROME_IS_LOCAL: true,
      CHROME_USER_AGENT: input.settings.userAgent || '',
      CHROME_ARGS: defaults.CHROME_ARGS.default,
      CHROME_ARGS_EXTRA: input.settings.chromeArgs || [],
      CHROME_RESOLUTION: `${input.settings.viewport?.width || 1440},${input.settings.viewport?.height || 1000}`,
      reuseExisting: false,
    });
    return {cdp: launched.cdpUrl, pid: launched.pid, profile: launched.userDataDir};
  }
  const browser = await connect();
  try {
    if (input.action === 'capture') return await capture(browser, input.sites);
    if (input.action === 'environment') {
      const target=browser.targets().find(t=>t._targetId===input.target);
      if(!target) throw Error('Browser tab unavailable');
      return await environment(await target.page());
    }
    if (input.action === 'seed') {
      const cdp = await browser.target().createCDPSession();
      // Replacing cookies is correct only inside this newly isolated fork.
      await cdp.send('Storage.clearCookies');
      const cookies = (input.state.cookies || []).map(c => {
        const out = {};
        for (const key of ['name','value','domain','path','secure','httpOnly','sameSite','expires','priority','sameParty','sourceScheme','sourcePort','partitionKey']) {
          if (c[key] !== undefined && !(key === 'expires' && c[key] <= 0)) out[key] = c[key];
        }
        if (c.hostOnly) { delete out.domain; out.url = `${c.secure ? 'https' : 'http'}://${c.domain.replace(/^\./,'')}${c.path || '/'}`; }
        return out;
      });
      if (cookies.length) await cdp.send('Storage.setCookies', {cookies});
      return {cookies: cookies.length};
    }
    if (input.action === 'prepare') {
      const page = await browser.newPage();
      return {targetId: page.target()._targetId};
    }
    if (input.action === 'navigate') {
      const target = browser.targets().find(t => t._targetId === input.target);
      if (!target) throw Error('Browser tab unavailable');
      await (await target.page()).goto(input.url, {waitUntil: 'domcontentloaded'});
      return {ok: true};
    }
    if (input.action === 'watch') {
      const target = browser.targets().find(t => t._targetId === input.target);
      if (!target) throw new Error('The check browser tab is no longer available');
      const page = await target.page();
      // Keep this connection alive: emulation and preload scripts belong to it.
      await configure(page, input.settings, input.state);
      const cdp = await page.createCDPSession();
      const live = path.join(input.work, 'live.jpg');
      let frames = 0, ending = false;
      cdp.on('Page.screencastFrame', frame => {
        if (/^https?:/.test(page.url()) && fs.readFileSync(path.join(input.work,'active-target'),'utf8')===input.target) {
          fs.writeFileSync(live + '.'+input.target+'.tmp', Buffer.from(frame.data, 'base64'));
          fs.renameSync(live + '.'+input.target+'.tmp', live);
          fs.writeFileSync(path.join(input.work, 'live.json'), JSON.stringify({frames: ++frames, captured_at: new Date().toISOString(), target: input.target}));
        }
        cdp.send('Page.screencastFrameAck', {sessionId: frame.sessionId}).catch(error => {
          if (!ending) process.stderr.write(`Frame acknowledgement failed: ${error.message}\n`);
        });
      });
      if (input.capture !== false) await cdp.send('Page.startScreencast', {format:'jpeg', quality:80, maxWidth:1600, maxHeight:1200, everyNthFrame:1});
      fs.writeFileSync(path.join(input.work, `watch-${input.target}.ready`), 'ready');
      await new Promise(resolve => {
        const timer = setInterval(() => {
          if (fs.existsSync(path.join(input.work, `watch-${input.target}.stop`))) { clearInterval(timer); resolve(); }
        }, 100);
        browser.once('disconnected', () => { clearInterval(timer); resolve(); });
      });
      ending = true;
      if (browser.connected) await cdp.send('Page.stopScreencast');
      return {frames};
    }
    if (input.action === 'control') {
      const target=browser.targets().find(t=>t._targetId===input.target);
      if (!target) throw Error('Browser tab unavailable');
      const page=await target.page(), cdp=await page.createCDPSession();
      if(input.operation==='select') {
        fs.writeFileSync(path.join(input.work,'active-target'),input.target);
        await page.bringToFront();
        const frame=await page.screenshot({type:'jpeg',quality:80});
        fs.writeFileSync(path.join(input.work,'live.jpg'),frame);
      } else if(input.operation==='click') {
        const params={x:input.x,y:input.y,button:'left',clickCount:1};
        await cdp.send('Input.dispatchMouseEvent',{type:'mousePressed',...params});
        await cdp.send('Input.dispatchMouseEvent',{type:'mouseReleased',...params});
      } else if(input.operation==='scroll') {
        await cdp.send('Input.dispatchMouseEvent',{type:'mouseWheel',x:input.x,y:input.y,deltaX:0,deltaY:input.delta});
      } else if(input.operation==='text') {
        await cdp.send('Input.insertText',{text:input.text});
      } else if(input.operation==='key') {
        const codes={Enter:13,Tab:9,Backspace:8,Escape:27,ArrowDown:40,ArrowUp:38,ArrowLeft:37,ArrowRight:39};
        await cdp.send('Input.dispatchKeyEvent',{type:'keyDown',key:input.key,windowsVirtualKeyCode:codes[input.key]});
        await cdp.send('Input.dispatchKeyEvent',{type:'keyUp',key:input.key,windowsVirtualKeyCode:codes[input.key]});
      } else throw Error('Unknown browser input');
      return {ok:true};
    }
    if (input.action === 'export') {
      const cdp = await browser.target().createCDPSession();
      const {cookies} = await cdp.send('Storage.getCookies');
      const origins = new Map((input.state.origins || []).map(o => [o.origin, o]));
      for (const page of await browser.pages()) {
        if (!/^https?:/.test(page.url())) continue;
        const origin = await page.evaluate(() => {
          const session = {...sessionStorage};
          delete session.__account_checker_restored; delete session.__account_checker_local_restored;
          return {origin: location.origin, localStorage:{...localStorage},sessionStorage:session};
        });
        origins.set(origin.origin, origin);
      }
      return {cookies:cookies.map(c => ({...c,hostOnly:!c.domain.startsWith('.')})),settings:input.settings,origins:[...origins.values()],siteScope:input.state.siteScope};
    }
    if (input.action === 'close') { await browser.close(); return {closed:true}; }
    if (input.action === 'screenshot') {
      const pages = (await browser.pages()).filter(p => /^https?:/.test(p.url()));
      if (!pages.length) throw new Error('No page is open');
      await pages[pages.length-1].screenshot({path:input.screenshot});
      return {screenshot:path.basename(input.screenshot)};
    }
    throw new Error('Unknown browser action');
  } finally { if (browser.connected) await browser.disconnect(); }
}

main().then(result => {process.stdout.write(JSON.stringify(result), () => process.exit(0));})
  .catch(error => {process.stderr.write(`Browser action failed: ${error.message}\n`); process.exit(1);});
