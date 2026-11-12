"""Empresa, perfil do usuário e histórico de auditoria."""
import json
from zoneinfo import available_timezones
from flask import Blueprint, g, jsonify, render_template, request, session
from werkzeug.security import check_password_hash
from . import accounts as acc, get_db
from .authz import company_id, perms, require_perm
from .db import transaction
from .security import login_required
from .stock import StockError, _local_bounds

bp = Blueprint("admin", __name__)

@bp.errorhandler(StockError)
def _err(e):
    return jsonify(error=e.msg, field=e.field), e.code

def _body():
    d = request.get_json(silent=True)
    if not isinstance(d, dict): raise StockError("Corpo da requisição inválido.")
    return d

@bp.get("/api/company")
@require_perm("items.view")
def company_get():
    r = get_db().execute("SELECT name, document, timezone FROM companies WHERE id=?", (company_id(),)).fetchone()
    return jsonify(dict(r))

@bp.put("/api/company")
@require_perm("settings.manage")
def company_put():
    d, db, cid = _body(), get_db(), company_id()
    with transaction(db):
        old = dict(db.execute("SELECT name, document, timezone FROM companies WHERE id=?", (cid,)).fetchone()); new = dict(old)
        if "name" in d:
            new["name"] = (d["name"] or "").strip()
            if not new["name"] or len(new["name"]) > 120: raise StockError("Informe o nome da empresa (até 120 caracteres).", field="name")
        if "document" in d:
            new["document"] = (d["document"] or "").strip() or None
            if new["document"] and len(new["document"]) > 30: raise StockError("Documento com até 30 caracteres.", field="document")
        if "timezone" in d:
            if d["timezone"] not in available_timezones(): raise StockError("Fuso horário inválido.", field="timezone")
            new["timezone"] = d["timezone"]
        db.execute("UPDATE companies SET name=?, document=?, timezone=? WHERE id=?", (new["name"], new["document"], new["timezone"], cid))
        acc.audit(db, cid, g.user["id"], "company.updated", "company", cid, {"before": old, "after": new}, request.remote_addr)
    return jsonify(new)

# ---- perfil ----
@bp.get("/api/profile")
@login_required
def profile_get():
    u = g.user; r = get_db().execute("SELECT last_login_at FROM users WHERE id=?", (u["id"],)).fetchone()
    return jsonify(name=u["name"], email=u["email"], role=u["role"], company=u["company_name"], last_login_at=r[0])

@bp.patch("/api/profile")
@login_required
def profile_patch():
    name = (_body().get("name") or "").strip()
    if not name or len(name) > 120: raise StockError("Informe o nome (até 120 caracteres).", field="name")
    db = get_db()
    with transaction(db):
        db.execute("UPDATE users SET name=? WHERE id=? AND company_id=?", (name, g.user["id"], company_id()))
        acc.audit(db, company_id(), g.user["id"], "profile.updated", "user", g.user["id"], {"before": g.user["name"], "after": name}, request.remote_addr)
    return jsonify(name=name)

@bp.post("/api/profile/password")
@login_required
def password_change():
    d, db, u, ip = _body(), get_db(), g.user, request.remote_addr or "desconhecido"
    if acc.is_locked(db, u["email"].lower(), ip): return jsonify(error="Muitas tentativas. Aguarde alguns minutos."), 429
    row = db.execute("SELECT password_hash FROM users WHERE id=? AND company_id=?", (u["id"], company_id())).fetchone()
    cur, new = d.get("current_password") or "", d.get("new_password") or ""
    if not check_password_hash(row["password_hash"], cur):
        db.execute("INSERT INTO login_attempts(email,ip,success) VALUES (?,?,0)", (u["email"].lower(), ip))
        raise StockError("Senha atual incorreta.", field="current_password")
    try: acc.validate_password(new, u["email"])
    except acc.ValidationError as e: raise StockError(str(e), field="new_password") from None
    if new == cur: raise StockError("A nova senha deve ser diferente da atual.", field="new_password")
    with transaction(db):
        db.execute("UPDATE users SET password_hash=?, session_epoch=session_epoch+1, must_change_password=0 WHERE id=?", (acc.hash_password(new), u["id"]))
        db.execute("DELETE FROM login_attempts WHERE email=? AND success=0", (u["email"].lower(),))
        acc.audit(db, company_id(), u["id"], "password.changed", "user", u["id"], ip=request.remote_addr)
    session["epoch"] = db.execute("SELECT session_epoch FROM users WHERE id=?", (u["id"],)).fetchone()[0]   # mantém esta sessão; encerra as demais
    return jsonify(ok=True)

# ---- auditoria ----
SCOPED = ("movement.%", "adjustment.%", "stock.%", "item.%", "report.%")   # o que quem não é administrador pode ver

@bp.get("/api/audit")
@require_perm("audit.view")
def audit_list():
    db, cid, a = get_db(), company_id(), request.args
    w, p = ["a.company_id=?"], [cid]
    if g.user["role"] != "admin":
        w.append("(" + " OR ".join("a.action LIKE ?" for _ in SCOPED) + ")"); p += list(SCOPED)
    if a.get("action"): w.append("a.action LIKE ?"); p.append(a["action"].replace("*", "%"))
    if a.get("user_id"):
        try: w.append("a.user_id=?"); p.append(int(a["user_id"]))
        except ValueError: raise StockError("Parâmetro 'user_id' inválido.") from None
    if a.get("from"): w.append("a.created_at>=?"); p.append(_local_bounds(db, cid, a["from"], False))
    if a.get("to"): w.append("a.created_at<?"); p.append(_local_bounds(db, cid, a["to"], True))
    try: page, size = max(int(a.get("page", 1)), 1), min(max(int(a.get("size", 25)), 1), 100)
    except ValueError: raise StockError("Parâmetros de paginação inválidos.") from None
    sql = " AND ".join(w)
    total = db.execute(f"SELECT count(*) FROM audit_log a WHERE {sql}", p).fetchone()[0]
    rows = db.execute(f"SELECT a.id,a.created_at,a.action,a.entity,a.entity_id,a.details,a.ip,u.name user_name FROM audit_log a LEFT JOIN users u ON u.id=a.user_id AND u.company_id=a.company_id "
                      f"WHERE {sql} ORDER BY a.id DESC LIMIT ? OFFSET ?", p + [size, (page - 1) * size]).fetchall()
    items = [{**dict(r), "details": json.loads(r["details"]) if r["details"] else None, **({} if g.user["role"] == "admin" else {"ip": None})} for r in rows]
    return jsonify(items=items, total=total, page=page, size=size)

def _page(perm, tpl):
    @login_required
    def view():
        return render_template(tpl) if perm is None or perm in perms() else (jsonify(error="Acesso negado."), 403)
    return view
bp.add_url_rule("/configuracoes", "settings_page", _page("settings.manage", "settings.html"))
bp.add_url_rule("/perfil", "profile_page", _page(None, "profile.html"))
bp.add_url_rule("/auditoria", "audit_page", _page("audit.view", "audit.html"))
