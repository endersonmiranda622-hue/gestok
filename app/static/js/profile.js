(function(){const $=id=>document.getElementById(id);
G.api('GET','/api/profile').then(p=>{$('pn').value=p.name;$('info').textContent=p.email+' · '+p.role+' · '+p.company+(p.last_login_at?' · último acesso '+new Date(p.last_login_at).toLocaleString('pt-BR'):'')}).catch(x=>G.say(x.message,1));
$('fp').onsubmit=async e=>{e.preventDefault();try{await G.api('PATCH','/api/profile',{name:$('pn').value});G.say('Nome atualizado.')}catch(x){G.say(x.message,1)}};
$('fw').onsubmit=async e=>{e.preventDefault();try{await G.api('POST','/api/profile/password',{current_password:$('cur').value,new_password:$('nw').value});$('fw').reset();G.say('Senha alterada.')}catch(x){G.say(x.message,1)}}})();
