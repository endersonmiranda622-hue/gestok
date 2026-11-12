# GESTOK — Regras de negócio e decisões técnicas

Este documento reúne as regras e decisões implementadas, por área. Visão geral, instalação e riscos: veja o README.md.

## Comandos úteis
    pip install -r requirements.txt
    cp .env.example .env   # e exporte as variáveis no ambiente
    python manage.py migrate
    python manage.py check
    python -m unittest discover -s tests -v

## Convenções de dados
- Quantidades: INTEGER em milésimos (1,5 kg = 1500). Custos e preços: INTEGER em 1/10000 de R$.
- Datas em UTC (ISO-8601); o fuso da empresa (padrão America/Belem) é aplicado nos cálculos de dias.
- `stock_movements`, `movement_lines` e `audit_log` são imutáveis (triggers). Correção = estorno/ajuste.
- FKs compostas (id, company_id) impedem, no próprio banco, vincular dados de empresas diferentes.

## Segurança já ativa
Segredo obrigatório (>=32 chars) em produção, cookies HttpOnly/SameSite/Secure, cabeçalhos CSP/X-Frame-Options/HSTS,
erros genéricos, migrations com checksum (migration aplicada não pode ser alterada).

## Primeiro administrador (único caminho; não há cadastro público nem senha padrão)
Executado no servidor, por quem tem acesso à máquina:
    GESTOK_ADMIN_PASSWORD='...' python manage.py create-admin --company "Minha Empresa" --name "Fulano" --email fulano@empresa.com
(sem a variável, a senha é pedida de forma interativa). Cada empresa nova é criada por este comando.

## Recuperação de senha
Não há integração de e-mail. O formulário /forgot responde sempre igual; o link é gerado por
`python manage.py reset-link --email usuario@empresa.com` (token de uso único, 60 min, só o hash é guardado)
e deve ser entregue ao usuário pelo operador. Em desenvolvimento o link também aparece no log.

## Observações de segurança
Senhas com scrypt; bloqueio de 15 min após 5 falhas por e-mail ou 20 por IP; CSRF em todo POST; sessão renovada no login e
invalidada ao trocar senha ou desativar usuário. Atrás de proxy reverso, configure o IP real do cliente (Etapa 9),
senão o limite por IP vê só o IP do proxy. O bloqueio por e-mail permite que terceiros travem um usuário por 15 min (trade-off aceito).

## Permissões e usuários (Etapa 3)
Verificadas no servidor por `@require_perm(...)`/`@require_admin`; a empresa vem sempre da sessão (company_id no corpo é ignorado).
Admin: todas, fixas. Gestor/colaborador: configuráveis em `PUT /api/permissions/<role>` (só admin). Quem recebe `users.manage`
nunca administra administradores nem edita permissões. Usuários novos são convidados por link de definição de senha (sem senha
conhecida pelo admin). Garantias: sempre há ≥1 admin ativo; ninguém se desativa ou muda a própria função; negações ficam na auditoria (`access.denied`).

## Cadastros (Etapa 4)
Itens, categorias, unidades e depósitos: `/api/items`, `/api/categories`, `/api/units`, `/api/warehouses` (telas em /materiais e /cadastros).
Não há exclusão: só desativação. Código do item é normalizado em maiúsculas, único por empresa e imutável. Unidade, tipo e custo de um
item deixam de ser editáveis depois da primeira movimentação (o custo passa a ser calculado pelas entradas). Depósito com saldo não
desativa; unidade em uso não muda código/fracionamento. Decimais aceitam "1,5" ou "1.234,56"; guardados como inteiros (ver convenções).
Permissões: leitura = items.view; itens = items.edit; categorias/unidades = categories.manage; depósitos = warehouses.manage.

## Movimentações e estoque (Etapa 5)
`POST /api/movements` (entrada, saida, transferencia, ajuste, estorno), `GET /api/movements`, `GET /api/stock/<item>`,
`/api/adjustment-requests` (solicitar/aprovar/rejeitar). Tela em /movimentacoes.
- **Transação:** cada operação roda em `BEGIN IMMEDIATE`; qualquer falha desfaz tudo (movimentação, linhas, saldos, custo).
- **Idempotência:** chave obrigatória (header `Idempotency-Key`). Mesma chave + mesmo conteúdo = devolve a original (200); conteúdo diferente = 409.
- **Saldo negativo:** bloqueado, salvo `settings.allow_negative_stock='1'` (auditado como `stock.negative_allowed`; tela de configuração na Etapa 8).
- **Data/hora:** sempre a do servidor (sem lançamento retroativo), para não adulterar a regra dos 60 dias.
- **Custo médio ponderado (por item, todos os depósitos):** só entradas COM custo recalculam (arredondamento meia-unidade p/ cima, 4 casas);
  entrada sem custo (ex.: devolução) entra ao custo médio vigente e não o altera; saída/transferência/ajuste/estorno usam o custo vigente
  (estorno usa o custo da linha original) e NÃO recalculam o médio; saldo zerado + nova entrada redefine o custo. Saldo inicial = uma entrada.
- **Lotes:** itens com controle de lote exigem o lote; validade exigida e não pode estar vencida na entrada; saldo por lote é conferido.
- **Ajuste/estorno:** execução direta só com `adjustments.approve`; os demais solicitam e outro usuário aprova (ninguém decide a própria solicitação).
  Aprovação revalida o estoque atual. Cada movimentação só pode ser estornada uma vez; estorno não é estornável.
