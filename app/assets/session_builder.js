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
    wrap.append(input); parent.append(wrap); return input;
  }
  function fresh(type) {
    if (type==='require_all'||type==='require_any') return {[type]:[]};
    if (type==='not') return {not:{type:'provider',id:data.providers[0]?.id||''}};
    if (type==='persona'||type==='provider') return {type,id:data[type==='persona'?'personas':'providers'][0]?.id||''};
    if (type==='task') return {type,site:sites[0]||'',tasks:'*',status:'healthy'};
    return {type:'ip',country:'US',source:'session'};
  }
  const kinds=[['persona','◎ Persona'],['provider','▱ Provider'],['task','✓ Site / task'],['ip','◎ IP'],['require_any','Any of…'],['require_all','All of…'],['not','Except…']];
  function controls(parent, items, render) {
    const bar=el('div','',{class:'condition-add'}), select=el('select','',{'aria-label':'Condition type'});
    kinds.forEach(([k,label])=>select.append(el('option',label,{value:k})));
    bar.append(select,button('＋ Add',()=>{items.push(fresh(select.value));render();preview();})); parent.append(bar);
  }
  function row(parent, condition, remove) {
    const box=el('div','',{class:'condition-row'}), body=el('div','',{class:'condition-fields'});
    const kind=condition.type||Object.keys(condition)[0];
    box.append(el('strong',kinds.find(k=>k[0]===kind)?.[1]||kind),body,button('×',remove,'Remove condition'));
    parent.append(box);
    if (!condition.type) {
      const nested=el('div','',{class:'condition-group'});body.append(nested);
      if (kind==='not') {
        const select=field(body,'Exclude',condition.not.type,v=>{condition.not=fresh(v);draw();},kinds.filter(k=>['persona','provider','ip'].includes(k[0])));
        const draw=()=>{nested.replaceChildren();row(nested,condition.not,()=>{condition.not=fresh(select.value);draw();preview();});};draw();
      } else list(nested,condition[kind]);
      return;
    }
    const set=(key,value)=>{if(value==='')delete condition[key];else condition[key]=value;};
    if (kind==='persona'||kind==='provider') {
      const items=kind==='persona'?data.personas:data.providers;
      field(body,kind==='persona'?'Persona':'Provider',condition.id,v=>{condition.id=v;},items.map(p=>[p.id,p.name]));
      if(kind==='provider') {
        const note=el('small','',{class:'provider-availability'});body.append(note);
        const update=()=>{note.textContent=items.find(p=>p.id===condition.id)?.unavailable||'';};
        body.querySelector('select').addEventListener('change',update);update();
      }
      return;
    }
    if (kind==='task') {
      const options=el('div','',{class:'task-choices'});
      function tasks() {
        options.replaceChildren();
        const names=[...new Set(data.tasks.filter(t=>t.site===condition.site).map(t=>t.name))];
        const pick=field(options,'Tasks',condition.tasks==='*'?'*':'selected',v=>{condition.tasks=v==='*'?'*':names.slice(0,1);tasks();},[['*','All tasks'],['selected','Selected tasks']]);
        if(pick.value==='selected') names.forEach(name=>{
          const label=el('label',''), input=el('input','',{type:'checkbox'});input.checked=condition.tasks.includes(name);
          input.onchange=()=>{condition.tasks=input.checked?[...condition.tasks,name]:condition.tasks.filter(t=>t!==name);preview();};
          label.append(input,document.createTextNode(name));options.append(label);
        });
      }
      field(body,'Site',condition.site,v=>{condition.site=v;condition.tasks='*';tasks();},sites.map(s=>[s,s])).parentElement.classList.add('wide-field');
      body.append(options);tasks();
      const age=field(body,'Freshness · minutes',condition.max_age===undefined?'':condition.max_age/60,v=>set('max_age',v===''?'':Math.round(Number(v)*60)),null,'number');
      age.min='0';age.step='any';age.placeholder='Task interval';return;
    }
    field(body,'Observed',condition.source||'session',v=>condition.source=v,[['session','This session'],['last_successful_session','Last successful session']]).parentElement.classList.add('wide-field');
    [['country','Country'],['state','State'],['city','City'],['ip','IP address']].forEach(([key,label])=>field(body,label,condition[key],v=>set(key,key==='country'?v.toUpperCase():v)));
    field(body,'Site',condition.site||'',v=>set('site',v),[['','Any site'],...sites.map(s=>[s,s])]);
    const age=field(body,'Freshness · minutes',condition.max_age===undefined?'':condition.max_age/60,v=>set('max_age',v===''?'':Math.round(Number(v)*60)),null,'number');age.min='0';age.placeholder='Any age';
  }
  function list(parent, items, ordered=false) {
    const draw=()=>{
      parent.replaceChildren();
      items.forEach((condition,i)=>{
        const wrap=el('div','',{class:'condition-item'});parent.append(wrap);
        if(ordered) {wrap.append(el('span',String(i+1),{class:'preference-rank'})); if(i)wrap.append(button('↑',()=>{[items[i-1],items[i]]=[items[i],items[i-1]];draw();preview();},'Move preference up'));}
        row(wrap,condition,()=>{items.splice(i,1);draw();preview();});
      });
      controls(parent,items,draw);
    }; draw();
  }
  list($('required-conditions'),spec.require_all);list($('preferred-conditions'),spec.prefer,true);
  $('recheck').checked=!!spec.recheck;$('allow-unhealthy').checked=!!spec.allow_unhealthy;
  $('lifetime').value=spec.lifetime||1800;$('actor').value=spec.actor||'UI';
  const timeout=spec.timeout??60;$('wait-mode').value=[0,-1,60].includes(timeout)?String(timeout):'custom';$('timeout').value=timeout>0?timeout:60;
  $('recheck').onchange=()=>{if($('recheck').checked){$('allow-unhealthy').checked=false;if($('wait-mode').value==='0')$('wait-mode').value='60';}preview();};
  $('allow-unhealthy').onchange=()=>{if($('allow-unhealthy').checked)$('recheck').checked=false;preview();};
  ['wait-mode','timeout','lifetime','actor'].forEach(id=>$(id).oninput=preview);
  const overrides=Object.entries(spec.provider_options);
  function drawOptions() {
    const parent=$('provider-options');parent.replaceChildren();
    overrides.forEach((entry,i)=>{
      const line=el('div','',{class:'condition-fields'});parent.append(line);
      field(line,'Option',entry[0],v=>entry[0]=v);
      field(line,'Value',typeof entry[1]==='string'?entry[1]:JSON.stringify(entry[1]),v=>{try{entry[1]=JSON.parse(v);}catch{entry[1]=v;}});
      line.append(button('×',()=>{overrides.splice(i,1);drawOptions();preview();},'Remove option'));
    });
    const known=[...new Set(data.providers.flatMap(p=>[...Object.keys(p.config),...Object.keys(p.fields)]))].sort();
    const select=field(parent,'Provider option',known[0]||'',()=>{},known.map(k=>[k,k]));
    parent.append(button('＋ Option',()=>{overrides.push([select.value,'']);drawOptions();preview();}));
  }
  drawOptions();
  function documentValue() {
    const options=Object.create(null);
    for(const [key,value] of overrides){const [root,nested]=key.split('.');if(nested)(options[root]||={})[nested]=value;else options[root]=value;}
    return {...spec,provider_options:options,recheck:$('recheck').checked,allow_unhealthy:$('allow-unhealthy').checked,
      timeout:Number($('wait-mode').value==='custom'?$('timeout').value:$('wait-mode').value),lifetime:Number($('lifetime').value),actor:$('actor').value};
  }
  function preview() {
    $('timeout').hidden=$('wait-mode').value!=='custom';
    const value=documentValue();$('request-preview').textContent=JSON.stringify(value,null,2);$('request-document').value=JSON.stringify(value);
    $('builder-error').textContent=value.recheck&&value.timeout===0?'Recheck needs time to complete.':'';
    $('create-session').disabled=!!$('builder-error').textContent;
  }
  form.addEventListener('submit',preview);preview();
})();
