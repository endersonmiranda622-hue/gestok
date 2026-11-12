(function(){const $=id=>document.getElementById(id),CAN=!!$('f');let page=1,editing=null,data=[];
const cell=(tr,t)=>{tr.insertCell().textContent=t??''};
async function load(){const p=new URLSearchParams({q:$('q').value,active:$('act').value,page,size:20});
 try{const j=await G.api('GET','/api/items?'+p);data=j.items;const rows=$('rows');rows.textContent='';$('empty').hidden=j.items.length>0;
 j.items.forEach(i=>{const tr=rows.insertRow();[i.code,i.name,i.item_type==='produto'?'Produto':'Material',i.category_name,i.qty_display+' '+i.unit_code,i.min_qty_display,i.cost_display].forEach(v=>cell(tr,v));
  const td=tr.insertCell();if(CAN){const e=document.createElement('button');e.className='sm';e.textContent='Editar';e.onclick=()=>edit(i);td.appendChild(e);
   const t=document.createElement('button');t.className='sm';t.textContent=i.active?'Desativar':'Reativar';t.onclick=async()=>{if(!confirm((i.active?'Desativar ':'Reativar ')+i.name+'?'))return;
    try{await G.api('PATCH','/api/items/'+i.id,{active:!i.active});load()}catch(x){G.say(x.message,1)}};td.appendChild(t)}});
 $('pg').textContent=' Página '+j.page+' de '+Math.max(1,Math.ceil(j.total/j.size))+' ('+j.total+' itens) ';$('prev').disabled=page<=1;$('next').disabled=page*j.size>=j.total}catch(x){G.say(x.message,1)}}
function edit(i){editing=i.id;$('ft').textContent='Editar '+i.code;$('code').value=i.code;$('code').disabled=true;$('name').value=i.name;$('barcode').value=i.barcode||'';$('type').value=i.item_type;
 $('cat').value=i.category_id||'';$('unit').value=i.unit_id;$('wh').value=i.default_warehouse_id||'';$('min').value=(i.min_qty_milli/1000).toString().replace('.',',');
 $('cost').value=(i.avg_cost_e4/10000).toString().replace('.',',');$('price').value=i.sale_price_e4==null?'':(i.sale_price_e4/10000).toString().replace('.',',');$('desc').value=i.description||'';
 $('lot').checked=!!i.tracks_lot;$('exp').checked=!!i.tracks_expiry;$('cancel').hidden=false;scrollTo(0,document.body.scrollHeight)}
function reset(){editing=null;$('f').reset();$('code').disabled=false;$('ft').textContent='Novo item';$('cancel').hidden=true}
async function init(){if(!CAN){load();return}
 const [c,u,w]=await Promise.all([G.api('GET','/api/categories'),G.api('GET','/api/units'),G.api('GET','/api/warehouses')]);
 G.opt($('cat'),c.items,x=>x.name,'— sem categoria —');G.opt($('unit'),u.items,x=>x.code+' · '+x.name);G.opt($('wh'),w.items,x=>x.name,'— nenhum —');
 $('cancel').onclick=reset;$('f').onsubmit=async ev=>{ev.preventDefault();$('save').disabled=true;
  const b={name:$('name').value,barcode:$('barcode').value,item_type:$('type').value,category_id:$('cat').value||null,unit_id:$('unit').value,default_warehouse_id:$('wh').value||null,
   min_qty:$('min').value||'0',cost:$('cost').value||'0',sale_price:$('price').value||null,description:$('desc').value,tracks_lot:$('lot').checked,tracks_expiry:$('exp').checked};
  try{if(editing){await G.api('PATCH','/api/items/'+editing,b)}else{b.code=$('code').value;await G.api('POST','/api/items',b)}G.say('Item salvo com sucesso.');reset();load()}
  catch(x){G.say(x.message,1);if(x.field&&$(x.field))$(x.field).focus()}$('save').disabled=false};load()}
$('q').oninput=()=>{page=1;clearTimeout(window._t);window._t=setTimeout(load,300)};$('act').onchange=()=>{page=1;load()};
$('prev').onclick=()=>{page--;load()};$('next').onclick=()=>{page++;load()};init().catch(x=>G.say(x.message,1))})();
