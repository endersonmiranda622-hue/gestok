-- Permite invalidar todas as sessões de um usuário (troca/redefinição de senha, desativação).
ALTER TABLE users ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0;
CREATE INDEX ix_reset_user ON password_reset_tokens(user_id, created_at);
