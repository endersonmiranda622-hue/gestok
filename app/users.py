"""Gestão de usuários e permissões. Toda consulta é filtrada pela empresa da sessão."""
import secrets
from flask import Blueprint, g, jsonify, render_template, request
from . import accounts as acc, get_db
from .authz import company_id, perms, require_admin, require_perm
from .db import transaction
from .permissions import PERMS
from .security import login_required

bp = Blueprint("users", __name__)
ROLES = ("admin", "gestor", "colaborador")
COLS = "id,name,email,role,active,last_login_at,created_at"

def _err(msg, code=400):
    return jsonify(error=msg), code

def _target(db, uid):
    return db.execute(f"SELECT {COLS} FROM users WHERE id=? AND company_id=?", (uid, company_id())).fetchone()

def _may_touch_admin():
    return g.user["role"] == "admin"

@bp.get("/usuarios")
@login_required
def page():
    if "users.manage" not in perms():
        return _err("Você não tem permissão para esta página.", 403)
    return render_template("users.html")

@bp.get("/api/users")
@require_perm("users.manage")
def list_users():
    q = (request.args.get("q") or "").strip().lower()
    try:
        page = max(int(request.args.get("page", 1)), 1)
        size = min(max(int(request.args.get("size", 20)), 1), 100)
    except ValueError:
        return _err("Parâmetros de paginação inválidos.")
    like = f"%{q}%"
    where, args = "company_id=? AND (lower(name) LIKE ? OR lower(email) LIKE ?)", (company_id(), like, like)
    db = get_db()
    total = db.execute(f"SELECT count(*) FROM users WHERE {where}", args).fetchone()[0]
    rows = db.execute(f"SELECT {COLS} FROM users WHERE {where} ORDER BY name LIMIT ? OFFSET ?", args + (size, (page - 1) * size)).fetchall()
    return jsonify(items=[dict(r) for r in rows], total=total, page=page, size=size)

@bp.get("/api/users/<int:uid>")
@require_perm("users.manage")
def get_user(uid):
    t = _target(get_db(), uid)
    return jsonify(dict(t)) if t else _err("Usuário não encontrado.", 404)

@bp.post("/api/users")
@require_perm("users.manage")
def create_user():
    d = request.get_json(silent=True) or {}
    role, name = d.get("role"), (d.get("name") or "").strip()
    if role not in ROLES: return _err("Função inválida.")
    if not name or len(name) > 120: return _err("Informe o nome (até 120 caracteres).")
    if role == "admin" and not _may_touch_admin(): return _deny_admin()
    try:
        email = acc.norm_email(d.get("email"))
    except acc.ValidationError as e:
        return _err(str(e))
    db = get_db()
    try:
        with transaction(db):  # company_id vem da sessão; qualquer company_id no corpo é ignorado
            uid = db.execute("INSERT INTO users(company_id,name,email,password_hash,role) VALUES (?,?,?,?,?)",
                             (company_id(), name, email, acc.hash_password(secrets.token_urlsafe(24)), role)).lastrowid
            acc.audit(db, company_id(), g.user["id"], "user.created", "user", uid, {"email": email, "role": role}, request.remote_addr)
    except Exception as exc:
        if "ux_users_email" in str(exc) or "users.email" in str(exc):
            return _err("Este e-mail já está cadastrado.", 409)
        raise
    token = acc.issue_reset_token(db, email, request.remote_addr)  # convite: o usuário define a própria senha
    return jsonify(user=dict(_target(db, uid)), reset_path=f"/reset/{token}"), 201

def _deny_admin():
    acc.audit(get_db(), company_id(), g.user["id"], "access.denied", details={"path": request.path, "need": "role:admin"}, ip=request.remote_addr)
    return _err("Somente administradores podem gerenciar administradores.", 403)

