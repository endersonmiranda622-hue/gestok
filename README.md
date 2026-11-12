# GESTOK — Gestão inteligente de materiais e estoque

Aplicação web multiempresa para controle de materiais de almoxarifado e produtos: cadastros, movimentações com saldo transacional, lotes e
validades, estoque parado (regra de 60 dias), valorização por custo médio ponderado, alertas, painel, relatórios e exportações.

## Status — leia antes de usar
**As 9 etapas do plano foram implementadas e testadas (205 testes automatizados, todos aprovados).**
**Isto NÃO significa "pronto para dados reais de uma empresa".** O próprio documento de requisitos (§20) exige itens que dependem de ambiente e que não pude
verificar aqui: HTTPS real em produção, teste das telas em navegador e em celular reais, agendamento de backup e varredura de dependências.
Recomendação: usar em **homologação/piloto** com dados não críticos e resolver a lista de **riscos** (`docs/SEGURANCA_E_RISCOS.md`) antes de dados reais.

## Arquitetura e tecnologias
- **Backend:** Python 3.12 + Flask 3.1 (Blueprints por módulo; regras de negócio em `app/stock.py`, `app/accounts.py`, `app/analytics.py`).
- **Banco:** SQLite (WAL, `synchronous=FULL`, chaves estrangeiras ligadas), SQL puro, **migrations versionadas com checksum**. Escritas serializadas por `BEGIN IMMEDIATE`.
- **Frontend:** HTML (Jinja2, com escape automático) + CSS próprio responsivo + JavaScript simples sem bibliotecas externas (gráficos em SVG). Política CSP `default-src 'self'`.
- **Exportações:** `openpyxl` (Excel) e `reportlab` (PDF); CSV pela biblioteca padrão. **Testes:** `unittest` (biblioteca padrão).
- **Isolamento entre empresas:** `company_id` em toda tabela de negócio; a empresa vem sempre da sessão no servidor; FKs compostas `(id, company_id)` impedem referências cruzadas **no próprio banco**.
- **Integridade do estoque:** saldo só muda junto com a movimentação, na mesma transação; histórico imutável por triggers; correção só por estorno/ajuste; idempotência obrigatória;
  `python manage.py reconcile` confere os saldos contra o histórico.

## Estrutura de arquivos
```
app/            código (config, db, accounts, security, authz, users, catalog, stock, movements, analytics, dashboard, exports, admin, ops)
app/templates/  páginas HTML          app/static/    css e js
migrations/     001..004 (SQL versionado)
tests/          test_stage1..9 (205 testes)
deploy/         exemplos de gunicorn/nginx/systemd (NÃO testados aqui)
docs/           MODELO_DE_DADOS.md (gerado) · REGRAS_DE_NEGOCIO.md · OPERACAO.md · SEGURANCA_E_RISCOS.md
tools/          gen_schema_doc.py        manage.py (CLI)        wsgi.py (entrada de produção)
```

## Instalar e executar (desenvolvimento)
```
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt          # versões fixadas = as testadas
export GESTOK_ENV=development
python manage.py migrate
GESTOK_ADMIN_PASSWORD='Senha-forte-123' python manage.py create-admin --company "Minha Empresa" --name "Seu Nome" --email voce@empresa.com
flask --app wsgi run                     # http://localhost:5000  (apenas desenvolvimento)
python manage.py doctor                  # diagnóstico
python -m unittest discover -s tests     # testes (≈2 min)
```
Produção: veja `docs/OPERACAO.md`. Variáveis de ambiente: `.env.example` (tabela abaixo).

| Variável | Padrão | Função |
|---|---|---|
| `GESTOK_ENV` | development | `production` ativa HTTPS obrigatório, cookie `__Host-` Secure e exige segredo forte |
| `GESTOK_SECRET_KEY` | — | Obrigatória em produção (≥32 caracteres). Assina cookies. Nunca no repositório |
| `GESTOK_DATABASE_PATH` | instance/gestok.db | Arquivo do banco |
| `GESTOK_BASE_URL` | http://localhost:5000 | Base dos links de redefinição de senha |
| `GESTOK_FORCE_HTTPS` | 1 em produção | Redireciona GET para HTTPS e recusa POST em HTTP |
| `GESTOK_TRUSTED_PROXIES` | 0 | Nº de proxies confiáveis para ler `X-Forwarded-*` (IP real no limite de login) |
| `GESTOK_SESSION_ABS_HOURS` / `_IDLE_MINUTES` | 8 / 120 | Validade absoluta e por inatividade da sessão |

## Primeiro administrador (procedimento seguro)
Não há senha padrão nem cadastro público. Quem tem acesso ao servidor executa `python manage.py create-admin --company ... --name ... --email ...`
(a senha vem de `GESTOK_ADMIN_PASSWORD` ou é pedida sem eco). O mesmo comando cria cada nova empresa. Depois disso, o administrador convida os demais usuários
pela tela de usuários (cada um define a própria senha por link de uso único). Nada fica "aberto" após a configuração: o procedimento é só por linha de comando no servidor.

