'use strict';
document.querySelector('#login').onclick=async()=>{
 const input=document.querySelector('#token');let token=input.value;input.value='';
 const headers={Authorization:'Bearer '+token};
 const response=await fetch('/admin/sessions',{headers});
 if(!response.ok){document.querySelector('#message').textContent='认证失败或连接方式不受支持';token='';return;}
 const sessions=await response.json();const root=document.querySelector('#sessions');root.replaceChildren();
 for(const session of sessions){
  const row=document.createElement('p');row.textContent='申请 '+session.application_id+' · '+session.status+' · '+(session.failure_kind||'');
  if(['needs_human','submission_uncertain'].includes(session.status)){
   if(session.status==='submission_uncertain')row.append(' · 最终结果不明：仅核对回执，禁止重复提交');
   const button=document.createElement('button');button.textContent='接管此申请（10分钟）';
   button.onclick=async()=>{
    const result=await fetch('/admin/sessions/'+encodeURIComponent(session.id)+'/handover',{method:'POST',headers});
    if(result.ok){const data=await result.json();location.assign(data.panel_path);}
    else document.querySelector('#message').textContent='此任务已变化，请重新查看';
   };row.append(button);
  }root.append(row);
 }
};
