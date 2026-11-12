-- Idempotência verificável (hash do pedido) e fluxo de solicitação/aprovação de ajustes.
ALTER TABLE stock_movements ADD COLUMN request_hash TEXT;

CREATE TABLE adjustment_requests(
  id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
  requested_by INTEGER NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('ajuste','estorno')),
  payload TEXT NOT NULL, reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pendente' CHECK(status IN ('pendente','aprovada','rejeitada')),
  decided_by INTEGER, decided_at TEXT, decision_note TEXT, movement_id TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  FOREIGN KEY(requested_by, company_id) REFERENCES users(id, company_id),
  FOREIGN KEY(decided_by, company_id) REFERENCES users(id, company_id),
  FOREIGN KEY(movement_id, company_id) REFERENCES stock_movements(id, company_id));
CREATE INDEX ix_adjreq_company ON adjustment_requests(company_id, status, created_at);