- **Consistência:** `python manage.py reconcile` compara saldos materializados com o histórico (retorna erro se houver divergência).

## Estoque parado, valorização e alertas (Etapa 6)
`GET /api/stale`, `/api/valuation`, `/api/alerts[?type=]`, `GET/PUT /api/settings`; tela em /estoque-parado.
- **Regra dos 60 dias (padrão):** linha de saldo (item x depósito) com saldo > 0 cuja última movimentação que alterou aquele saldo ocorreu há
  60 dias ou mais, contando dias de calendário no fuso da empresa (padrão America/Belem; 59 dias = não parado, 60 = parado). Consultas,
  edição de cadastro ou troca de categoria NÃO contam. Uma transferência conta como movimentação nos dois depósitos. Itens sem saldo
  (ou negativos) nunca são parados; itens sem nenhuma movimentação aparecem em "sem histórico", sem data inventada.
- **Valor:** quantidade x custo médio ponderado do item (nunca o preço de venda), arredondado ao centavo (meia-unidade p/ cima) POR LINHA de
  saldo; todos os totais (cartões, tabelas, por categoria/depósito/tipo, alertas) somam essas mesmas linhas. Só saldos positivos. Quantidades de
  unidades diferentes nunca são somadas (`qty_by_unit`). Valores extremos (>~9x10^18 no produto qtd x custo internos) perdem precisão no SQLite.
- **Configurações (por empresa, só `settings.manage`, auditadas antes/depois):** `stale_days` (1-3650, padrão 60), `expiry_alert_days` (0-365, padrão 30),
  `allow_negative_stock` (0/1, padrão 0).
- **Alertas (item ativo):** `zero` (saldo 0), `negative` (saldo < 0), `replenish` (mínimo > 0 e 0 < saldo <= mínimo), `stale`, `expiring`/`expired`
  (lotes com saldo; validade no intervalo / anterior a hoje), mais contadores de atenção (7 dias): operações rejeitadas, acessos negados, estoque
  negativo autorizado. Não há envio de e-mail/notificação externa: os alertas são calculados e exibidos no sistema.

## Painel e relatórios (Etapa 7)
`GET /api/dashboard?from=&to=` (padrão: mês corrente no fuso da empresa; máx. 366 dias; diário até 92 dias, mensal acima), tela em `/` (quem tem
`dashboard.view`; os demais veem uma página inicial simples). `GET /api/reports` e `/api/reports/<nome>` (10 relatórios, `reports.view`), tela em /relatorios:
position, entries, exits, history, stale, valuation, replenish, lots, by_user, adjustments. Filtros: período, categoria, item, depósito, usuário, tipo de item, tipo de movimentação.
Cada relatório devolve `columns` (com tipo), `rows` (valores brutos), `total_rows` e `totals` — a mesma estrutura alimentará as exportações da Etapa 8.
**Definições (também exibidas no painel):** valor de linha = quantidade x custo da linha, arredondado ao centavo por linha (simétrico).
Entradas = linhas de entrada, líquidas de estornos de entradas; Saídas = linhas de saída, líquidas de estornos de saídas (ao custo médio vigente na saída);
Saldo do período = Entradas − Saídas em valor; transferências ficam de fora; ajustes aparecem à parte (líquido, com sinal). Quantidades são mostradas
por unidade, nunca somadas entre unidades. Valor em estoque, capital parado, alertas e relatórios usam as mesmas funções (um único critério).
Gráficos são SVG gerados no navegador a partir da API (sem bibliotecas externas, compatível com a política de segurança de conteúdo).

## Exportações, configurações, perfil e auditoria (Etapa 8)
- **Exportação:** `GET /api/reports/<nome>/export?format=csv|xlsx|pdf` + os mesmos filtros do relatório (permissão `reports.view`, isolada por empresa, auditada como `report.exported`).
  Máx. 50.000 linhas (413 acima disso: refine os filtros). CSV: UTF-8 com BOM, separador `;`, vírgula decimal, sem milhar (abre certo no Excel pt-BR), datas no fuso da empresa.
  XLSX: números/datas nativos com formato, cabeçalho congelado e filtro. PDF: A4 paisagem, cabeçalho repetido, rodapé com página. Todos trazem linha TOTAL quando há valor.
  **Injeção de fórmulas:** texto iniciado por `= + - @` TAB ou CR recebe apóstrofo inicial (CSV e XLSX) e células de texto do XLSX são gravadas como string, nunca como fórmula.
- **/configuracoes** (`settings.manage`): dados da empresa (nome, documento, fuso — o fuso muda o "dia" de todos os cálculos), regras (dias de estoque parado, antecedência de validade,
  itens por página, estoque negativo) e editor de permissões de gestor/colaborador (só admin). Tudo auditado com antes/depois.
- **/perfil:** nome e troca de senha (exige a senha atual, com limite de tentativas; encerra as outras sessões e mantém a atual).
- **/auditoria** (`audit.view`): administrador vê tudo (com IP); demais com a permissão veem só movimentações, ajustes, estoque, itens e exportações (sem eventos de segurança nem IP).
  O registro é imutável (triggers no banco).
