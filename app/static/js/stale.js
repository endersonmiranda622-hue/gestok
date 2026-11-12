(function(){const $=id=>document.getElementById(id);let page=1;
async function load(){const p=new URLSearchParams({page,size:20,sort:$('so').value});if($('cat').value)p.set('category_id',$('cat').value);if($('wh').value)p.set('warehouse_id',$('wh').value);if($('ty').value)p.set('item_type',$('ty').value);
 try{const j=await G.api('GET','/api/stale?'+p);$('kc').textContent=j.count;$('kv').textContent=j.value_display;$('kd').textContent=j.stale_days+' dias sem movimentação';
 const rows=$('rows');rows.textContent='';$('empty').hidden=j.items.length>0;
 j.items.forEach(i=>{const tr=rows.insertRow();[i.code,i.name,i.category_name||'—',i.warehouse_name,i.qty_display+' '+i.unit_code,i.cost_display,i.value_display,new Date(i.last_movement_at).toLocaleDateString('pt-BR'),i.days_stalled].forEach(v=>tr.insertCell().textContent=v)});
 $('pg').textContent=' Página '+j.page+' de '+Math.max(1,Math.ceil(j.count/j.size))+' ';$('prev').disabled=page<=1;$('next').disabled=page*j.size>=j.count;
 $('nh').textContent=j.no_history.length?j.no_history.map(x=>x.code+' · '+x.name).join(' | '):'Todos os itens ativos têm histórico.'}catch(x){G.say(x.message,1)}}
(async()=>{const [c,w]=await Promise.all([G.api('GET','/api/categories'),G.api('GET','/api/warehouses')]);G.opt($('cat'),c.items,x=>x.name,'Todas as categorias');G.opt($('wh'),w.items,x=>x.name,'Todos os depósitos');
 ['cat','wh','ty','so'].forEach(i=>$(i).onchange=()=>{page=1;load()});$('prev').onclick=()=>{page--;load()};$('next').onclick=()=>{page++;load()};load()})().catch(x=>G.say(x.message,1))})();
