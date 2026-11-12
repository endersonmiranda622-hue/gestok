-- GESTOK 001: esquema inicial.
-- Convenções: quantidades em milésimos (INTEGER, 3 casas); custos/preços em 1/10000 de R$ (INTEGER, 4 casas);
-- datas em UTC ISO-8601. FKs compostas (id, company_id) impedem referências entre empresas no próprio banco.
CREATE TABLE companies(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, document TEXT,
  timezone TEXT NOT NULL DEFAULT 'America/Belem',
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));

CREATE TABLE users(
  id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
  name TEXT NOT NULL, email TEXT NOT NULL, password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','gestor','colaborador')),
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  must_change_password INTEGER NOT NULL DEFAULT 0 CHECK(must_change_password IN (0,1)),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  last_login_at TEXT, UNIQUE(id, company_id));
CREATE UNIQUE INDEX ux_users_email ON users(lower(email));

CREATE TABLE role_permissions(
  company_id INTEGER NOT NULL REFERENCES companies(id), role TEXT NOT NULL
  CHECK(role IN ('admin','gestor','colaborador')), permission TEXT NOT NULL,
  PRIMARY KEY(company_id, role, permission));

CREATE TABLE login_attempts(
  id INTEGER PRIMARY KEY, email TEXT NOT NULL, ip TEXT NOT NULL,
  success INTEGER NOT NULL CHECK(success IN (0,1)),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
CREATE INDEX ix_login_attempts ON login_attempts(email, created_at);
CREATE INDEX ix_login_attempts_ip ON login_attempts(ip, created_at);

CREATE TABLE password_reset_tokens(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  token_hash TEXT NOT NULL UNIQUE, expires_at TEXT NOT NULL, used_at TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));

CREATE TABLE settings(
  company_id INTEGER NOT NULL REFERENCES companies(id), key TEXT NOT NULL, value TEXT NOT NULL,
  PRIMARY KEY(company_id, key));

CREATE TABLE units(
  id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
  code TEXT NOT NULL, name TEXT NOT NULL,
  allow_fraction INTEGER NOT NULL DEFAULT 0 CHECK(allow_fraction IN (0,1)),
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  UNIQUE(company_id, code), UNIQUE(id, company_id));
CREATE TABLE categories(
  id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
  name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  UNIQUE(company_id, name), UNIQUE(id, company_id));
CREATE TABLE warehouses(
  id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
  name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  UNIQUE(company_id, name), UNIQUE(id, company_id));

CREATE TABLE items(
  id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
  code TEXT NOT NULL, barcode TEXT, name TEXT NOT NULL, description TEXT,
  item_type TEXT NOT NULL CHECK(item_type IN ('material','produto')),
  category_id INTEGER, unit_id INTEGER NOT NULL, default_warehouse_id INTEGER,
  min_qty_milli INTEGER NOT NULL DEFAULT 0 CHECK(min_qty_milli >= 0),
  avg_cost_e4 INTEGER NOT NULL DEFAULT 0 CHECK(avg_cost_e4 >= 0),
  sale_price_e4 INTEGER CHECK(sale_price_e4 IS NULL OR sale_price_e4 >= 0),
  tracks_lot INTEGER NOT NULL DEFAULT 0 CHECK(tracks_lot IN (0,1)),
  tracks_expiry INTEGER NOT NULL DEFAULT 0 CHECK(tracks_expiry IN (0,1)),
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  created_by INTEGER, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT,
  UNIQUE(company_id, code), UNIQUE(id, company_id),
  FOREIGN KEY(category_id, company_id) REFERENCES categories(id, company_id),
  FOREIGN KEY(unit_id, company_id) REFERENCES units(id, company_id),
  FOREIGN KEY(default_warehouse_id, company_id) REFERENCES warehouses(id, company_id),
  FOREIGN KEY(created_by, company_id) REFERENCES users(id, company_id));
CREATE UNIQUE INDEX ux_items_barcode ON items(company_id, barcode) WHERE barcode IS NOT NULL;
CREATE INDEX ix_items_name ON items(company_id, name);

