# Modelo de dados do GESTOK

Gerado automaticamente das migrations por `tools/gen_schema_doc.py` (não edite à mão).

Migrations: 001_initial, 002_session_epoch, 003_movements_support, 004_user_sessions.

Convenções: quantidades em milésimos (INTEGER); custos/preços em 1/10000 de R$ (INTEGER); valores financeiros agregados em centavos; datas em UTC ISO-8601.
FKs compostas `(id, company_id)` impedem, no próprio banco, ligar registros de empresas diferentes. Tabelas `stock_movements`, `movement_lines` e `audit_log` são imutáveis (triggers).

## `adjustment_requests`

| Coluna | Tipo | Obrigatória | Padrão | Chave |
|---|---|---|---|---|
| id | INTEGER | sim |  | PK |
| company_id | INTEGER | sim |  |  |
| requested_by | INTEGER | sim |  |  |
| kind | TEXT | sim |  |  |
| payload | TEXT | sim |  |  |
| reason | TEXT | sim |  |  |
| status | TEXT | sim | `'pendente'` |  |
| decided_by | INTEGER | não |  |  |
| decided_at | TEXT | não |  |  |
| decision_note | TEXT | não |  |  |
| movement_id | TEXT | não |  |  |
| created_at | TEXT | sim | `strftime('%Y-%m-%dT%H:%M:%fZ','now')` |  |

Chaves estrangeiras: (movement_id, company_id) → stock_movements(id, company_id); (decided_by, company_id) → users(id, company_id); (requested_by, company_id) → users(id, company_id); (company_id) → companies(id)

Índices: `ix_adjreq_company`

## `audit_log`

| Coluna | Tipo | Obrigatória | Padrão | Chave |
|---|---|---|---|---|
| id | INTEGER | sim |  | PK |
| company_id | INTEGER | não |  |  |
| user_id | INTEGER | não |  |  |
| action | TEXT | sim |  |  |
| entity | TEXT | não |  |  |
| entity_id | TEXT | não |  |  |
| details | TEXT | não |  |  |
| ip | TEXT | não |  |  |
| created_at | TEXT | sim | `strftime('%Y-%m-%dT%H:%M:%fZ','now')` |  |

Chaves estrangeiras: (user_id) → users(id); (company_id) → companies(id)

Índices: `ix_audit_company`

Triggers (imutabilidade): `trg_audit_no_update`, `trg_audit_no_delete`

## `categories`

| Coluna | Tipo | Obrigatória | Padrão | Chave |
|---|---|---|---|---|
| id | INTEGER | sim |  | PK |
| company_id | INTEGER | sim |  |  |
| name | TEXT | sim |  |  |
| active | INTEGER | sim | `1` |  |

Chaves estrangeiras: (company_id) → companies(id)

Unicidade: (id, company_id); (company_id, name)

## `companies`

| Coluna | Tipo | Obrigatória | Padrão | Chave |
|---|---|---|---|---|
| id | INTEGER | sim |  | PK |
| name | TEXT | sim |  |  |
| document | TEXT | não |  |  |
| timezone | TEXT | sim | `'America/Belem'` |  |
| active | INTEGER | sim | `1` |  |
| created_at | TEXT | sim | `strftime('%Y-%m-%dT%H:%M:%fZ','now')` |  |

## `items`

| Coluna | Tipo | Obrigatória | Padrão | Chave |
|---|---|---|---|---|
| id | INTEGER | sim |  | PK |
| company_id | INTEGER | sim |  |  |
| code | TEXT | sim |  |  |
| barcode | TEXT | não |  |  |
| name | TEXT | sim |  |  |
| description | TEXT | não |  |  |
| item_type | TEXT | sim |  |  |
| category_id | INTEGER | não |  |  |
| unit_id | INTEGER | sim |  |  |
| default_warehouse_id | INTEGER | não |  |  |
| min_qty_milli | INTEGER | sim | `0` |  |
| avg_cost_e4 | INTEGER | sim | `0` |  |
| sale_price_e4 | INTEGER | não |  |  |
| tracks_lot | INTEGER | sim | `0` |  |
| tracks_expiry | INTEGER | sim | `0` |  |
| active | INTEGER | sim | `1` |  |
| created_by | INTEGER | não |  |  |
| created_at | TEXT | sim | `strftime('%Y-%m-%dT%H:%M:%fZ','now')` |  |
| updated_at | TEXT | não |  |  |

