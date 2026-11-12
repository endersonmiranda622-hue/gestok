(function(){
const csrf=document.getElementById('csrf').value,msg=document.getElementById('msg'),rows=document.getElementById('rows');
function say(t,err){msg.hidden=false;msg.className='msg '+(err?'err':'ok');msg.textContent=t}
async function api(method,url,body){const r=await fetch(url,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body?JSON.stringify(body):undefined});
 const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||'Erro inesperado');return j}
function link(p){return location.origin+p}
async function load(){const j=await api('GET','/api/users');rows.textContent='';document.getElementById('empty').hidden=j.items.length>0;
 j.items.forEach(u=>{const tr=rows.insertRow();[u.name,u.email,u.role,u.active?'Ativo':'Inativo'].forEach(v=>tr.insertCell().textContent=v);
  const td=tr.insertCell();const b=document.createElement('button');b.className='sm';b.textContent=u.active?'Desativar':'Reativar';
  b.onclick=async()=>{if(!confirm((u.active?'Desativar ':'Reativar ')+u.name+'?'))return;b.disabled=true;
   try{await api('PATCH','/api/users/'+u.id,{active:!u.active});load()}catch(e){say(e.message,1);b.disabled=false}};td.appendChild(b);
  const l=document.createElement('button');l.className='sm';l.textContent='Link de senha';l.onclick=async()=>{try{const j=await api('POST','/api/users/'+u.id+'/reset-link');say('Entregue ao usuário (uso único, 60 min): '+link(j.reset_path))}catch(e){say(e.message,1)}};td.appendChild(l)})}
document.getElementById('new').onsubmit=async ev=>{ev.preventDefault();const btn=ev.target.querySelector('button');btn.disabled=true;
 try{const j=await api('POST','/api/users',{name:n.value,email:e.value,role:r.value});say('Usuário criado. Entregue este link para ele definir a senha (uso único, 60 min): '+link(j.reset_path));ev.target.reset();load()}
 catch(x){say(x.message,1)}btn.disabled=false};
load().catch(x=>say(x.message,1))})();
