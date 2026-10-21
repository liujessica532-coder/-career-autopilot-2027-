'use strict';
const base=location.pathname;const screen=document.querySelector('#screen');const message=document.querySelector('#message');let previousURL;let closed=false;let busy=false;
async function request(path,body){
 const response=await fetch(base+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
 if(!response.ok){let data={};try{data=await response.json();}catch{}message.textContent=data.detail||'会话失效或仍需人工验证';return false;}
 return true;
}
let controls=Promise.resolve();
function control(body){controls=controls.then(()=>request('/control',body));return controls;}
async function frame(){
 if(closed||busy)return;busy=true;
 try{
  const response=await fetch(base+'/screen');
  if(!response.ok){closed=true;message.textContent='接管已结束或过期；请回到首页查看任务状态';return;}
  const next=URL.createObjectURL(await response.blob());screen.src=next;if(previousURL)URL.revokeObjectURL(previousURL);previousURL=next;
 }finally{busy=false;}
}
let dragging=false;let movedAt=0;
function point(event){const r=screen.getBoundingClientRect();return {x:Math.max(0,Math.min(1279,Math.round((event.clientX-r.left)*1280/r.width))),y:Math.max(0,Math.min(899,Math.round((event.clientY-r.top)*900/r.height)))};}
screen.onpointerdown=event=>{event.preventDefault();dragging=true;screen.setPointerCapture(event.pointerId);control({action:'down',...point(event)});};
screen.onpointermove=event=>{if(dragging&&Date.now()-movedAt>45){movedAt=Date.now();control({action:'move',...point(event)});}};
screen.onpointerup=event=>{if(dragging){dragging=false;control({action:'move',...point(event)}).then(()=>control({action:'up'}));}};
screen.onpointercancel=()=>{dragging=false;control({action:'up'});};
screen.addEventListener('wheel',event=>{event.preventDefault();control({action:'scroll',delta:Math.max(-1500,Math.min(1500,Math.round(event.deltaY)))});},{passive:false});
document.querySelector('#send').onclick=()=>{const field=document.querySelector('#text');const text=field.value;field.value='';control({action:'text',text});};
for(const button of document.querySelectorAll('[data-key]'))button.onclick=()=>control({action:'key',key:button.dataset.key});
document.querySelector('#resume').onclick=async()=>{await controls;if(await request('/resume')){message.textContent='已请求核验并继续；只有真实回执通过核验后才会标记已提交';}};
frame();setInterval(frame,900);