@bp.patch("/api/users/<int:uid>")
@require_perm("users.manage")
def update_user(uid):
    d = request.get_json(silent=True) or {}
    db, me = get_db(), g.user
    try:
        with transaction(db):  # serializa para proteger a regra do "último administrador"
            t = _target(db, uid)
            if not t: raise LookupError
            new = {"name": (d.get("name") or t["name"]).strip(), "role": d.get("role", t["role"]),
                   "active": int(bool(d["active"])) if "active" in d else t["active"]}
            if new["role"] not in ROLES or not new["name"] or len(new["name"]) > 120:
                raise ValueError("Dados inválidos.")
            if (t["role"] == "admin" or new["role"] == "admin") and not _may_touch_admin():
                raise PermissionError
            if uid == me["id"] and (new["role"] != t["role"] or not new["active"]):
                raise ValueError("Você não pode alterar sua própria função nem se desativar.")
            if t["role"] == "admin" and t["active"] and (new["role"] != "admin" or not new["active"]):
                others = db.execute("SELECT count(*) FROM users WHERE company_id=? AND role='admin' AND active=1 AND id<>?", (company_id(), uid)).fetchone()[0]
                if others == 0: raise ValueError("A empresa precisa manter pelo menos um administrador ativo.")
            bump = ", session_epoch=session_epoch+1" if (not new["active"] or new["role"] != t["role"]) else ""
            db.execute(f"UPDATE users SET name=?, role=?, active=?{bump} WHERE id=? AND company_id=?", (new["name"], new["role"], new["active"], uid, company_id()))
            acc.audit(db, company_id(), me["id"], "user.updated", "user", uid,
                      {"before": {k: t[k] for k in ("name", "role", "active")}, "after": new}, request.remote_addr)
    except LookupError:
        return _err("Usuário não encontrado.", 404)
    except PermissionError:
        return _deny_admin()
    except ValueError as e:
        return _err(str(e))
    return jsonify(dict(_target(db, uid)))

@bp.post("/api/users/<int:uid>/reset-link")
@require_perm("users.manage")
def user_reset_link(uid):
    db = get_db(); t = _target(db, uid)
    if not t: return _err("Usuário não encontrado.", 404)
    if t["role"] == "admin" and not _may_touch_admin(): return _deny_admin()
    token = acc.issue_reset_token(db, t["email"], request.remote_addr)
    if not token: return _err("Não foi possível gerar o link agora (usuário inativo ou limite de pedidos).", 429)
    acc.audit(db, company_id(), g.user["id"], "user.reset_link_issued", "user", uid, ip=request.remote_addr)
    return jsonify(reset_path=f"/reset/{token}")

@bp.get("/api/permissions")
@require_admin
def get_permissions():
    rows = get_db().execute("SELECT role, permission FROM role_permissions WHERE company_id=? ORDER BY permission", (company_id(),)).fetchall()
    out = {r: [] for r in ROLES}
    for r in rows: out[r["role"]].append(r["permission"])
    return jsonify(catalog=PERMS, roles=out)

@bp.put("/api/permissions/<role>")
@require_admin
def put_permissions(role):
    if role not in ("gestor", "colaborador"):
        return _err("Somente as funções gestor e colaborador são configuráveis; o administrador é fixo.")
    new = (request.get_json(silent=True) or {}).get("permissions")
    if not isinstance(new, list) or any(p not in PERMS for p in new):
        return _err("Lista de permissões inválida.")
    new, db = set(new), get_db()
    with transaction(db):
        old = {r[0] for r in db.execute("SELECT permission FROM role_permissions WHERE company_id=? AND role=?", (company_id(), role))}
        db.execute("DELETE FROM role_permissions WHERE company_id=? AND role=?", (company_id(), role))
        db.executemany("INSERT INTO role_permissions(company_id,role,permission) VALUES (?,?,?)", [(company_id(), role, p) for p in sorted(new)])
        acc.audit(db, company_id(), g.user["id"], "permissions.updated", "role", role,
                  {"added": sorted(new - old), "removed": sorted(old - new)}, request.remote_addr)
    return jsonify(role=role, permissions=sorted(new))
