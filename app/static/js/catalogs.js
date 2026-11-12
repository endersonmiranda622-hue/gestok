(function(){const root=document.getElementById('sections'),canCat=root.dataset.cat==='1',canWh=root.dataset.wh==='1';
const defs=[{key:'categories',title:'Categorias',can:canCat,fields:[['name','Nome']]},
 {key:'units',title:'Unidades de medida',can:canCat,fields:[['code','Código (ex.: KG)'],['name','Nome']],frac:true},
 {key:'warehouses',title:'Depósitos',can:canWh,fields:[['name','Nome']]}];
defs.forEach(d=>{const box=document.createElement('section');box.className='panel wide';const h=document.createElement('h2');h.textContent=d.title;box.appendChild(h);
 const tbl=document.createElement('table');tbl.className='tbl';box.appendChild(tbl);
 async function load(){const j=await G.api('GET','/api/'+d.key);tbl.textContent='';if(!j.items.length){const r=tbl.insertRow();r.insertCell().textContent='Nada cadastrado ainda.'}
  j.items.forEach(i=>{const tr=tbl.insertRow();tr.insertCell().textContent=d.key==='units'?i.code+' · '+i.name+(i.allow_fraction?' (aceita fração)':''):i.name;tr.insertCell().textContent=i.active?'Ativo':'Inativo';
   if(d.can){const b=document.createElement('button');b.className='sm';b.textContent=i.active?'Desativar':'Reativar';
    b.onclick=async()=>{if(!confirm((i.active?'Desativar ':'Reativar ')+'"'+(i.name)+'"?'))return;try{await G.api('PATCH','/api/'+d.key+'/'+i.id,{active:!i.active});load()}catch(x){G.say(x.message,1)}};tr.insertCell().appendChild(b)}})}
 if(d.can){const f=document.createElement('form');const ins=[];d.fields.forEach(([k,l])=>{const lab=document.createElement('label');lab.textContent=l+' *';const inp=document.createElement('input');inp.required=true;inp.maxLength=80;lab.appendChild(inp);f.appendChild(lab);ins.push([k,inp])});
  let fr;if(d.frac){const lab=document.createElement('label');fr=document.createElement('input');fr.type='checkbox';lab.appendChild(fr);lab.append(' Aceita quantidades fracionadas');f.appendChild(lab)}
  const sb=document.createElement('button');sb.textContent='Adicionar';f.appendChild(sb);
  f.onsubmit=async ev=>{ev.preventDefault();sb.disabled=true;const b={};ins.forEach(([k,i])=>b[k]=i.value);if(fr)b.allow_fraction=fr.checked;
   try{await G.api('POST','/api/'+d.key,b);f.reset();G.say('Cadastro salvo.');load()}catch(x){G.say(x.message,1)}sb.disabled=false};box.appendChild(f)}
 root.appendChild(box);load().catch(x=>G.say(x.message,1))})})();