Chaves estrangeiras: (created_by, company_id) → users(id, company_id); (default_warehouse_id, company_id) → warehouses(id, company_id); (unit_id, company_id) → units(id, company_id); (category_id, company_id) → categories(id, company_id); (company_id) → companies(id)

Unicidade: (id, company_id); (company_id, code)

Índices: `ix_items_name`, `ux_items_barcode` (único)

## `login_attempts`

| Coluna | Tipo | Obrigatória | Padrão | Chave |
|---|---|---|---|---|
| id | INTEGER | sim |  | PK |
| email | TEXT | sim |  |  |
| ip | TEXT | sim |  |  |
| success | INTEGER | sim |  |  |
| created_at | TEXT | sim | `strftime('%Y-%m-%dT%H:%M:%fZ','now')` |  |

Índices: `ix_login_attempts_ip`, `ix_login_attempts`

## `lot_balances`

| Coluna | Tipo | Obrigatória | Padrão | Chave |
|---|---|---|---|---|
| company_id | INTEGER | sim |  | PK |
| lot_id | INTEGER | sim |  | PK |
| warehouse_id | INTEGER | sim |  | PK |
| qty_milli | INTEGER | sim | `0` |  |

Chaves estrangeiras: (warehouse_id, company_id) → warehouses(id, company_id); (lot_id, company_id) → lots(id, company_id)

Unicidade: (company_id, lot_id, warehouse_id)

## `lots`

| Coluna | Tipo | Obrigatória | Padrão | Chave |
|---|---|---|---|---|
| id | INTEGER | sim |  | PK |
| company_id | INTEGER | sim |  |  |
| item_id | INTEGER | sim |  |  |
| lot_code | TEXT | sim |  |  |
| expiry_date | TEXT | não |  |  |
| created_at | TEXT | sim | `strftime('%Y-%m-%dT%H:%M:%fZ','now')` |  |

Chaves estrangeiras: (item_id, company_id) → items(id, company_id)

Unicidade: (id, company_id); (company_id, item_id, lot_code)

## `movement_lines`

| Coluna | Tipo | Obrigatória | Padrão | Chave |
|---|---|---|---|---|
| id | INTEGER | sim |  | PK |
| movement_id | TEXT | sim |  |  |
| company_id | INTEGER | sim |  |  |
| item_id | INTEGER | sim |  |  |
| warehouse_id | INTEGER | sim |  |  |
| lot_id | INTEGER | não |  |  |
| qty_milli | INTEGER | sim |  |  |
| unit_cost_e4 | INTEGER | sim |  |  |

Chaves estrangeiras: (lot_id, company_id) → lots(id, company_id); (warehouse_id, company_id) → warehouses(id, company_id); (item_id, company_id) → items(id, company_id); (movement_id, company_id) → stock_movements(id, company_id)

Índices: `ix_lines_mov`, `ix_lines_item`

Triggers (imutabilidade): `trg_line_no_update`, `trg_line_no_delete`

## `password_reset_tokens`

| Coluna | Tipo | Obrigatória | Padrão | Chave |
|---|---|---|---|---|
| id | INTEGER | sim |  | PK |
| user_id | INTEGER | sim |  |  |
| token_hash | TEXT | sim |  |  |
| expires_at | TEXT | sim |  |  |
| used_at | TEXT | não |  |  |
| created_at | TEXT | sim | `strftime('%Y-%m-%dT%H:%M:%fZ','now')` |  |

Chaves estrangeiras: (user_id) → users(id)

Unicidade: (token_hash)

Índices: `ix_reset_user`

## `role_permissions`

| Coluna | Tipo | Obrigatória | Padrão | Chave |
|---|---|---|---|---|
| company_id | INTEGER | sim |  | PK |
| role | TEXT | sim |  | PK |
| permission | TEXT | sim |  | PK |

Chaves estrangeiras: (company_id) → companies(id)

Unicidade: (company_id, role, permission)

## `schema_migrations`

| Coluna | Tipo | Obrigatória | Padrão | Chave |
|---|---|---|---|---|
| version | TEXT | sim |  | PK |
| checksum | TEXT | sim |  |  |
| applied_at | TEXT | sim | `strftime('%Y-%m-%dT%H:%M:%fZ','now')` |  |

Unicidade: (version)

## `settings`