## Backup e restauração (testados)
```
python manage.py backup --dest /var/backups/gestok --keep 14     # cópia consistente (mesmo com o sistema em uso) + SHA-256 + verificação de integridade
# desastre: pare a aplicação, então
python manage.py restore --file /var/backups/gestok/gestok-AAAAMMDD-HHMMSS-uuuuuu.db [--force]
python manage.py migrate && python manage.py doctor
```
Teste feito: perda total do banco → restauração → login com a senha original, saldos conferidos com o histórico. O **agendamento** (systemd timer) está em `deploy/` mas **não foi testado**.
Backups contêm e-mails, hashes de senha e IPs: proteja e, se sair do servidor, criptografe. Detalhes em `docs/OPERACAO.md`.

## Testes executados (205 aprovados, 0 falhas, 0 ignorados — último run: 111 s)
`test_stage1` 20 · `2` 21 · `3` 17 · `4` 24 · `5` 37 · `6` 19 · `7` 16 · `8` 20 · `9` 31. Cobertura dos 22 itens do §19 do documento:

| §19 | Onde | | §19 | Onde |
|---|---|---|---|---|
| 1 cadastro/edição | stage4 | | 12 sem saldo/sem histórico | stage6 |
| 2 código duplicado | stage1, 4 | | 13 alertas reposição/validade | stage6, 7 |
| 3 entrada · 4 saída | stage5 | | 14 permissões por função | stage3, 5, 9 (varredura de **todas** as rotas) |
| 5 saída > saldo | stage5 | | 15 sem autenticação | stage2, 9 (varredura de todas as rotas) |
| 6 transferência | stage5 | | 16 acesso direto à API | stage9 |
| 7 ajustes/estornos | stage5 | | 17 isolamento entre empresas | stage3–9 (inclui varredura em ~35 endpoints) |
| 8 persistência/reinício | stage1, 4, 9 (restauração) | | 18 recuperação de senha | stage2 |
| 9 valor total · 10 custo médio | stage5, 6, 7 | | 19 exportações e filtros | stage8 |
| 11 estoque parado (59/60/61 dias) | stage6 | | 20 concorrência · 21 falha em transação | stage5, 9 |
| | | | **22 layout celular/PC** | stage9 — **apenas estrutural** (ver limitações) |

Também testado: injeção de SQL, XSS armazenado, injeção de fórmulas em CSV/Excel, sessão roubada após logout, HTTPS/cookies de produção, proxy e IP real, backup sob escrita concorrente,
restauração com arquivo adulterado/corrompido, migração após restaurar esquema antigo, e que as próprias varreduras detectam rotas inseguras plantadas de propósito.

## Funcionalidades concluídas (as 10 áreas do §3)
Painel de controle · Materiais e produtos (lotes, validade, código de barras) · Movimentações (entrada, saída, transferência, ajuste, estorno, solicitação/aprovação) · Estoque parado ·
Relatórios (10) com exportação CSV/Excel/PDF · Cadastros (categorias, unidades, depósitos) · Usuários e permissões configuráveis · Configurações da empresa · Perfil · Auditoria.
Regras detalhadas: `docs/REGRAS_DE_NEGOCIO.md`.

## Pendentes e limitações conhecidas
- **Telas nunca executadas em navegador real.** O JavaScript foi verificado por análise estática (sem `innerHTML`/`eval`) e a API/HTML por testes, mas nenhum navegador rodou os scripts; o layout responsivo foi
  verificado só estruturalmente (meta viewport, regras `@media`, tabelas roláveis, campos com rótulo). **Teste manual em PC e celular é obrigatório.**
- **Sem envio de e-mail/notificações.** A redefinição de senha usa link entregue pelo administrador/operador (`manage.py reset-link` ou tela de usuários). Alertas só aparecem no sistema.
- **SQLite, um único servidor:** sem proteção por linha no banco (RLS) além das FKs compostas, sem alta disponibilidade; escritas serializadas. Para muitas empresas/carga alta, migrar para PostgreSQL com RLS.
- **Sem 2FA.** Sem lançamento retroativo de movimentações (data = hora do servidor). Formulário de movimentação registra um item por vez (a API aceita várias linhas). Sem importação em lote, contagem de inventário guiada ou fotos de itens.
- **Deploy não testado:** `deploy/` (gunicorn, nginx, systemd, timer de backup) são exemplos; TLS/HTTPS depende da sua infraestrutura. Dependências **não passaram por varredura de vulnerabilidades** (sem rede aqui).
- Sem testes de carga/volume. Valores extremos (produto quantidade×custo > ~9×10¹⁸ internos) perdem precisão no SQLite.

## Riscos a resolver antes de usar dados reais
Lista completa, com severidade e mitigação, em **`docs/SEGURANCA_E_RISCOS.md`**. Os principais: (1) validar telas em navegador/celular; (2) colocar atrás de HTTPS e definir `GESTOK_TRUSTED_PROXIES`;
(3) agendar e **testar a restauração** de backups no seu ambiente; (4) varredura de dependências (`pip-audit`); (5) decidir política de retenção/eliminação de dados pessoais (LGPD) frente à auditoria imutável;
(6) avaliar 2FA e PostgreSQL conforme o porte.
