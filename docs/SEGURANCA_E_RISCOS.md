# Segurança: controles, achados da auditoria e riscos

## 1. Controles implementados (e onde foram testados)
| Área | Controle | Teste |
|---|---|---|
| Senhas | scrypt; política mín. 10 caracteres com letras e números; nunca em texto puro | stage2 |
| Login | resposta idêntica para e-mail inexistente/senha errada; tempo equalizado; bloqueio 15 min (5 falhas/e-mail, 20/IP); auditoria | stage2 |
| Sessão | token aleatório, **só o hash no banco**, revogável: logout, expiração absoluta (8 h), inatividade (2 h), desativação, troca de função e de senha | stage2, 3, 8, **9** |
| Cookie | HttpOnly, SameSite=Lax; em produção `__Host-` + Secure + sem Domain | stage9 |
| CSRF | token em todo POST/PUT/PATCH/DELETE (inclui login) | stage2, 9 |
| HTTPS | produção redireciona GET e recusa POST em HTTP; HSTS; `X-Forwarded-*` só se `GESTOK_TRUSTED_PROXIES` > 0 | stage9 |
| Cabeçalhos | CSP `default-src 'self'` (sem scripts/estilos inline), X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy, COOP/CORP, `Cache-Control: no-store` | stage1, 9 |
| Autorização | permissões verificadas no servidor; **varredura automática de todas as rotas**: anônimo → 401/302; usuário sem permissões → 403 em toda rota `/api` e página; rotas só-admin fechadas até para gestor com todas as permissões | stage3, 9 |
| Multiempresa | empresa vem da sessão; FKs compostas no banco; `company_id` do corpo ignorado; **varredura em ~35 endpoints/relatórios/exportações** sem vazamento | stage3–9 |
| Injeções | SQL parametrizado (listas brancas p/ ordenação); 8 cargas de SQLi em 11 endpoints; Jinja com autoescape; JS sem `innerHTML`/`eval`; sem `\|safe`/`style=`/`on*=` nos templates | stage9 |
| Exportação | neutraliza fórmulas (`= + - @`) em CSV e XLSX; limite 50.000 linhas; respeita filtros/permissões/empresa; auditada | stage8 |
| Integridade | transação única por operação; histórico e auditoria imutáveis (triggers); idempotência; `reconcile`; estorno único | stage1, 5, 9 |
| Erros | 500/404 genéricos, sem rastros; cabeçalhos mantidos | stage1, 9 |
| Segredos | só por variável de ambiente; app recusa iniciar em produção sem segredo forte; backups sem segredos | stage1, 9 |

## 2. Achados da auditoria interna e correções
| # | Gravidade | Achado | Situação |
|---|---|---|---|
| 1 | **Alta** | Logout só apagava o cookie no navegador: um cookie roubado seguia válido até 8 h (cookie assinado sem estado no servidor) | **Corrigido** (migration 004, sessões revogáveis); teste de reutilização do cookie após logout |
| 2 | Média | Limite de login por IP seria falsificável por `X-Forwarded-For` atrás de proxy | **Mitigado**: cabeçalho ignorado por padrão; confiança explícita e numérica por configuração; testado nos dois modos |
| 3 | Média | Duas telas tinham campos sem rótulo acessível | **Corrigido** (detectado pelo novo teste de acessibilidade) |
| 4 | Baixa | Atributo `style=` inline no menu lateral seria bloqueado pela CSP (nome "GESTOK" ilegível) | **Corrigido**; teste impede reincidência |
| 5 | Média | O arquivo do banco era criado com permissão `0644` (legível por outros usuários do servidor), achado pelo `doctor` ao testar o pacote extraído | **Corrigido**: banco novo (e `-wal`/`-shm`) nasce `0600`; banco existente não é alterado; testado |
| 6 | — | Durante o desenvolvimento, a CSP já havia pego um `<script>` inline (Etapa 4) | Corrigido na época |
A varredura de rotas foi validada por **mutação**: plantei uma rota sem autenticação e outra sem checagem de permissão, e os critérios da varredura as classificam como falha.

## 3. Riscos residuais — resolver antes de dados reais
| # | Sev. | Risco | Mitigação / decisão necessária |
|---|---|---|---|
| 1 | Alta | **Telas nunca rodaram em navegador real** (JS, gráficos, formulários); layout responsivo só verificado estruturalmente | Roteiro manual em PC e celular (login, cadastro, movimentação, relatórios, exportação, configurações) antes do piloto |
| 2 | Alta | Produção/HTTPS **não verificados** (gunicorn, nginx, TLS, systemd são exemplos) | Subir em homologação, rodar `doctor`, confirmar redirecionamento, cookie `__Host-` e IP real |
| 3 | Alta | **Restauração só testada aqui**; agendamento do backup não testado; sem cópia off-site automática | Agendar, copiar para fora do servidor e **ensaiar a restauração** no seu ambiente |
| 4 | Média | Dependências sem varredura de vulnerabilidades (sem rede) | `pip-audit` / atualização das versões fixadas; reexecutar os testes |
| 5 | Média | **LGPD:** guarda nome, e-mail e IP; auditoria imutável impede apagar registros pessoais | Definir base legal, retenção e como atender pedido de eliminação (ex.: anonimizar usuário desativado — não implementado) |
| 6 | Média | Sem 2FA; administrador pode gerar link de redefinição de qualquer usuário (poder de personificação; é auditado) | 2FA e/ou aprovação dupla para administradores, conforme o risco da empresa |
| 7 | Média | Sem e-mail: redefinição e alertas dependem de entrega manual | Integrar provedor de e-mail/SMS quando houver |
| 8 | Média | SQLite em um servidor: sem RLS, sem alta disponibilidade, escritas serializadas | Para várias empresas ou carga alta: PostgreSQL com RLS (reescrita da camada de dados) |
| 9 | Baixa | Bloqueio de login por e-mail permite travar a conta de outra pessoa por 15 min | Aceito; monitorar `login.failed` na auditoria |
| 10 | Baixa | Token de redefinição aparece na URL (logs do proxy) | Mascarar `/reset/` nos logs; TTL 60 min, uso único |
| 11 | Baixa | Backups contêm e-mails, hashes de senha e IPs, sem criptografia própria | Criptografar e restringir acesso aos backups |
| 12 | Baixa | Mudar o fuso da empresa reclassifica retroativamente "dias parados" | Documentado; alterar com cautela |
| 13 | Baixa | Sem teste de carga; precisão de valores extremos limitada pelo SQLite | Medir com o volume real |
| 14 | Info | Sem lançamento retroativo, inventário guiado, importação em lote | Backlog funcional |

## 4. Critérios de aceitação (§20 do documento) — situação honesta
Atendidos e testados: funcionalidades obrigatórias, persistência, cálculos consistentes, 60 dias, permissões no servidor, isolamento, histórico preservado, tratamento de erros, testes críticos, backup/restauração documentados e testados.
**Não verificáveis neste ambiente:** configuração real de produção com HTTPS e segredos no servidor definitivo (itens 2 e 4 acima) e "nenhuma falha crítica conhecida sem tratamento" depende do teste manual em navegador (item 1).
Por isso o sistema **não é declarado pronto para uso empresarial**: está pronto para homologação.