| Coluna | Tipo | Obrigatória | Padrão | Chave |
|---|---|---|---|---|
| company_id | INTEGER | sim |  | PK |
| key | TEXT | sim |  | PK |
| value | TEXT | sim |  |  |

Chaves estrangeiras: (company_id) → companies(id)

Unicidade: (company_id, key)

## `stock_balances`

| Coluna | Tipo | Obrigatória | Padrão | Chave |
|---|---|---|---|---|
| company_id | INTEGER | sim |  | PK |
| item_id | INTEGER | sim |  | PK |
| warehouse_id | INTEGER | sim |  | PK |
| qty_milli | INTEGER | sim | `0` |  |
| last_movement_at | TEXT | não |  |  |

Chaves estrangeiras: (warehouse_id, company_id) → warehouses(id, company_id); (item_id, company_id) → items(id, company_id)

Unicidade: (company_id, item_id, warehouse_id)

## `stock_movements`

| Coluna | Tipo | Obrigatória | Padrão | Chave |
|---|---|---|---|---|
| id | TEXT | sim |  | PK |
| company_id | INTEGER | sim |  |  |
| kind | TEXT | sim |  |  |
| user_id | INTEGER | sim |  |  |
| reason | TEXT | não |  |  |
| reverses_id | TEXT | não |  |  |
| idempotency_key | TEXT | não |  |  |
| occurred_at | TEXT | sim |  |  |
| created_at | TEXT | sim | `strftime('%Y-%m-%dT%H:%M:%fZ','now')` |  |
| request_hash | TEXT | não |  |  |

Chaves estrangeiras: (reverses_id, company_id) → stock_movements(id, company_id); (user_id, company_id) → users(id, company_id); (company_id) → companies(id)

Unicidade: (company_id, idempotency_key); (id, company_id); (reverses_id); (id)

Índices: `ix_mov_company_date`

Triggers (imutabilidade): `trg_mov_no_update`, `trg_mov_no_delete`

## `units`

| Coluna | Tipo | Obrigatória | Padrão | Chave |
|---|---|---|---|---|
| id | INTEGER | sim |  | PK |
| company_id | INTEGER | sim |  |  |
| code | TEXT | sim |  |  |
| name | TEXT | sim |  |  |
| allow_fraction | INTEGER | sim | `0` |  |
| active | INTEGER | sim | `1` |  |

Chaves estrangeiras: (company_id) → companies(id)

Unicidade: (id, company_id); (company_id, code)

## `user_sessions`

| Coluna | Tipo | Obrigatória | Padrão | Chave |
|---|---|---|---|---|
| id | INTEGER | sim |  | PK |
| token_hash | TEXT | sim |  |  |
| user_id | INTEGER | sim |  |  |
| created_at | TEXT | sim | `strftime('%Y-%m-%dT%H:%M:%fZ','now')` |  |
| last_seen_at | TEXT | sim | `strftime('%Y-%m-%dT%H:%M:%fZ','now')` |  |
| expires_at | TEXT | sim |  |  |
| revoked_at | TEXT | não |  |  |
| ip | TEXT | não |  |  |

Chaves estrangeiras: (user_id) → users(id)

Unicidade: (token_hash)

Índices: `ix_sessions_user`

## `users`

| Coluna | Tipo | Obrigatória | Padrão | Chave |
|---|---|---|---|---|
| id | INTEGER | sim |  | PK |
| company_id | INTEGER | sim |  |  |
| name | TEXT | sim |  |  |
| email | TEXT | sim |  |  |
| password_hash | TEXT | sim |  |  |
| role | TEXT | sim |  |  |
| active | INTEGER | sim | `1` |  |
| must_change_password | INTEGER | sim | `0` |  |
| created_at | TEXT | sim | `strftime('%Y-%m-%dT%H:%M:%fZ','now')` |  |
| last_login_at | TEXT | não |  |  |
| session_epoch | INTEGER | sim | `0` |  |

Chaves estrangeiras: (company_id) → companies(id)

Unicidade: (id, company_id)

Índices: `ux_users_email` (único)

## `warehouses`

| Coluna | Tipo | Obrigatória | Padrão | Chave |
|---|---|---|---|---|
| id | INTEGER | sim |  | PK |
| company_id | INTEGER | sim |  |  |
| name | TEXT | sim |  |  |
| active | INTEGER | sim | `1` |  |

Chaves estrangeiras: (company_id) → companies(id)

Unicidade: (id, company_id); (company_id, name)
