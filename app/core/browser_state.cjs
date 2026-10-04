// Provider-neutral observations; no site checks, selectors or account decisions.
async function environment(page) {
  return page.evaluate(async () => ({
    userAgent: navigator.userAgent, platform: navigator.platform,
    userAgentMetadata: navigator.userAgentData ? await navigator.userAgentData.getHighEntropyValues(['architecture','bitness','model','platformVersion','fullVersionList','wow64']) : undefined,
    locale: navigator.language, languages: [...navigator.languages], acceptLanguage: navigator.languages.join(','),
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    viewport: {width:innerWidth,height:innerHeight,deviceScaleFactor:devicePixelRatio},
    screen: {width:screen.width,height:screen.height,availWidth:screen.availWidth,availHeight:screen.availHeight,colorDepth:screen.colorDepth,pixelDepth:screen.pixelDepth,orientation:screen.orientation?.type},
    window: {width:outerWidth,height:outerHeight},
    mobile: navigator.userAgentData?.mobile || false, maxTouchPoints:navigator.maxTouchPoints,
    hardwareConcurrency:navigator.hardwareConcurrency, deviceMemory:navigator.deviceMemory,
    zoom:visualViewport?.scale || 1,
    colorScheme:matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light',
    reducedMotion:matchMedia('(prefers-reduced-motion:reduce)').matches?'reduce':'no-preference',
    contrast:matchMedia('(prefers-contrast:more)').matches?'more':'no-preference',
    forcedColors:matchMedia('(forced-colors:active)').matches?'active':'none',
    reducedTransparency:matchMedia('(prefers-reduced-transparency:reduce)').matches?'reduce':'no-preference',
  }));
}
const matches = (host, sites) => sites === null || sites.some(s => host.replace(/^\./,'') === s || host.replace(/^\./,'').endsWith('.'+s));
function allowed(url, sites) {try {const u=new URL(url);return ['http:','https:'].includes(u.protocol)&&matches(u.hostname,sites)}catch{return false}}
async function capture(browser, sites, browserContextId) {
  const cdp=await browser.target().createCDPSession();
  const targets=browser.targets().filter(t=>t.type()==='page'&&allowed(t.url(),sites)
    && (!browserContextId || t.browserContext().id===browserContextId));
  if(!targets.length) throw Error('Open a selected site in the source browser before importing');
  const contexts=new Set();
  for(const target of targets) {
    const {targetInfo}=await cdp.send('Target.getTargetInfo',{targetId:target._targetId});
    contexts.add(targetInfo.browserContextId || '');
  }
  if(contexts.size!==1) throw Error('Selected sites span multiple browser profiles; choose one source profile');
  const context=[...contexts][0];
  const {cookies}=await cdp.send('Storage.getCookies',context?{browserContextId:context}:{});
  const origins=new Map(); let settings;
  for(const target of targets) {
    const page=await target.page();
    if(!settings) settings=await environment(page);
    for(const frame of page.frames().filter(f=>allowed(f.url(),sites))) {
      const state=await frame.evaluate(()=>({origin:location.origin,localStorage:{...localStorage},sessionStorage:{...sessionStorage}}));
      if(!origins.has(state.origin)) origins.set(state.origin,state);
    }
  }
  return {cookies:cookies.filter(c=>matches(c.domain,sites)&&(!c.partitionKey||allowed(c.partitionKey.topLevelSite,sites))).map(c=>({...c,hostOnly:!c.domain.startsWith('.')})), settings, origins:[...origins.values()],siteScope:sites};
}
module.exports={environment,capture};
