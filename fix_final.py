import pathlib
pathlib.Path("vercel.json").write_text('{"rewrites": [{"source": "/(.*)", "destination": "/api/index"}]}')
pathlib.Path("requirements.txt").write_text("Flask\n")
code='''from flask import Flask, request, redirect, session, render_template_string
app = Flask(__name__)
app.config["SECRET_KEY"] = "gestok-2024"
HTML = "<html><head><title>Gestok</title><style>body{background:#0f0f0f;color:#fff;font-family:Arial;display:flex;justify-content:center;align-items:center;height:100vh} .box{background:#1e1e1e;padding:40px;border-radius:16px;width:320px;border:1px solid #333} h1{color:#00ff88;text-align:center} input{width:100%;padding:12px;margin:6px 0;background:#2a2a2a;border:1px solid #333;color:#fff;border-radius:8px} button{width:100%;padding:12px;background:#00ff88;border:none;border-radius:8px;font-weight:bold;margin-top:10px}</style></head><body><div class=box><h1>Gestok</h1><form method=POST><input name=email value=admin@gestok.com><input name=password type=password value=admin123><button>Entrar</button></form></div></body></html>"
@app.route("/")
def index():
    if "user" not in session: return redirect("/auth/login")
    return f"<h1 style=color:#00ff88>GESTOK OK!</h1><p>{session.get('user')}</p><a href=/auth/logout>Sair</a>"
@app.route("/auth/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        session["user"]=request.form.get("email")
        return redirect("/")
    return render_template_string(HTML)
@app.route("/auth/logout")
def logout():
    session.clear()
    return redirect("/auth/login")
@app.route("/health")
def health():
    return {"status":"ok"}
'''
pathlib.Path("api/index.py").write_text(code, encoding="utf-8")
print("TUDO CONSERTADO!")