CREATE TABLE lots(
  id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, item_id INTEGER NOT NULL,
  lot_code TEXT NOT NULL, expiry_date TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE(company_id, item_id, lot_code), UNIQUE(id, company_id),
  FOREIGN KEY(item_id, company_id) REFERENCES items(id, company_id));

CREATE TABLE stock_movements(
  id TEXT PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
  kind TEXT NOT NULL CHECK(kind IN ('entrada','saida','transferencia','ajuste','estorno')),
  user_id INTEGER NOT NULL, reason TEXT, reverses_id TEXT UNIQUE, idempotency_key TEXT,
  occurred_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE(id, company_id), UNIQUE(company_id, idempotency_key),
  FOREIGN KEY(user_id, company_id) REFERENCES users(id, company_id),
  FOREIGN KEY(reverses_id, company_id) REFERENCES stock_movements(id, company_id));
CREATE INDEX ix_mov_company_date ON stock_movements(company_id, occurred_at);

CREATE TABLE movement_lines(
  id INTEGER PRIMARY KEY, movement_id TEXT NOT NULL, company_id INTEGER NOT NULL,
  item_id INTEGER NOT NULL, warehouse_id INTEGER NOT NULL, lot_id INTEGER,
  qty_milli INTEGER NOT NULL CHECK(qty_milli <> 0),      -- com sinal: + entra, - sai
  unit_cost_e4 INTEGER NOT NULL CHECK(unit_cost_e4 >= 0),
  FOREIGN KEY(movement_id, company_id) REFERENCES stock_movements(id, company_id),
  FOREIGN KEY(item_id, company_id) REFERENCES items(id, company_id),
  FOREIGN KEY(warehouse_id, company_id) REFERENCES warehouses(id, company_id),
  FOREIGN KEY(lot_id, company_id) REFERENCES lots(id, company_id));
CREATE INDEX ix_lines_item ON movement_lines(company_id, item_id, warehouse_id);
CREATE INDEX ix_lines_mov ON movement_lines(movement_id);

CREATE TABLE stock_balances(
  company_id INTEGER NOT NULL, item_id INTEGER NOT NULL, warehouse_id INTEGER NOT NULL,
  qty_milli INTEGER NOT NULL DEFAULT 0, last_movement_at TEXT,
  PRIMARY KEY(company_id, item_id, warehouse_id),
  FOREIGN KEY(item_id, company_id) REFERENCES items(id, company_id),
  FOREIGN KEY(warehouse_id, company_id) REFERENCES warehouses(id, company_id));

CREATE TABLE lot_balances(
  company_id INTEGER NOT NULL, lot_id INTEGER NOT NULL, warehouse_id INTEGER NOT NULL,
  qty_milli INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(company_id, lot_id, warehouse_id),
  FOREIGN KEY(lot_id, company_id) REFERENCES lots(id, company_id),
  FOREIGN KEY(warehouse_id, company_id) REFERENCES warehouses(id, company_id));

CREATE TABLE audit_log(
  id INTEGER PRIMARY KEY, company_id INTEGER REFERENCES companies(id), user_id INTEGER REFERENCES users(id),
  action TEXT NOT NULL, entity TEXT, entity_id TEXT, details TEXT, ip TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
CREATE INDEX ix_audit_company ON audit_log(company_id, created_at);

-- Histórico imutável: correções só por estorno/ajuste (novos registros).
CREATE TRIGGER trg_mov_no_update BEFORE UPDATE ON stock_movements BEGIN SELECT RAISE(ABORT,'stock_movements e imutavel'); END;
CREATE TRIGGER trg_mov_no_delete BEFORE DELETE ON stock_movements BEGIN SELECT RAISE(ABORT,'stock_movements e imutavel'); END;
CREATE TRIGGER trg_line_no_update BEFORE UPDATE ON movement_lines BEGIN SELECT RAISE(ABORT,'movement_lines e imutavel'); END;
CREATE TRIGGER trg_line_no_delete BEFORE DELETE ON movement_lines BEGIN SELECT RAISE(ABORT,'movement_lines e imutavel'); END;
CREATE TRIGGER trg_audit_no_update BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT,'audit_log e imutavel'); END;
CREATE TRIGGER trg_audit_no_delete BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT,'audit_log e imutavel'); END;
