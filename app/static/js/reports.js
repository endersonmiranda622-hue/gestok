(function(){const $=id=>document.getElementById(id);let page=1;
let SZ=20;function qs(){const p=new URLSearchParams();[['rf','from'],['rt','to'],['cat','category_id'],['wh','warehouse_id'],['ty','item_type']].forEach(([i,k])=>{if($(i).value)p.set(k,$(i).value)});return p}
function exp(f){const p=qs();p.set('format',f);location.href='/api/reports/'+$('rp').value+'/export?'+p}
async function run(){const p=new URLSearchParams({page,size:SZ});[['rf','from'],['rt','to'],['cat','category_id'],['wh','warehouse_id'],['ty','item_type']].forEach(([i,k])=>{if($(i).value)p.set(k,$(i).value)});
 try{const r=await G.api('GET','/api/reports/'+$('rp').value+'?'+p),th=$('th'),rows=$('rows');th.textContent='';rows.textContent='';const h=th.insertRow();r.columns.forEach(c=>{const x=document.createElement('th');x.textContent=c.label;h.appendChild(x)});
 r.rows.forEach(o=>{const tr=rows.insertRow();r.columns.forEach(c=>tr.insertCell().textContent=G.F[c.type](o[c.key]))});$('empty').hidden=r.rows.length>0;
 $('tot').textContent=r.total_rows+' registro(s)'+(r.totals.value_cents!=null?' · Total: '+G.F.cents(r.totals.value_cents):'');
 $('pg').textContent=' Página '+r.page+' de '+Math.max(1,Math.ceil(r.total_rows/r.size))+' ';$('prev').disabled=page<=1;$('next').disabled=page*r.size>=r.total_rows}catch(x){G.say(x.message,1)}}
(async()=>{const [l,c,w,st]=await Promise.all([G.api('GET','/api/reports'),G.api('GET','/api/categories'),G.api('GET','/api/warehouses'),G.api('GET','/api/settings')]);SZ=st.default_page_size||20;['csv','xlsx','pdf'].forEach(f=>$('x'+f).onclick=()=>exp(f));G.opt($('rp'),l.items.map(i=>({id:i.name,name:i.title,active:1})),x=>x.name);
 G.opt($('cat'),c.items,x=>x.name,'Todas as categorias');G.opt($('wh'),w.items,x=>x.name,'Todos os depósitos');$('go').onclick=()=>{page=1;run()};$('prev').onclick=()=>{page--;run()};$('next').onclick=()=>{page++;run()};$('rp').onchange=()=>{page=1;run()};run()})().catch(x=>G.say(x.message,1))})();
