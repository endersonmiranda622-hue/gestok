"""Regras de negócio de contas: criação de empresa/admin, autenticação, redefinição de senha."""
import hashlib, json, re, secrets, sqlite3
from datetime import datetime, timedelta, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from .db import transaction
from .permissions import DEFAULTS

LOGIN_WINDOW_MIN, MAX_FAIL_EMAIL, MAX_FAIL_IP = 15, 5, 20
RESET_TTL_MIN, MAX_RESETS_PER_HOUR = 60, 3
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
_DUMMY_HASH = generate_password_hash("gestok-dummy", method="scrypt")  # equaliza tempo p/ e-mail inexistente

class ValidationError(ValueError):
    pass

def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

def now_iso(delta_min=0):
    return iso(datetime.now(timezone.utc) + timedelta(minutes=delta_min))

def norm_email(email):
    email = (email or "").strip().lower()
    if len(email) > 254 or not _EMAIL.match(email):
        raise ValidationError("Informe um e-mail válido.")
    return email

def validate_password(pw, email=""):
    if len(pw or "") < 10 or len(pw) > 128:
        raise ValidationError("A senha deve ter de 10 a 128 caracteres.")
    if not (re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw)):
        raise ValidationError("A senha deve conter letras e números.")
    if pw.lower() == (email or "").lower():
        raise ValidationError("A senha não pode ser igual ao e-mail.")

def hash_password(pw):
    return generate_password_hash(pw, method="scrypt")

def audit(conn, company_id, user_id, action, entity=None, entity_id=None, details=None, ip=None):
    conn.execute("INSERT INTO audit_log(company_id,user_id,action,entity,entity_id,details,ip) VALUES (?,?,?,?,?,?,?)",
                 (company_id, user_id, action, entity, None if entity_id is None else str(entity_id),
                  json.dumps(details, ensure_ascii=False) if details else None, ip))

def seed_permissions(conn, company_id):
    conn.executemany("INSERT INTO role_permissions(company_id,role,permission) VALUES (?,?,?)",
                     [(company_id, r, p) for r, ps in DEFAULTS.items() for p in sorted(ps)])

def create_company_with_admin(conn, company_name, admin_name, email, password, ip=None):
    """Cria empresa + administrador + permissões padrão + unidade/depósito iniciais, atomicamente."""
    company_name, admin_name = (company_name or "").strip(), (admin_name or "").strip()
    if not company_name or not admin_name:
        raise ValidationError("Informe o nome da empresa e do administrador.")
    email = norm_email(email); validate_password(password, email)
    try:
        with transaction(conn):
            cid = conn.execute("INSERT INTO companies(name) VALUES (?)", (company_name,)).lastrowid
            uid = conn.execute("INSERT INTO users(company_id,name,email,password_hash,role) VALUES (?,?,?,?,'admin')",
                               (cid, admin_name, email, hash_password(password))).lastrowid
            seed_permissions(conn, cid)
            conn.execute("INSERT INTO units(company_id,code,name,allow_fraction) VALUES (?,?,?,0)", (cid, "UN", "Unidade"))
            conn.execute("INSERT INTO warehouses(company_id,name) VALUES (?,?)", (cid, "Depósito principal"))
            audit(conn, cid, uid, "company.created", "company", cid, {"admin_email": email}, ip)
    except sqlite3.IntegrityError as exc:
        if "users.email" in str(exc) or "ux_users_email" in str(exc):
            raise ValidationError("Este e-mail já está cadastrado.") from None
        raise
    return cid, uid

def _fail_count(conn, column, value):
    return conn.execute(f"SELECT count(*) FROM login_attempts WHERE {column}=? AND success=0 AND created_at>?",
                        (value, now_iso(-LOGIN_WINDOW_MIN))).fetchone()[0]

def is_locked(conn, email, ip):
    return _fail_count(conn, "email", email) >= MAX_FAIL_EMAIL or _fail_count(conn, "ip", ip) >= MAX_FAIL_IP

