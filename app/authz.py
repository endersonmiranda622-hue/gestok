"""Autorização no servidor. A empresa e as permissões vêm SEMPRE da sessão/banco, nunca do cliente."""
from functools import wraps
from flask import g, jsonify, request
from . import accounts as acc

def company_id():
    return g.user["company_id"]

def perms():
    from .db import db
def get_db(): return db
    if "_perms" not in g:
        u = g.user
        g._perms = {r[0] for r in get_db().execute(
            "SELECT permission FROM role_permissions WHERE company_id=? AND role=?", (u["company_id"], u["role"]))} if u else set()
    return g._perms

def has_perm(p):
    return g.get("user") is not None and p in perms()

def _deny(reason):
    from .db import db
def get_db(): return db
    u = g.user
    acc.audit(get_db(), u["company_id"], u["id"], "access.denied", details={"path": request.path, "method": request.method, "need": reason}, ip=request.remote_addr)
    return jsonify(error="Você não tem permissão para esta operação."), 403

def require_perm(p):
    def deco(view):
        @wraps(view)
        def wrapped(*a, **kw):
            if g.user is None:
                return jsonify(error="Autenticação necessária."), 401
            if p not in perms():
                return _deny(p)
            return view(*a, **kw)
        return wrapped
    return deco

def require_admin(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if g.user is None:
            return jsonify(error="Autenticação necessária."), 401
        if g.user["role"] != "admin":
            return _deny("role:admin")
        return view(*a, **kw)
    return wrapped
