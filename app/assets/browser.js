const app=document.querySelector('#browser-app');
if(app){
  let ready=app.dataset.ready==='true', target=app.dataset.target;
  const status=document.querySelector('#browser-status'), frame=document.querySelector('#browser-frame');
  async function control(payload){
    const response=await fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json','X-Account-Checker':'browser-control'},body:JSON.stringify({target,...payload})});
    const data=await response.json(); if(!response.ok) throw Error(data.error||'Browser action failed'); return data;
  }
  function perform(payload){control(payload).catch(error=>status.textContent=error.message);}
  document.querySelector('#close-browser').onclick=()=>control({operation:'close'}).then(()=>location.href='/runs/'+app.dataset.run).catch(error=>status.textContent=error.message);
  document.querySelectorAll('.browser-tabs button').forEach(button=>button.onclick=()=>{if(!ready)return; target=button.dataset.target;perform({operation:'select'});});
  document.querySelectorAll('[data-key]').forEach(button=>button.onclick=()=>perform({operation:'key',key:button.dataset.key}));
  const input=document.querySelector('#browser-text');
  if(input) document.querySelector('#send-text').onclick=()=>{perform({operation:'text',text:input.value});input.value='';};
  if(frame){
    function coordinates(e){const rect=frame.getBoundingClientRect();return {x:(e.clientX-rect.left)/rect.width*Number(app.dataset.width),y:(e.clientY-rect.top)/rect.height*Number(app.dataset.height)};}
    frame.onclick=e=>{if(ready) perform({operation:'click',...coordinates(e)});};
    frame.onwheel=e=>{if(!ready)return;e.preventDefault();perform({operation:'scroll',delta:Math.round(e.deltaY),...coordinates(e)});};
  }
  const timer=setInterval(async()=>{
    try{
      const response=await fetch('/runs/'+app.dataset.run+'/browser-status'); if(!response.ok)throw Error('Browser status unavailable');
      const data=await response.json();
      if(!['queued','starting','running','finishing'].includes(data.status)){clearInterval(timer);status.textContent='Browser closed — see check results';return;}
      if(data.ready!==ready||data.tabs.length!==document.querySelectorAll('.browser-tabs button').length){location.reload();return;}
      if(frame)frame.src='/evidence/'+app.dataset.run+'/live.jpg?v='+Date.now();
      const remote=document.querySelector('#remote-browser');if(remote)remote.style.pointerEvents=ready?'auto':'none';
    }catch(error){status.textContent=error.message;}
  },1500);
}
