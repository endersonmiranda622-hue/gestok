from flask import Blueprint, request, redirect, url_for, session
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from werkzeug.security import check_password_hash

bp = Blueprint("auth", __name__)
auth_bp = bp

def get_db():
    url = os.getenv("DATABASE_URL")
    if not url:
        print("DATABASE_URL NAO ENCONTRADA")
        return None
    try:
        return psycopg2.connect(url, cursor_factory=RealDictCursor)
    except Exception as e:
        print(f"ERRO CONEXAO: {e}")
        return None

LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head><title>Gestok - Login</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font-family:Arial;display:flex;justify-content:center;align-items:center;height:100vh;background:#f0f2f5;margin:0}
.box{background:white;padding:40px;border-radius:8px;box-shadow:0 2px 10px rgba(0,0,0,0.1);width:340px}
input{width:100%;padding:12px;margin:10px 0;border:1px solid #ddd;border-radius:6px;box-sizing:border-box}
button{width:100%;padding:12px;background:#1877f2;color:white;border:none;border-radius:6px;cursor:pointer;font-size:16px;font-weight:bold}
.error{color:#b00020;background:#ffe0e0;padding:10px;border-radius:6px;margin-bottom:10px;text-align:center;font-size:14px}
h2{text-align:center;margin:0 0 20px 0;color:#1877f2}
a{color:#1877f2;text-decoration:none}
.info{color:#666;font-size:12px;text-align:center;margin-top:10px}
</style>
</head>
<body>
<div class="box">
<h2>GESTOK</h2>
{ERROR}
<form method="POST">
<input type="email" name="email" placeholder="Email" required value="{EMAIL}">
<input type="password" name="password" placeholder="Senha" required>
<button type="submit">Entrar</button>
</form>
<p style="text-align:center;margin-top:15px;font-size:14px"><a href="/rotas">Rotas</a> | <a href="/health">Health</a></p>
<div class="info">Conectado ao NEON - Login Real</div>
</div>
</body>
</html>
"""

@bp.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    email_val = ""
    if request.method == "POST":
        email_val = request.form.get("email","").strip()
        senha = request.form.get("password","")
        conn = get_db()
        if not conn:
            error = '<div class="error">ERRO: DATABASE_URL nao configurada!</div>'
        else:
            try:
                cur = conn.cursor()
                cur.execute("SELECT id, email, senha_hash, password_hash, senha FROM usuarios WHERE email = %s LIMIT 1", (email_val,))
                user = cur.fetchone()
                cur.close()
                conn.close()
                if not user:
                    error = '<div class="error">Usuario nao encontrado no NEON</div>'
                else:
                    hash_db = user.get("senha_hash") or user.get("password_hash") or user.get("senha") or ""
                    ok = False
                    try:
                        if hash_db and "$" in hash_db and len(hash_db) > 20:
                            ok = check_password_hash(hash_db, senha)
                        else:
                            ok = (hash_db == senha)
                    except:
                        ok = (hash_db == senha)
                    if ok:
                        session["uid"] = user["id"]
                        session["email"] = user["email"]
                        return redirect("/")
                    else:
                        error = '<div class="error">Senha incorreta</div>'
            except Exception as e:
                error = f'<div class="error">Erro DB: {str(e)[:200]}</div>'
    return LOGIN_HTML.replace("{ERROR}", error).replace("{EMAIL}", email_val)

@bp.route("/")
def home():
    if "uid" not in session:
        return redirect(url_for("auth.login"))
    return f'<h1>Bem vindo {session.get("email","")}!</h1><p>Login OK NEON! UID={session.get("uid")}</p><a href="/rotas">Rotas</a> | <a href="/logout">Sair</a>'

@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))

@bp.route("/forgot")
def forgot():
    return '<h1>Recuperar senha</h1><p>Em breve</p><a href="/login">Voltar</a>'

@bp.route("/reset/<token>")
def reset(token):
    return f'<h1>Reset: {token}</h1><a href="/login">Voltar</a>'