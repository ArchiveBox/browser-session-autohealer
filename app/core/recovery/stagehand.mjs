// Trusted local variable delivery. No site-specific selectors or login recipes.
import { strictActionSchema } from '../stagehand_schema.mjs';
let raw = '';
for await (const chunk of process.stdin) raw += chunk;
const input = JSON.parse(raw);
const {Stagehand} = await import(process.env.ACCOUNT_CHECKER_STAGEHAND_MODULE || '@browserbasehq/stagehand');
const sensitive = [...Object.values(input.knownValues || {}), input.apiKey, input.cdp].filter(Boolean).flatMap(v => [v, encodeURIComponent(v)]).sort((a,b) => b.length-a.length);
const privacy = {inferenceRequests:0, knownSecretMatchesAfterRedaction:0};
const strictSecrets = new Set(Object.entries(input.knownValues || {}).filter(([key]) => !key.startsWith('fact:')).map(([,value]) => value));
const tokenFacts = new Set(Object.entries(input.knownValues || {}).filter(([key,value]) => key.startsWith('fact:') && !strictSecrets.has(value)).map(([,value]) => value));
function secretPattern(value) {
  const escaped = value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(value.length <= 4 && tokenFacts.has(value) ? '(?<![\\w./\\[\\]-])' + escaped + '(?![\\w./\\[\\]-])' : escaped, 'g');
}
function redact(value) {
  if (typeof value === 'string') {
    for (const secret of sensitive) value = value.replace(secretPattern(secret), '[secret redacted]');
    return value;
  }
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k,v]) => [k,redact(v)]));
  return value;
}
const stagehand = new Stagehand({env:'LOCAL', localBrowserLaunchOptions:{cdpUrl:input.cdp},
  model:{modelName:input.model, apiKey:input.apiKey, middleware:{
    specificationVersion:'v2', transformParams: async ({params}) => {
      const prompt = redact(params.prompt);
      privacy.inferenceRequests++;
      const serialized = JSON.stringify(prompt);
      const matches = sensitive.filter(v => v.length >= 4 && secretPattern(v).test(serialized)).length;
      privacy.knownSecretMatchesAfterRedaction += matches;
      if (matches) throw new Error('Known credential remained in the inference request');
      const responseFormat = params.responseFormat?.schema ? {...params.responseFormat, schema:strictActionSchema(params.responseFormat.schema)} : params.responseFormat;
      return {...params, prompt, responseFormat};
    },
  }}, disableAPI:true, serverCache:false, logInferenceToFile:false, disablePino:true,
  logger:()=>{}, verbose:0, selfHeal:false, keepAlive:true});
const emit = result => process.stdout.write('\nACCOUNT_CHECKER_RESULT='+JSON.stringify(redact({...result, privacy}))+'\n');
try {
  await stagehand.init();
  const page = stagehand.context.pages().find(p => p.targetId() === input.target_id);
  if (!page) throw new Error('target unavailable');
  const current = new URL(await page.url()).origin;
  if (!input.origins.includes(current)) { emit({status:'blocked', reason:'Browser is outside approved login origins'}); }
  else if (input.action === 'origin') { emit({origin:current}); }
  else if (input.action === 'select') {
    // Resolve a private value against observed, visible options locally. The
    // inference model cannot choose a DOM node from an opaque variable value.
    const index = await page.evaluate(({selector, value}) => {
      const matches = [...document.querySelectorAll(selector)].map((node, index) => ({node,index}))
        .filter(({node}) => node.getAttribute('role') === 'option' && node.getClientRects().length
          && node.innerText.trim() === value);
      if (matches.length !== 1) throw new Error('Expected exactly one visible matching option');
      return matches[0].index;
    }, {selector:input.selector, value:input.value});
    await page.locator(input.selector).nth(index).click();
    const selected = await page.evaluate(({selector, index}) =>
      document.querySelectorAll(selector)[index]?.getAttribute('aria-selected') === 'true',
      {selector:input.selector, index});
    emit({status:selected ? 'completed' : 'failed', selected});
  }
  else if (input.action === 'capture') {
    const value = await page.evaluate(({selector}) => {
      const nodes = document.querySelectorAll(selector);
      if (nodes.length !== 1) throw new Error('Select exactly one field');
      const node = nodes[0];
      return 'value' in node ? node.value : node.textContent;
    }, {selector:input.selector});
    // Private subprocess envelope consumed only by the local broker. Never
    // returned verbatim to MCP, model prompts, action logs or browser-harness.
    process.stdout.write('\nACCOUNT_CHECKER_RESULT='+JSON.stringify({status:'captured', private_value:value})+'\n');
  }
  else if (input.action === 'link') {
    if (!input.origins.includes(new URL(input.url).origin)) throw new Error('unapproved verification destination');
    await page.goto(input.url);
    emit({status:'opened'});
  } else {
    const result = await stagehand.act(input.instruction, {page, variables:input.variables, timeout:45000});
    emit({status:result.success ? 'completed' : 'failed', message:result.message, actions:result.actions});
  }
} catch (error) {
  // Upstream errors can contain action arguments. Keep those off all log surfaces.
  emit({status:'unavailable', reason:'Stagehand could not complete this browser action', diagnostic:redact(String(error.message)).slice(0,600)});
} finally {
  await stagehand.close();
}
