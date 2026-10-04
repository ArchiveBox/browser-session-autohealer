// Trusted local variable delivery. No site-specific selectors or login recipes.
let raw = '';
for await (const chunk of process.stdin) raw += chunk;
const input = JSON.parse(raw);
const {Stagehand} = await import(process.env.ACCOUNT_CHECKER_STAGEHAND_MODULE || '@browserbasehq/stagehand');
const sensitive = [...Object.values(input.knownValues || {}), input.apiKey, input.cdp].filter(Boolean).flatMap(v => [v, encodeURIComponent(v)]).sort((a,b) => b.length-a.length);
const privacy = {inferenceRequests:0, knownSecretMatchesAfterRedaction:0};
function redact(value) {
  if (typeof value === 'string') {
    for (const secret of sensitive) value = value.split(secret).join('[secret redacted]');
    return value;
  }
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k,v]) => [k,redact(v)]));
  return value;
}
// Stagehand 3's nullable action object omits this nested JSON Schema keyword.
// OpenAI structured output requires closed objects, including nullable branches.
function strictActionSchema(value) {
  if (Array.isArray(value)) return value.map(strictActionSchema);
  if (!value || typeof value !== 'object') return value;
  const result = Object.fromEntries(Object.entries(value).map(([k,v]) => [k,strictActionSchema(v)]));
  if (result.type === 'object' && result.properties) result.additionalProperties = false;
  return result;
}
const stagehand = new Stagehand({env:'LOCAL', localBrowserLaunchOptions:{cdpUrl:input.cdp},
  model:{modelName:input.model, apiKey:input.apiKey, middleware:{
    specificationVersion:'v2', transformParams: async ({params}) => {
      const prompt = redact(params.prompt);
      privacy.inferenceRequests++;
      const serialized = JSON.stringify(prompt);
      const matches = sensitive.filter(v => v.length >= 4 && serialized.includes(v)).length;
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
