(function(){const $=id=>document.getElementById(id),F=G.F,NS='http://www.w3.org/2000/svg';
function kpi(box,label,val,sub){const d=document.createElement('div');d.className='kpi';const s=document.createElement('span');s.textContent=label;const b=document.createElement('b');b.textContent=val;d.append(s,b);
 if(sub){const t=document.createElement('small');t.textContent=sub;d.appendChild(t)}box.appendChild(d)}
function el(n,a,t){const e=document.createElementNS(NS,n);for(const k in a)e.setAttribute(k,a[k]);if(t!=null)e.textContent=t;return e}
function bars(box,series){box.textContent='';const W=720,H=240,P=40,mx=Math.max(1,...series.flatMap(s=>[s.entradas_cents,s.saidas_cents]));
 if(!series.some(s=>s.entradas_cents||s.saidas_cents)){box.textContent='Sem entradas ou saídas no período.';return}
 const svg=el('svg',{viewBox:`0 0 ${W} ${H}`,role:'img','aria-label':'Entradas e saídas por período',class:'svg'}),bw=(W-P)/series.length;
 svg.appendChild(el('line',{x1:P,y1:H-P,x2:W,y2:H-P,stroke:'#9aa7b5'}));svg.appendChild(el('text',{x:2,y:14,'font-size':10},F.cents(mx)));
 series.forEach((s,i)=>{[['entradas_cents','#2f9e63'],['saidas_cents','#d9534f']].forEach(([k,c],j)=>{const h=(H-P-16)*s[k]/mx,r=el('rect',{x:P+i*bw+j*bw*.4+bw*.1,y:H-P-h,width:Math.max(1,bw*.35),height:h,fill:c});r.appendChild(el('title',{},s.bucket+': '+(k==='entradas_cents'?'Entradas ':'Saídas ')+F.cents(s[k])));svg.appendChild(r)});
  if(series.length<=16||i%Math.ceil(series.length/12)===0)svg.appendChild(el('text',{x:P+i*bw,y:H-P+14,'font-size':9},s.bucket.slice(series[0].bucket.length>7?5:0)))});
 svg.appendChild(el('text',{x:W-170,y:14,'font-size':11,fill:'#2f9e63'},'■ Entradas'));svg.appendChild(el('text',{x:W-90,y:14,'font-size':11,fill:'#d9534f'},'■ Saídas'));box.appendChild(svg)}
function hbars(box,rows){box.textContent='';const t=rows.filter(r=>r.value_cents>0);if(!t.length){box.textContent='Sem saldo valorizado.';return}const mx=t[0].value_cents,H=t.length*28+10;
 const svg=el('svg',{viewBox:`0 0 720 ${H}`,role:'img','aria-label':'Valor por categoria',class:'svg'});
 t.forEach((r,i)=>{svg.appendChild(el('text',{x:0,y:i*28+18,'font-size':12},r.name));const w=380*r.value_cents/mx;svg.appendChild(el('rect',{x:170,y:i*28+4,width:Math.max(2,w),height:18,fill:'#2f80ed'}));svg.appendChild(el('text',{x:176+w,y:i*28+18,'font-size':12},F.cents(r.value_cents)))});box.appendChild(svg)}
async function load(){const q=new URLSearchParams();if($('df').value)q.set('from',$('df').value);if($('dt').value)q.set('to',$('dt').value);
 try{const d=await G.api('GET','/api/dashboard?'+q);$('df').value=d.period.from;$('dt').value=d.period.to;$('start').hidden=!d.empty;const c=d.cards,p=d.period_metrics;
 $('cards').textContent='';$('pcards').textContent='';
 kpi($('cards'),'Valor total em estoque',c.total_value_display);kpi($('cards'),'Itens cadastrados',c.items_total,c.items_active+' ativos');kpi($('cards'),'Alertas de reposição',c.replenish);kpi($('cards'),'Itens sem estoque',c.zero);
 kpi($('cards'),'Capital parado há '+c.stale_days+'+ dias',c.stale_value_display,c.stale_count+' linhas de saldo');kpi($('cards'),'Validade próxima / vencidos',c.expiring+' / '+c.expired);
 const u=o=>Object.entries(o).map(([k,v])=>F.qty(v)+' '+k).join(' · ')||'—';
 kpi($('pcards'),'Entradas (valor)',p.entradas_display,u(p.entradas_qty_by_unit));kpi($('pcards'),'Saídas (valor)',p.saidas_display,u(p.saidas_qty_by_unit));kpi($('pcards'),'Saldo do período (entradas − saídas)',p.saldo_periodo_display,'ajustes: '+F.cents(p.ajustes_cents));
 $('gran').textContent='('+d.period.granularity+')';bars($('c1'),d.series);hbars($('c2'),d.by_category);
 const e=$('exp');e.textContent='';if(!d.expiring_soon.length)e.textContent='Nenhum lote com validade próxima.';else d.expiring_soon.forEach(x=>{const li=document.createElement('div');li.textContent=x.code+' · '+x.name+' — lote '+x.lot_code+' vence em '+F.date(x.expiry_date)+' ('+x.qty_display+')';e.appendChild(li)});
 const ul=$('defs');ul.textContent='';Object.values(d.definitions).forEach(t=>{const li=document.createElement('li');li.textContent=t;ul.appendChild(li)})}catch(x){G.say(x.message,1)}}
$('ap').onclick=load;load()})();
