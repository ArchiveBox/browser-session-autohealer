document.addEventListener('click', async event => {
  const button = event.target.closest('[data-copy]');
  if (!button) return;
  event.preventDefault();
  const source = document.getElementById(button.dataset.copy);
  try { await navigator.clipboard.writeText(source.value ?? source.textContent); button.textContent = 'Copied'; }
  catch { button.textContent = 'Select and copy'; source.select?.(); }
});
(() => {
  const form = document.getElementById('session-builder');
  if (!form) return;
  const data = JSON.parse(document.getElementById('builder-data').textContent), spec = data.initial;
  spec.require_all ||= []; spec.prefer ||= []; spec.provider_options ||= {};
  const sites = [...new Set(data.tasks.map(t => t.site))].sort();
  const $ = id => document.getElementById(id);
  function el(tag, text, attrs={}) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    Object.entries(attrs).forEach(([k,v]) => node.setAttribute(k,v));
    return node;
  }
  function button(text, fn, title=text) {
    const node = el('button',text,{type:'button',class:'btn quiet',title}); node.onclick=fn; return node;
  }
  function field(parent, label, value, change, choices=null, type='text') {
    const wrap = el('label',label), input = el(choices ? 'select' : 'input', '', {'aria-label':label});
    if (choices) choices.forEach(([key,name]) => input.append(el('option',name,{value:key})));
    else input.type=type;
    input.value=value ?? '';
    input.onchange=() => {change(input.value); preview();};
    if (!choices) input.oninput=input.onchange;
    wrap.append(input); parent.append(wrap); return input;
  }
  $('recheck').checked=!!spec.recheck;$('allow-unhealthy').checked=!!spec.allow_unhealthy;
  $('lifetime').value=spec.lifetime||1800;$('actor').value=spec.actor||'UI';
  const timeout=spec.timeout??60;$('wait-mode').value=[0,-1,60].includes(timeout)?String(timeout):'custom';$('timeout').value=timeout>0?timeout:60;
  $('recheck').onchange=()=>{if($('recheck').checked){$('allow-unhealthy').checked=false;if($('wait-mode').value==='0')$('wait-mode').value='60';}preview();};
  $('allow-unhealthy').onchange=()=>{if($('allow-unhealthy').checked)$('recheck').checked=false;preview();};
  ['wait-mode','timeout','lifetime','actor'].forEach(id=>$(id).oninput=preview);
  function jsonObject(id) {
    const value=JSON.parse($(id).value);
    if(!value || Array.isArray(value) || typeof value!=='object') throw new Error('Enter a JSON object.');
    return value;
  }
  function advancedConditions() {
    const value=jsonObject('advanced-conditions');
    if(Object.keys(value).some(k=>!['require_all','prefer'].includes(k)) || !Array.isArray(value.require_all) || !Array.isArray(value.prefer))
      throw new Error('Conditions need require_all and prefer arrays.');
    return value;
  }
  $('provider-options').value=JSON.stringify(spec.provider_options,null,2);
  $('advanced-conditions').value=JSON.stringify({require_all:spec.require_all,prefer:spec.prefer},null,2);
  ['provider-options','advanced-conditions'].forEach(id=>$(id).oninput=preview);
  const newSite=()=>({site:'',age:'',mode:'all',minimum:1,importance:{}});
  let guide = readGuidedConditions(spec);
  let advanced = !guide && (spec.require_all.length>0 || spec.prefer.length>0);
  guide ||= {provider:'',persona:'',country:'',sites:[newSite()]};
  function availableTasks(site) {
    return data.tasks.filter(t=>t.site===site && (!guide.provider||t.provider===guide.provider) && (!guide.persona||t.persona===guide.persona));
  }
  function namesFor(site, saved={}) { return [...new Set([...availableTasks(site).map(t=>t.name),...Object.keys(saved)])].sort(); }
  function guidedConditions() {
    const require_all=[], prefer=[];
    for(const type of ['provider','persona']) if(guide[type]) require_all.push({type,id:guide[type]});
    if(guide.country) require_all.push({type:'ip',country:guide.country});
    for(const s of guide.sites) {
      const task={type:'task',site:s.site,tasks:'*',status:'healthy'};
      if(s.age!=='') task.max_age=Math.round(Number(s.age)*60);
      if(s.mode==='minimum') task.min_passed=Number(s.minimum);
      if(s.mode==='choose') {
        const names=namesFor(s.site,s.importance), role=name=>s.importance[name]||'required';
        task.tasks=names.filter(n=>role(n)==='required');
        const optional=names.filter(n=>role(n)==='preferred');
        if(optional.length) prefer.push({...task,tasks:optional});
      }
      require_all.push(task);
    }
    return {require_all,prefer};
  }
  function readGuidedConditions(conditions) {
    const next={provider:'',persona:'',country:'',sites:[]};
    for(const c of conditions.require_all) {
      if(['provider','persona'].includes(c.type) && Object.keys(c).every(k=>['type','id'].includes(k)) && !next[c.type]) next[c.type]=c.id;
      else if(c.type==='ip' && Object.keys(c).every(k=>['type','country'].includes(k)) && !next.country) next.country=c.country;
      else if(c.type==='task' && Object.keys(c).every(k=>['type','site','tasks','status','max_age','min_passed'].includes(k)) && !next.sites.some(s=>s.site===c.site) && (!c.min_passed||c.tasks==='*')) {
        const names=data.tasks.filter(t=>t.site===c.site).map(t=>t.name);
        if(c.tasks!=='*' && !c.tasks.every(n=>names.includes(n))) return null;
        next.sites.push({site:c.site,age:c.max_age===undefined?'':c.max_age/60,mode:c.min_passed?'minimum':c.tasks==='*'?'all':'choose',minimum:c.min_passed||1,
          importance:Object.fromEntries([...new Set(names)].map(n=>[n,c.tasks==='*'||c.tasks.includes(n)?'required':'ignore']))});
      } else return null;
    }
    for(const c of conditions.prefer) {
      const s=next.sites.find(s=>s.site===c.site);
      if(c.type!=='task'||!s||s.mode!=='choose'||!Array.isArray(c.tasks)||c.min_passed||c.max_age!==(s.age===''?undefined:s.age*60)) return null;
      for(const name of c.tasks) {
        if(!(name in s.importance)||s.importance[name]==='required') return null;
        s.importance[name]='preferred';
      }
    }
    if(!next.sites.length) return null;
    return next;
  }
  function drawBrowser() {
    const parent=$('browser-fields');parent.replaceChildren();
    const providers=field(parent,'Provider',guide.provider,v=>{guide.provider=v;drawSites();},[['','Any provider'],...data.providers.map(p=>[p.id,p.name])]);
    for(const option of providers.options) {const p=data.providers.find(p=>p.id===option.value);if(p?.unavailable){option.disabled=true;option.title=p.unavailable;option.textContent+=' · unavailable';}}
    field(parent,'Persona',guide.persona,v=>{guide.persona=v;drawSites();},[['','Any persona'],...data.personas.map(p=>[p.id,p.name])]);
    const regions=new Intl.DisplayNames(['en'],{type:'region',fallback:'none'}), countries=[];
    const countryCodes='AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW';
    for(const code of countryCodes.split(' ')) countries.push([code,regions.of(code)]);
    countries.sort((a,b)=>a[1].localeCompare(b[1]));
    field(parent,'IP country',guide.country,v=>{guide.country=v;},[['','Any country'],...countries]);
  }
  function drawSites() {
    const parent=$('site-requirements');parent.replaceChildren();
    guide.sites.forEach((s,index)=>{
      const section=el('section','',{class:'site-requirement'}), head=el('div','',{class:'site-requirement-heading condition-fields'});
      parent.append(section);section.append(head);
      if(s.site) head.append(el('img','',{src:`https://www.google.com/s2/favicons?domain=${encodeURIComponent(s.site)}&sz=64`,alt:'',width:22,height:22}));
      const others=guide.sites.filter(v=>v!==s).map(v=>v.site);
      field(head,'Site',s.site,v=>{s.site=v;s.importance={};drawSites();},[['','Select a site'],...sites.filter(v=>!others.includes(v)).map(v=>[v,v])]);
      const age=field(head,'Checked within',s.age,v=>{s.age=v;},null,'number');age.min=0;age.step='any';age.placeholder='Schedule';age.parentElement.append(el('small','min'));age.title='Leave blank to use each check’s schedule';
      if(guide.sites.length>1) head.append(button('×',()=>{guide.sites.splice(index,1);drawSites();preview();},'Remove site'));
      if(!s.site) return;
      const names=namesFor(s.site,s.mode==='choose'?s.importance:{}), rule=el('div','',{class:'site-check-rule condition-fields'});section.append(rule);
      field(rule,'Passing checks',s.mode,v=>{s.mode=v;if(v==='choose')names.forEach(n=>{s.importance[n]||='required';});drawSites();},[['all','All checks'],['minimum','At least…'],['choose','Choose required checks']]);
      if(s.mode==='minimum') {
        const minimum=field(rule,'Minimum',s.minimum,v=>{s.minimum=Number(v);},null,'number');minimum.min=1;minimum.max=names.length;minimum.step=1;
        minimum.parentElement.append(el('small',`of ${names.length} checks`));
      }
      const table=el('div','',{class:'request-checks'});section.append(table);
      for(const name of names) {
        const t=availableTasks(s.site).filter(t=>t.name===name).sort((a,b)=>b.at.localeCompare(a.at))[0]||{status:'',at:''};
        const row=el('div','',{class:'request-check'}), evidence=el(t.result_url?'a':'span','',{class:'request-check-image '+t.status});
        if(t.result_url){evidence.href=t.result_url;evidence.target='_blank';evidence.title='Open last result';}
        evidence.append(t.image?el('img','',{src:t.image,alt:`Last result: ${name}`,loading:'lazy'}):el('span','◷'));
        const title=el('div','',{class:'request-check-name'});title.append(el('strong',name));
        const source=[!guide.persona&&data.personas.find(p=>p.id===t.persona)?.name,!guide.provider&&data.providers.find(p=>p.id===t.provider)?.name].filter(Boolean).join(' · ');
        if(t.at) title.append(el('small',`${source?source+' · ':''}Last ${t.status==='success'?'pass':t.status==='failure'?'failure':'run'} · ${new Date(t.at).toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'})}`));
        if(!t.provider) title.append(el('small','Not configured for this selection.',{class:'error'}));
        row.append(evidence,title);
        if(s.mode==='choose') field(row,'Importance',s.importance[name]||'required',v=>{s.importance[name]=v;},[['required','✓ Required'],['preferred','☆ Preferred'],['ignore','— Ignore']]);
        else row.append(el('span',s.mode==='all'?'✓ Required':'Counts toward minimum',{class:'check-role'}));
        table.append(row);
      }
      if(!names.length) table.append(el('p','No checks configured for this selection.',{class:'error'}));
    });
    $('add-site').disabled=guide.sites.length>=sites.length;
  }
  function renderMode() {
    $('guided-builder').hidden=advanced;$('advanced-builder').hidden=!advanced;
    for(const id of ['guided-builder','advanced-builder']) $(id).querySelectorAll('input,select,textarea').forEach(input=>{input.disabled=$(id).hidden;});
    $('guided-mode').setAttribute('aria-pressed',String(!advanced));$('advanced-mode').setAttribute('aria-pressed',String(advanced));
  }
  $('advanced-mode').onclick=()=>{
    if(advanced)return;
    $('advanced-conditions').value=JSON.stringify(guidedConditions(),null,2);advanced=true;renderMode();preview();
  };
  $('guided-mode').onclick=()=>{
    if(!advanced)return;
    try {
      const parsed=readGuidedConditions(advancedConditions());
      if(!parsed)throw new Error('These conditions need the advanced editor.');
      guide=parsed;advanced=false;drawBrowser();drawSites();renderMode();preview();
    } catch(error) { $('builder-error').textContent=error.message; }
  };
  $('add-site').onclick=()=>{guide.sites.push(newSite());drawSites();preview();};
  $('preview-recheck').onclick=()=>{$('recheck').checked=true;$('recheck').onchange();};
  function summary() {
    const parent=$('session-summary');parent.replaceChildren();
    const name=(kind,id)=>data[kind].find(v=>v.id===id)?.name;
    parent.append(el('strong',name('providers',guide.provider)||'Any provider'),el('small',name('personas',guide.persona)||'Any persona'));
    if(guide.country) parent.append(el('small',new Intl.DisplayNames(['en'],{type:'region'}).of(guide.country)+' IP'));
    for(const s of guide.sites) if(s.site) {
      const item=el('div','',{class:'summary-site'}), names=namesFor(s.site,s.mode==='choose'?s.importance:{});
      const required=names.filter(n=>(s.importance[n]||'required')==='required').length, optional=names.filter(n=>s.importance[n]==='preferred').length;
      item.append(el('strong',s.site),el('span',s.mode==='minimum'?`At least ${s.minimum} of ${names.length} checks`:s.mode==='choose'?`${required} required${optional?` · ${optional} preferred`:''}`:`All ${names.length} check${names.length===1?'':'s'}`),el('small',$('recheck').checked?'Recheck before use':s.age===''?'Within each check’s interval':`Checked within ${s.age} min`));parent.append(item);
    }
  }
  drawBrowser();drawSites();renderMode();
  function documentValue() {
    return {...spec,...(advanced?advancedConditions():guidedConditions()),provider_options:jsonObject('provider-options'),recheck:$('recheck').checked,allow_unhealthy:$('allow-unhealthy').checked,
      timeout:Number($('wait-mode').value==='custom'?$('timeout').value:$('wait-mode').value),lifetime:Number($('lifetime').value),actor:$('actor').value};
  }
  let previewTimer, previewRequest;
  async function availability(value, signal) {
    try {
      const response=await fetch('/sessions/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value),signal});
      if(!response.ok)throw new Error(response.status===400?(await response.json()).error:'Availability could not be loaded.');
      const html=await response.text();
      if(!signal.aborted)$('request-availability').innerHTML=html;
    } catch(error) {if(!signal.aborted)$('request-availability').textContent=error.message;}
  }
  function preview() {
    $('timeout').hidden=$('wait-mode').value!=='custom';
    clearTimeout(previewTimer);previewRequest?.abort();
    let value;
    try { value=documentValue(); } catch(error) {
      $('builder-error').textContent=error.message;$('create-session').disabled=true;$('request-availability').replaceChildren();return false;
    }
    $('request-preview').textContent=JSON.stringify(value,null,2);$('request-document').value=JSON.stringify(value);
    let error=value.recheck&&value.timeout===0?'Recheck needs time to complete.':'';
    if(!advanced) {
      for(const s of guide.sites) {
        const names=namesFor(s.site,s.mode==='choose'?s.importance:{});
        if(!s.site) error='Select a site.';
        else if(!names.length) error='No checks configured for this selection.';
        else if(s.mode==='minimum' && (!Number.isInteger(s.minimum)||s.minimum<1||s.minimum>names.length)) error=`Choose a minimum between 1 and ${names.length}.`;
        else if(s.mode==='choose' && !names.some(n=>(s.importance[n]||'required')==='required')) error='Choose at least one required check.';
      }
      summary();
    }
    $('builder-error').textContent=error;
    $('create-session').disabled=!!error;
    $('preview-recheck').hidden=!!error||value.recheck||value.allow_unhealthy;
    if(error) $('request-availability').replaceChildren();
    else {
      $('request-availability').textContent='Checking availability…';
      previewRequest=new AbortController();
      const signal=previewRequest.signal;
      previewTimer=setTimeout(()=>availability(value,signal),250);
    }
    return !error;
  }
  form.addEventListener('submit',event=>{if(!preview())event.preventDefault();});preview();
})();
