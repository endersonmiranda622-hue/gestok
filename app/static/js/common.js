window.G=(function(){const csrf=document.querySelector('meta[name=csrf]').content,msg=document.getElementById('msg');
function say(t,err){msg.hidden=false;msg.className='msg '+(err?'err':'ok');msg.textContent=t}
async function api(m,u,b){const r=await fetch(u,{method:m,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:b?JSON.stringify(b):undefined});
 const j=await r.json().catch(()=>({}));if(!r.ok){const e=new Error(j.error||'Erro inesperado');e.field=j.field;throw e}return j}
function opt(sel,items,label,blank){sel.textContent='';if(blank){const o=document.createElement('option');o.value='';o.textContent=blank;sel.appendChild(o)}
 items.filter(i=>i.active).forEach(i=>{const o=document.createElement('option');o.value=i.id;o.textContent=label(i);sel.appendChild(o)})}
const N=(v,min,max)=>new Intl.NumberFormat('pt-BR',{minimumFractionDigits:min,maximumFractionDigits:max}).format(v);
const F={qty:v=>N(v/1000,0,3),cents:v=>'R$ '+N(v/100,2,2),e4:v=>'R$ '+N(v/10000,2,4),int:v=>v==null?'—':String(v),date:v=>v?new Date(v+'T12:00:00').toLocaleDateString('pt-BR'):'—',datetime:v=>v?new Date(v).toLocaleString('pt-BR'):'—',text:v=>v==null?'':String(v)};
return{say,api,opt,F}})();
