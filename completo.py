import pathlib
code = '''
from flask import Flask, request, redirect, session, render_template_string
app = Flask(__name__)
app.config["SECRET_KEY"] = "gestok-2024-full"

# Banco em memoria
DB = {"produtos": [{"id":1,"nome":"Arroz 5kg","estoque":50,"preco":25.9},{"id":2,"nome":"Feijao 1kg","estoque":30,"preco":8.5}], "vendas": [], "clientes": []}

LAYOUT = """
<!DOCTYPE html><html><head><title>Gestok</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0f0f0f;color:#fff;font-family:Arial;display:flex;min-height:100vh}
.sidebar{width:220px;background:#1a1a1a;border-right:1px solid #333;padding:20px}
.sidebar h2{color:#00ff88;margin-bottom:30px}
.sidebar a{display:block;color:#aaa;text-decoration:none;padding:12px;border-radius:8px;margin-bottom:8px}
.sidebar a.active,.sidebar a:hover{background:#2a2a2a;color:#00ff88}
.main{flex:1;padding:30px}
.card{background:#1e1e1e;border:1px solid #333;border-radius:12px;padding:20px;margin-bottom:20px}
h1{color:#00ff88;margin-bottom:20px}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:15px;margin-bottom:20px}
.stat{background:#1e1e1e;border:1px solid #333;padding:20px;border-radius:12px;text-align:center}
.stat b{font-size:24px;color:#00ff88;display:block}
table{width:100%;border-collapse:collapse}
th,td{padding:12px;text-align:left;border-bottom:1px solid #333}
th{color:#00ff88}
.btn{background:#00ff88;color:#000;border:none;padding:10px 20px;border-radius:8px;font-weight:bold;cursor:pointer;text-decoration:none;display:inline-block}
input,select{padding:10px;background:#2a2a2a;border:1px solid #333;color:#fff;border-radius:8px;width:100%;margin:5px 0}
.form-row{display:flex;gap:10px}
</style></head><body>
<div class=sidebar><h2>Gestok</h2>
<a href="/" class="{{'active' if page=='dash' else ''}}">Dashboard</a>
<a href="/produtos" class="{{'active' if page=='prod' else ''}}">Produtos</a>
<a href="/vendas" class="{{'active' if page=='venda' else ''}}">Vendas</a>
<a href="/clientes" class="{{'active' if page=='cli' else ''}}">Clientes</a>
<a href="/auth/logout">Sair ({{user}})</a>
</div><div class=main>{{content|safe}}</div></body></html>
"""

LOGIN_HTML = """<html><head><title>Gestok Login</title><style>body{background:#0f0f0f;color:#fff;font-family:Arial;display:flex;justify-content:center;align-items:center;height:100vh} .box{background:#1e1e1e;padding:40px;border-radius:16px;width:340px;border:1px solid #333} h1{color:#00ff88;text-align:center;margin-bottom:20px} input{width:100%;padding:12px;margin:8px 0;background:#2a2a2a;border:1px solid #333;color:#fff;border-radius:8px} button{width:100%;padding:12px;background:#00ff88;border:none;border-radius:8px;font-weight:bold;margin-top:10px;cursor:pointer}</style></head><body><div class=box><h1>Gestok</h1><form method=POST><input name=email placeholder=Email value=admin@gestok.com><input name=password type=password placeholder=Senha value=admin123><button>Entrar</button></form></div></body></html>"""

def render(page, content):
    return render_template_string(LAYOUT, page=page, user=session.get("user",""), content=content)

@app.route("/")
def index():
    if "user" not in session: return redirect("/auth/login")
    total_estoque = sum(p["estoque"] for p in DB["produtos"])
    total_vendas = len(DB["vendas"])
    valor_estoque = sum(p["estoque"]*p["preco"] for p in DB["produtos"])
    content = f"""
    <h1>Dashboard</h1>
    <div class=stats>
        <div class=stat><b>{len(DB["produtos"])}</b>Produtos</div>
        <div class=stat><b>{total_estoque}</b>Itens em Estoque</div>
        <div class=stat><b>R$ {valor_estoque:.2f}</b>Valor Estoque</div>
    </div>
    <div class=card><h3>Vendas Recentes</h3><p>{total_vendas} vendas registradas</p></div>
    """
    return render("dash", content)

@app.route("/produtos", methods=["GET","POST"])
def produtos():
    if "user" not in session: return redirect("/auth/login")
    if request.method=="POST":
        DB["produtos"].append({"id":len(DB["produtos"])+1,"nome":request.form["nome"],"estoque":int(request.form["estoque"]),"preco":float(request.form["preco"])})
        return redirect("/produtos")
    linhas = "".join([f"<tr><td>{p['id']}</td><td>{p['nome']}</td><td>{p['estoque']}</td><td>R$ {p['preco']:.2f}</td></tr>" for p in DB["produtos"]])
    content = f"""
    <h1>Produtos</h1>
    <div class=card><form method=POST><div class=form-row><input name=nome placeholder="Nome do produto" required><input name=estoque type=number placeholder=Estoque required><input name=preco type=number step=0.01 placeholder=Preco required></div><button class=btn>Cadastrar</button></form></div>
    <div class=card><table><tr><th>ID</th><th>Nome</th><th>Estoque</th><th>Preco</th></tr>{linhas}</table></div>
    """
    return render("prod", content)

@app.route("/vendas")
def vendas():
    if "user" not in session: return redirect("/auth/login")
    content = "<h1>Vendas</h1><div class=card><p>Sistema de vendas - em breve PDV completo</p><p>Total de produtos: %d</p></div>" % len(DB["produtos"])
    return render("venda", content)

@app.route("/clientes", methods=["GET","POST"])
def clientes():
    if "user" not in session: return redirect("/auth/login")
    if request.method=="POST":
        DB["clientes"].append({"nome":request.form["nome"],"tel":request.form["tel"]})
        return redirect("/clientes")
    linhas = "".join([f"<tr><td>{c['nome']}</td><td>{c['tel']}</td></tr>" for c in DB["clientes"]])
    content = f"""
    <h1>Clientes</h1>
    <div class=card><form method=POST><div class=form-row><input name=nome placeholder=Nome required><input name=tel placeholder=Telefone></div><button class=btn>Cadastrar Cliente</button></form></div>
    <div class=card><table><tr><th>Nome</th><th>Telefone</th></tr>{linhas}</table></div>
    """
    return render("cli", content)

@app.route("/auth/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        session["user"]=request.form.get("email")
        return redirect("/")
    return render_template_string(LOGIN_HTML)

@app.route("/auth/logout")
def logout():
    session.clear()
    return redirect("/auth/login")

@app.route("/health")
def health():
    return {"status":"ok","produtos":len(DB["produtos"])}
'''
pathlib.Path("api/index.py").write_text(code, encoding="utf-8")
print("SISTEMA COMPLETO GRAVADO!")