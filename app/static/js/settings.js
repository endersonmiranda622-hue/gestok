(function(){const $=id=>document.getElementById(id),ADMIN=document.currentScript.dataset.admin==='1';
const LBL={'dashboard.view':'Ver painel de controle','items.view':'Consultar materiais e cadastros','items.edit':'Cadastrar e editar materiais','categories.manage':'Gerenciar categorias e unidades','warehouses.manage':'Gerenciar depósitos',
'movements.create':'Registrar entradas, saídas e transferências','movements.view_all':'Ver todas as movimentações','movements.view_own':'Ver as próprias movimentações','adjustments.request':'Solicitar ajustes e estornos',
'adjustments.approve':'Aprovar e executar ajustes e estornos','reports.view':'Relatórios, estoque parado e exportações','audit.view':'Histórico de auditoria','users.manage':'Gerenciar usuários','settings.manage':'Configurações da empresa'};
async function init(){const [c,s]=await Promise.all([G.api('GET','/api/company'),G.api('GET','/api/settings')]);$('cn').value=c.name;$('cd').value=c.document||'';$('ctz').value=c.timezone;
 $('sd').value=s.stale_days;$('ed').value=s.expiry_alert_days;$('ps').value=s.default_page_size;$('neg').checked=!!s.allow_negative_stock;
 $('fc').onsubmit=async e=>{e.preventDefault();try{await G.api('PUT','/api/company',{name:$('cn').value,document:$('cd').value,timezone:$('ctz').value});G.say('Dados da empresa salvos.')}catch(x){G.say(x.message,1)}};
 $('fs').onsubmit=async e=>{e.preventDefault();if($('neg').checked&&!s.allow_negative_stock&&!confirm('Permitir estoque negativo? Saídas acima do saldo passarão a ser aceitas (e auditadas).'))return;
  try{const r=await G.api('PUT','/api/settings',{stale_days:$('sd').value,expiry_alert_days:$('ed').value,default_page_size:$('ps').value,allow_negative_stock:$('neg').checked});Object.assign(s,r);G.say('Regras salvas.')}catch(x){G.say(x.message,1)}};
 if(!ADMIN)return;$('pm').hidden=false;const p=await G.api('GET','/api/permissions'),box=$('pmx');
 ['gestor','colaborador'].forEach(role=>{const f=document.createElement('form');f.className='panel';const h=document.createElement('h3');h.textContent=role==='gestor'?'Gestor':'Colaborador';f.appendChild(h);const boxes={};
  p.catalog.forEach(k=>{const l=document.createElement('label');const i=document.createElement('input');i.type='checkbox';i.checked=p.roles[role].includes(k);boxes[k]=i;l.append(i,' '+(LBL[k]||k));f.appendChild(l)});
  const b=document.createElement('button');b.textContent='Salvar permissões de '+h.textContent.toLowerCase();f.appendChild(b);
  f.onsubmit=async e=>{e.preventDefault();if(!confirm('Salvar as permissões de '+h.textContent+'?'))return;try{await G.api('PUT','/api/permissions/'+role,{permissions:Object.keys(boxes).filter(k=>boxes[k].checked)});G.say('Permissões salvas.')}catch(x){G.say(x.message,1)}};box.appendChild(f)})}
init().catch(x=>G.say(x.message,1))})();
