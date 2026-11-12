# Operação do GESTOK

> Comandos de `manage.py` e `ops` abaixo foram **testados**. Arquivos em `deploy/` são **exemplos não testados** (este ambiente não tem systemd, nginx nem gunicorn).

## 1. Implantação (produção)
1. Servidor Linux, Python 3.12, usuário dedicado sem login (`gestok`), diretório `/opt/gestok`; `pip install -r requirements-prod.txt` em um venv.
2. `/etc/gestok.env` (chmod 600, dono `gestok`) com: `GESTOK_ENV=production`, `GESTOK_SECRET_KEY` (≥32 aleatórios), `GESTOK_DATABASE_PATH=/var/lib/gestok/gestok.db`,
   `GESTOK_BASE_URL=https://seu.dominio`, `GESTOK_TRUSTED_PROXIES=1` (se houver 1 proxy). O app **recusa iniciar** em produção sem segredo forte.
3. `python manage.py migrate` → `python manage.py create-admin ...` → `python manage.py doctor` (deve terminar sem `[FAIL]`).
4. Proxy reverso com TLS (exemplo em `deploy/nginx.conf.example`). **O proxy deve sobrescrever `X-Forwarded-For`** (não acrescentar), senão o IP usado no limite de login pode ser forjado.
   Mascare ou não registre o caminho `/reset/` nos logs de acesso (o token de redefinição trafega na URL).
5. Serviço: `deploy/gestok.service` (gunicorn em `127.0.0.1:8000`). Em produção o app redireciona GET de HTTP para HTTPS e recusa POST em HTTP; `/healthz` responde sempre (monitoramento).
6. O arquivo do banco e a pasta de backups devem ter permissão restrita (`chmod 600`/`700`); `doctor` avisa se o banco estiver legível por outros usuários.

## 2. Atualizações
`git pull` (ou novo pacote) → **faça um backup** → `python manage.py migrate` (aditivo; migration já aplicada que seja editada é recusada por checksum) → `doctor` → reiniciar o serviço.
Nunca apague nem recrie o banco para atualizar.

## 3. Backup
```
python manage.py backup --dest /var/backups/gestok --keep 14
```
Usa a API de backup do SQLite (cópia consistente mesmo com escritas em andamento — testado), confere `integrity_check` e `foreign_key_check` na cópia, grava `.sha256` ao lado, aplica `chmod 600`
e mantém só os 14 mais recentes (apaga apenas arquivos no padrão `gestok-*.db`). Agendamento: `deploy/gestok-backup.timer` (diário 02:30) ou cron — **não testado aqui**.
**Boas práticas:** copiar para outro local/servidor; criptografar se sair da máquina (o backup tem e-mails, hashes de senha e IPs); testar a restauração periodicamente.

## 4. Restauração após falha
1. **Pare a aplicação** (o comando recusa se o banco estiver em uso).
2. Escolha o backup (o `.sha256` deve estar junto): `python manage.py restore --file /var/backups/gestok/gestok-...db`
   — se já existir um banco, o comando recusa; use `--force` para substituir (ele salva antes uma cópia `gestok.db.pre-restore-AAAAMMDD-HHMMSS`).
3. O comando verifica SHA-256, integridade e chaves estrangeiras **antes** de tocar no banco, troca o arquivo de forma atômica, remove `-wal/-shm` antigos e confere saldos × histórico.
4. `python manage.py migrate` (aplica migrations que o backup ainda não tinha — testado) → `python manage.py doctor` → subir a aplicação.
5. Perda desde o último backup: movimentações feitas depois dele não existem mais; reconstrua a partir dos documentos de origem. Sessões ativas continuam válidas só se estiverem no backup.
Recusas testadas: arquivo adulterado (SHA-256), sem `.sha256`, arquivo corrompido, banco em uso, sobrescrita sem `--force`.

## 5. Verificação e rotina
- `python manage.py doctor` — modo, HTTPS, proxies, permissões do arquivo, integridade, esquema, saldos × histórico, administrador, WAL. Saída 1 se houver `[FAIL]`.
- `python manage.py reconcile` — saldos × histórico (também no `doctor`). Rodar após restauração e periodicamente.
- `GET /healthz` — banco acessível e versão do esquema.
- Auditoria (`/auditoria`, administrador): logins, falhas, permissões, configurações, exportações, operações rejeitadas.

## 6. Senhas e acesso
- Usuário esqueceu a senha: administrador usa "Link de senha" em `/usuarios`, ou o operador roda `python manage.py reset-link --email ...`. O link vale 60 min, uso único, e é entregue pelo canal que vocês escolherem (não há e-mail automático).
- Troca da `GESTOK_SECRET_KEY`: invalida todos os cookies (todos precisam entrar de novo). Troque se houver suspeita de vazamento.
- Remover um usuário = desativar (histórico é preservado). Desativação e mudança de função encerram as sessões dele.
