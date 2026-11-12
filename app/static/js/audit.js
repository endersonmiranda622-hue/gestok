(function(){const $=id=>document.getElementById(id);let page=1;
async function load(){const p=new URLSearchParams({page,size:25});if($('ac').value)p.set('action',$('ac').value);if($('af').value)p.set('from',$('af').value);if($('at').value)p.set('to',$('at').value);
 try{const j=await G.api('GET','/api/audit?'+p),r=$('rows');r.textContent='';$('empty').hidden=j.items.length>0;
 j.items.forEach(a=>{const tr=r.insertRow();[new Date(a.created_at).toLocaleString('pt-BR'),a.user_name||'—',a.action,(a.entity||'')+(a.entity_id?' '+String(a.entity_id).slice(0,12):''),a.details?JSON.stringify(a.details):''].forEach(v=>tr.insertCell().textContent=v)});
 $('pg').textContent=' Página '+j.page+' de '+Math.max(1,Math.ceil(j.total/j.size))+' ';$('prev').disabled=page<=1;$('next').disabled=page*j.size>=j.total}catch(x){G.say(x.message,1)}}
$('go').onclick=()=>{page=1;load()};$('prev').onclick=()=>{page--;load()};$('next').onclick=()=>{page++;load()};load()})();
