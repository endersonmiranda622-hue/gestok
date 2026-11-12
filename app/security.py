"""Sessão, CSRF e decorador de autenticação."""
import hashlib, hmac, secrets
from datetime import timedelta
from functools import wraps
from flask import current_app, g, jsonify, redirect, request, session, url_for, abort
from . import accounts as acc

def csrf_token():
    if not session.get("_csrf"):
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]

def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if g.user is None:
            if request.path.startswith("/api/"):
                return jsonify(error="Autenticação necessária."), 401
            return redirect(url_for("auth.login"))
        return view(*a, **kw)
    return wrapped

def init_security(app, get_db):
    app.permanent_session_lifetime = timedelta(hours=app.config["SESSION_ABS_HOURS"])
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def _csrf_check():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            expected = session.get("_csrf") or ""
            sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
            if not expected or not hmac.compare_digest(expected, sent):
                abort(400)

    @app.before_request
    def _load_user():
        g.user = None
        uid = session.get("uid")
        if not uid:
            return
        sid = session.get("sid")
        db = get_db()
        row = db.execute(
            "SELECT u.id,u.company_id,u.name,u.email,u.role,u.session_epoch,c.name AS company_name,s.id AS sess_id,s.last_seen_at FROM users u "
            "JOIN companies c ON c.id=u.company_id JOIN user_sessions s ON s.user_id=u.id AND s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>? AND s.last_seen_at>? "
            "WHERE u.id=? AND u.active=1 AND c.active=1",
            (hashlib.sha256((sid or "").encode()).hexdigest(), acc.now_iso(), acc.now_iso(-current_app.config["SESSION_IDLE_MINUTES"]), uid)).fetchone() if sid else None
        if row and row["last_seen_at"] < acc.now_iso(-5):   # atualiza a "última atividade" no máx. a cada 5 min
            db.execute("UPDATE user_sessions SET last_seen_at=? WHERE id=?", (acc.now_iso(), row["sess_id"]))
        if row and row["session_epoch"] == session.get("epoch"):
            g.user = {k: row[k] for k in ("id", "company_id", "name", "email", "role", "session_epoch", "company_name")}
        else:
            session.clear()  # usuário desativado ou senha alterada: sessão antiga deixa de valer
