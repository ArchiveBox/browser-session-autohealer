// Adapted from abx-plugins/plugins/twocaptcha/on_CrawlSetup__95_twocaptcha_config.js.
// Login initializes the worker's API client; writing chrome.storage alone does not.
function extensionConfig(options, apiKey) {
  const config = {
    apiKey, api_key: apiKey, isPluginEnabled: true,
    repeatOnErrorTimes: options.retry_count, repeatOnErrorDelay: options.retry_delay,
    autoSubmitForms: options.auto_submit, submitFormsDelay: 0,
    recaptchaV2Type: 'token', recaptchaV3MinScore: 0.3, buttonPosition: 'inner',
    useProxy: false, proxy: '', proxytype: 'HTTP', blackListDomain: '',
    autoSubmitRules: [], normalSources: [],
  };
  for (const type of ['Normal', 'RecaptchaV2', 'InvisibleRecaptchaV2', 'RecaptchaV3',
    'Geetest', 'Geetest_v4', 'Keycaptcha', 'Arkoselabs', 'Lemin', 'Yandex',
    'CapyPuzzle', 'Turnstile', 'AmazonWaf', 'MTCaptcha']) {
    config[`enabledFor${type}`] = true;
    config[`autoSolve${type}`] = true;
  }
  config.enabledForRecaptchaAudio = false;
  config.autoSolveRecaptchaAudio = false;
  return config;
}

async function configure(browser, options, apiKey) {
  // Providers assign different IDs to unpacked extensions. Identify the vendor
  // using its manifest and options API, never an assumed Web Store ID.
  const session = await browser.target().createCDPSession();
  const {targetInfos} = await session.send('Target.getTargets');
  const ids = [...new Set(targetInfos.map(t => t.url.match(/^chrome-extension:\/\/([a-p]{32})\//)?.[1]).filter(Boolean))];
  for (const id of ids) {
    const page = await browser.defaultBrowserContext().newPage();
    let matched = false;
    try {
      await page.goto(`chrome-extension://${id}/options/options.html`, {waitUntil: 'domcontentloaded', timeout: 10000});
      const vendor = await page.evaluate(() => {
        const m = chrome.runtime.getManifest();
        return /2captcha/i.test(m.name) || /2captcha\.com/i.test(m.homepage_url || '');
      });
      if (!vendor) continue;
      matched = true;
      await page.waitForFunction(() => typeof Config !== 'undefined', {timeout: 10000});
      if (!options.enabled) {
        const disabled = await page.evaluate(async () => {
          await Config.set({isPluginEnabled: false, apiKey: null, api_key: null});
          return (await chrome.storage.local.get('config')).config.isPluginEnabled === false;
        });
        if (!disabled) throw new Error('2Captcha could not be disabled in the inherited profile');
        return {status: 'disabled', verified: true};
      }
      const cfg = extensionConfig(options, apiKey);
      const result = await page.evaluate((cfg) => new Promise(resolve => {
        const popup = chrome.runtime.connect({name: 'popup'});
        const timeout = setTimeout(() => {
          popup.disconnect(); resolve({error: '2Captcha login timed out'});
        }, 20000);
        popup.onMessage.addListener(async message => {
          if (message.action !== 'login') return;
          clearTimeout(timeout);
          try {
            if (message.error) {
              const code = String(message.error);
              resolve({error: /^ERROR_[A-Z_]+$/.test(code) ? code : '2Captcha login failed'});
              return;
            }
            await Config.set(cfg);
            const saved = (await chrome.storage.local.get('config')).config;
            const verified = saved && Object.entries(cfg).every(([key, value]) => JSON.stringify(saved[key]) === JSON.stringify(value));
            resolve(verified ? {status: 'ready', method: 'popup_login', verified: true} : {error: '2Captcha settings verification failed'});
          } catch { resolve({error: '2Captcha settings could not be saved'}); }
          finally { popup.disconnect(); }
        });
        popup.postMessage({action: 'login', apiKey: cfg.apiKey});
      }), cfg);
      if (result.error) throw new Error(result.error);
      return {...result, extensionId: id};
    } catch (error) {
      if (matched) throw new Error(/^2Captcha|^ERROR_/.test(error.message) ? error.message : '2Captcha options page could not be configured');
      // Other installed extensions need not have a 2Captcha options page.
    } finally { await page.close(); }
  }
  if (!options.enabled) return {status: 'disabled'};
  throw new Error('2Captcha extension was not loaded by this provider');
}

module.exports = {configure, extensionConfig};