def authenticate(conn, email, password, ip):
    """Retorna (usuário|None, estado) com estado em ok | invalid | locked."""
    email = (email or "").strip().lower()[:254]
    if is_locked(conn, email, ip):
        return None, "locked"
    row = conn.execute("SELECT u.*, c.active AS company_active FROM users u JOIN companies c ON c.id=u.company_id "
                       "WHERE lower(u.email)=?", (email,)).fetchone()
    ok = check_password_hash(row["password_hash"] if row else _DUMMY_HASH, password or "")
    ok = bool(row and ok and row["active"] and row["company_active"])
    conn.execute("INSERT INTO login_attempts(email,ip,success) VALUES (?,?,?)", (email, ip, int(ok)))
    if not ok:
        if row:
            audit(conn, row["company_id"], row["id"], "login.failed", ip=ip)
        return None, "invalid"
    conn.execute("DELETE FROM login_attempts WHERE email=? AND success=0", (email,))
    conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (now_iso(), row["id"]))
    audit(conn, row["company_id"], row["id"], "login.success", ip=ip)
    return row, "ok"

def _th(token):
    return hashlib.sha256(token.encode()).hexdigest()

def issue_reset_token(conn, email, ip=None):
    """Gera token de uso único (só o hash é guardado). Retorna o token bruto ou None (sem revelar o motivo)."""
    email = (email or "").strip().lower()
    u = conn.execute("SELECT id, company_id FROM users WHERE lower(email)=? AND active=1", (email,)).fetchone()
    if not u:
        return None
    recent = conn.execute("SELECT count(*) FROM password_reset_tokens WHERE user_id=? AND created_at>?",
                          (u["id"], now_iso(-60))).fetchone()[0]
    if recent >= MAX_RESETS_PER_HOUR:
        return None
    token = secrets.token_urlsafe(32)
    with transaction(conn):
        conn.execute("INSERT INTO password_reset_tokens(user_id,token_hash,expires_at) VALUES (?,?,?)",
                     (u["id"], _th(token), now_iso(RESET_TTL_MIN)))
        audit(conn, u["company_id"], u["id"], "password.reset_requested", ip=ip)
    return token

def reset_token_valid(conn, token):
    return conn.execute("SELECT 1 FROM password_reset_tokens WHERE token_hash=? AND used_at IS NULL AND expires_at>?",
                        (_th(token or ""), now_iso())).fetchone() is not None

def reset_password(conn, token, new_password, ip=None):
    row = conn.execute("SELECT t.id, t.user_id, u.email, u.company_id FROM password_reset_tokens t "
                       "JOIN users u ON u.id=t.user_id WHERE t.token_hash=? AND t.used_at IS NULL AND t.expires_at>? AND u.active=1",
                       (_th(token or ""), now_iso())).fetchone()
    if not row:
        raise ValidationError("Link inválido ou expirado. Solicite uma nova redefinição.")
    validate_password(new_password, row["email"])
    with transaction(conn):
        conn.execute("UPDATE users SET password_hash=?, session_epoch=session_epoch+1, must_change_password=0 WHERE id=?",
                     (hash_password(new_password), row["user_id"]))
        conn.execute("UPDATE password_reset_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL", (now_iso(), row["user_id"]))
        conn.execute("DELETE FROM login_attempts WHERE email=?", (row["email"].lower(),))
        audit(conn, row["company_id"], row["user_id"], "password.reset_done", ip=ip)

def create_session(conn, user_id, ip, hours):
    """Sessão no servidor: o cookie leva um token aleatório; só o hash fica no banco."""
    conn.execute("DELETE FROM user_sessions WHERE expires_at<?", (now_iso(-60 * 24 * 7),))
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO user_sessions(token_hash,user_id,expires_at,ip) VALUES (?,?,?,?)", (_th(token), user_id, now_iso(hours * 60), ip))
    return token

def revoke_session(conn, token):
    if token:
        conn.execute("UPDATE user_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL", (now_iso(), _th(token)))
