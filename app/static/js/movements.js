(function(){const $=id=>document.getElementById(id),CAN=!!$('f'),APPROVE=document.currentScript.dataset.approve==='1';let page=1,key=crypto.randomUUID();
const D=s=>new Date(s).toLocaleString('pt-BR');
async function load(){const p=new URLSearchParams({page,size:20,kind:$('fk').value,from:$('ff').value,to:$('ft').value});
 try{const j=await G.api('GET','/api/movements?'+p),rows=$('rows');rows.textContent='';$('empty').hidden=j.items.length>0;
 j.items.forEach(m=>{const tr=rows.insertRow();const L=m.lines;[D(m.occurred_at),m.kind,L.map(l=>l.item_code).join(', '),L.map(l=>(l.qty_milli<0?'-':'+')+l.qty_display).join(', '),[...new Set(L.map(l=>l.warehouse_name))].join(' → '),m.user_name,m.reason||''].forEach(v=>tr.insertCell().textContent=v);
  const td=tr.insertCell();if(APPROVE&&m.kind!=='estorno'){const b=document.createElement('button');b.className='sm';b.textContent='Estornar';b.onclick=async()=>{const r=prompt('Motivo do estorno (mín. 5 caracteres):');if(!r)return;
   try{await G.api('POST','/api/movements',{kind:'estorno',movement_id:m.id,reason:r,idempotency_key:crypto.randomUUID()});G.say('Estorno registrado.');load()}catch(x){G.say(x.message,1)}};td.appendChild(b)}});
 $('pg').textContent=' Página '+j.page+' de '+Math.max(1,Math.ceil(j.total/j.size))+' ';$('prev').disabled=page<=1;$('next').disabled=page*j.size>=j.total}catch(x){G.say(x.message,1)}}
async function items(){const j=await G.api('GET','/api/items?size=100&q='+encodeURIComponent($('iq').value));G.opt($('item'),j.items,i=>i.code+' · '+i.name+' ('+i.qty_display+' '+i.unit_code+')')}
function mode(){const k=$('kind').value;$('dst').hidden=k!=='transferencia';$('ent').hidden=k!=='entrada';$('adj').hidden=k!=='ajuste';$('whl').textContent=k==='transferencia'?'Depósito de origem *':'Depósito *'}
async function init(){if(CAN){const w=await G.api('GET','/api/warehouses');G.opt($('wh'),w.items,x=>x.name);G.opt($('wh2'),w.items,x=>x.name);await items();mode();
 $('kind').onchange=mode;$('iq').oninput=()=>{clearTimeout(window._t);window._t=setTimeout(items,300)};
 $('f').onsubmit=async ev=>{ev.preventDefault();$('save').disabled=true;const k=$('kind').value;
  const l={item_id:$('item').value,qty:$('qty').value,lot_code:$('lot').value||null};
  if(k==='transferencia'){l.from_warehouse_id=$('wh').value;l.to_warehouse_id=$('wh2').value}else l.warehouse_id=$('wh').value;
  if(k==='entrada'){l.cost=$('cost').value||null;l.expiry_date=$('exp').value||null}if(k==='ajuste')l.direction=$('dir').value;
  try{const m=await G.api('POST','/api/movements',{kind:k,reason:$('reason').value,lines:[l],idempotency_key:key});
   G.say('Movimentação registrada: '+m.kind+' · '+m.lines.map(x=>x.item_code+' '+x.qty_display).join(', ')+' (ID '+m.id.slice(0,8)+')');$('f').reset();mode();key=crypto.randomUUID();items();load()}
  catch(x){G.say(x.message,1)}$('save').disabled=false}}load()}
['fk','ff','ft'].forEach(i=>$(i).onchange=()=>{page=1;load()});$('prev').onclick=()=>{page--;load()};$('next').onclick=()=>{page++;load()};init().catch(x=>G.say(x.message,1))})();
