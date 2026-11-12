-- Sessões revogáveis no servidor: logout/expiração/inatividade invalidam de fato um cookie roubado.
CREATE TABLE user_sessions(
  id INTEGER PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, user_id INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  last_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  expires_at TEXT NOT NULL, revoked_at TEXT, ip TEXT);
CREATE INDEX ix_sessions_user ON user_sessions(user_id, revoked_at);
