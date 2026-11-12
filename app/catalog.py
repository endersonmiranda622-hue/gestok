"""Cadastros: categorias, unidades, depósitos e materiais/produtos. Sempre filtrados pela empresa da sessão."""
import re, sqlite3
from flask import Blueprint, g, jsonify, render_template, request
from . import accounts as acc, get_db
from .authz import company_id, perms, require_perm
from .db import transaction
from .numbers import NumError, fmt_money, fmt_qty, parse_money, parse_qty
from .security import login_required

bp = Blueprint("catalog", __name__)
CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._/-]{0,39}$")

class Bad(Exception):
    def __init__(self, msg, field=None, code=400):
        self.msg, self.field, self.code = msg, field, code

@bp.errorhandler(Bad)
def _bad(e):
    return jsonify(error=e.msg, field=e.field), e.code

def _body():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        raise Bad("Corpo da requisição inválido.")
    return d

def _text(d, key, label, maxlen, required=True, default=None):
    v = d.get(key, default)
    v = ("" if v is None else str(v)).strip()
    if required and not v:
        raise Bad(f"Informe {label}.", key)
    if len(v) > maxlen:
        raise Bad(f"{label[0].upper() + label[1:]} deve ter até {maxlen} caracteres.", key)
    return v or None

def _flag(d, key, default):
    return int(bool(d[key])) if key in d else default

def _page():
    try:
        return max(int(request.args.get("page", 1)), 1), min(max(int(request.args.get("size", 20)), 1), 100)
    except ValueError:
        raise Bad("Parâmetros de paginação inválidos.") from None

# ---------------- cadastros simples (categorias, depósitos, unidades) ----------------
def _register_simple(table, path, manage_perm, label):
    def rows():
        return [dict(r) for r in get_db().execute(f"SELECT * FROM {table} WHERE company_id=? ORDER BY active DESC, 2", (company_id(),))]

    def build(d, old=None):
        if table == "units":
            code = (_text(d, "code", "o código", 6, default=old and old["code"]) or "").upper()
            return {"code": code, "name": _text(d, "name", "o nome", 40, default=old and old["name"]),
                    "allow_fraction": _flag(d, "allow_fraction", old["allow_fraction"] if old else 0),
                    "active": _flag(d, "active", old["active"] if old else 1)}
        return {"name": _text(d, "name", "o nome", 80, default=old and old["name"]),
                "active": _flag(d, "active", old["active"] if old else 1)}

    def conflict(exc):
        if "UNIQUE" in str(exc):
            raise Bad(f"Já existe {label} com este nome/código.", code=409)
        raise exc

    def listing():
        return jsonify(items=rows())

    def create():
        db, v = get_db(), build(_body())
        v["active"] = 1
        cols = ",".join(v)
        try:
            with transaction(db):
                rid = db.execute(f"INSERT INTO {table}(company_id,{cols}) VALUES (?,{','.join('?' * len(v))})", (company_id(), *v.values())).lastrowid
                acc.audit(db, company_id(), g.user["id"], f"{table}.created", table, rid, v, request.remote_addr)
        except sqlite3.IntegrityError as e:
            conflict(e)
        return jsonify(id=rid, **v), 201

    def update(rid):
        db = get_db()
        try:
            with transaction(db):
                old = db.execute(f"SELECT * FROM {table} WHERE id=? AND company_id=?", (rid, company_id())).fetchone()
                if not old:
                    raise Bad("Registro não encontrado.", code=404)
                v = build(_body(), old)
                if table == "warehouses" and old["active"] and not v["active"]:
                    left = db.execute("SELECT count(*) FROM stock_balances WHERE company_id=? AND warehouse_id=? AND qty_milli<>0", (company_id(), rid)).fetchone()[0]
                    if left:
                        raise Bad("Este depósito ainda possui saldo. Transfira ou ajuste o estoque antes de desativá-lo.", code=409)
                if table == "units":
                    used = db.execute("SELECT count(*) FROM items WHERE company_id=? AND unit_id=?", (company_id(), rid)).fetchone()[0]
                    if used and (v["code"] != old["code"] or v["allow_fraction"] != old["allow_fraction"]):
                        raise Bad("Esta unidade já é usada por itens: código e fracionamento não podem mudar.", code=409)
                    if old["active"] and not v["active"] and db.execute("SELECT count(*) FROM items WHERE company_id=? AND unit_id=? AND active=1", (company_id(), rid)).fetchone()[0]:
                        raise Bad("Há itens ativos usando esta unidade.", code=409)
                sets = ",".join(f"{k}=?" for k in v)
                db.execute(f"UPDATE {table} SET {sets} WHERE id=? AND company_id=?", (*v.values(), rid, company_id()))
                acc.audit(db, company_id(), g.user["id"], f"{table}.updated", table, rid,
                          {"before": {k: old[k] for k in v}, "after": v}, request.remote_addr)
        except sqlite3.IntegrityError as e:
            conflict(e)
        return jsonify(id=rid, **v)

    bp.add_url_rule(path, f"{table}_list", require_perm("items.view")(listing), methods=["GET"])
    bp.add_url_rule(path, f"{table}_create", require_perm(manage_perm)(create), methods=["POST"])
    bp.add_url_rule(f"{path}/<int:rid>", f"{table}_update", require_perm(manage_perm)(update), methods=["PATCH"])

_register_simple("categories", "/api/categories", "categories.manage", "uma categoria")
_register_simple("warehouses", "/api/warehouses", "warehouses.manage", "um depósito")
_register_simple("units", "/api/units", "categories.manage", "uma unidade")

# ---------------- materiais e produtos ----------------
SORTS = {"code": "i.code", "name": "i.name", "qty": "qty_milli", "created": "i.created_at", "cost": "i.avg_cost_e4"}
SELECT = """SELECT i.*, c.name AS category_name, u.code AS unit_code, u.allow_fraction, w.name AS warehouse_name,
  COALESCE((SELECT sum(b.qty_milli) FROM stock_balances b WHERE b.company_id=i.company_id AND b.item_id=i.id),0) AS qty_milli
  FROM items i JOIN units u ON u.id=i.unit_id AND u.company_id=i.company_id
  LEFT JOIN categories c ON c.id=i.category_id AND c.company_id=i.company_id
  LEFT JOIN warehouses w ON w.id=i.default_warehouse_id AND w.company_id=i.company_id"""

def _present(r):
    d = dict(r)
    d.update(qty_display=fmt_qty(d["qty_milli"]), min_qty_display=fmt_qty(d["min_qty_milli"]), cost_display=fmt_money(d["avg_cost_e4"]),
             price_display=fmt_money(d["sale_price_e4"]) if d["sale_price_e4"] is not None else None)
    return d

def _like(q):
    return "%" + q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

@bp.get("/api/items")
@require_perm("items.view")
def list_items():
    page, size = _page()
    where, args = ["i.company_id=?"], [company_id()]
    q = (request.args.get("q") or "").strip()
    if q:
        where.append("(" + " OR ".join(f"lower(COALESCE({c},'')) LIKE ? ESCAPE '\\'" for c in ("i.code", "i.barcode", "i.name", "c.name")) + ")")
        args += [_like(q)] * 4
    for key, col in (("category_id", "i.category_id"), ("item_type", "i.item_type")):
        if request.args.get(key):
            where.append(f"{col}=?"); args.append(request.args[key])
    act = request.args.get("active", "1")
    if act in ("0", "1"):
        where.append("i.active=?"); args.append(int(act))
    elif act != "all":
        raise Bad("Filtro 'active' inválido.")
    sort = request.args.get("sort", "name")
    if sort not in SORTS:
        raise Bad("Ordenação inválida.")
    direction = "DESC" if request.args.get("dir") == "desc" else "ASC"
    sql = f"SELECT * FROM ({SELECT} WHERE {' AND '.join(where)}) AS t"
    if request.args.get("below_min") == "1":
        sql += " WHERE t.min_qty_milli > 0 AND t.qty_milli <= t.min_qty_milli"
    db = get_db()
    total = db.execute(f"SELECT count(*) FROM ({sql})", args).fetchone()[0]
    rows = db.execute(f"{sql} ORDER BY {SORTS[sort].replace('i.', 't.')} {direction}, t.id LIMIT ? OFFSET ?", args + [size, (page - 1) * size]).fetchall()
    return jsonify(items=[_present(r) for r in rows], total=total, page=page, size=size)

def _get(db, iid):
    r = db.execute(SELECT + " WHERE i.id=? AND i.company_id=?", (iid, company_id())).fetchone()
    if not r:
        raise Bad("Item não encontrado.", code=404)
    return r

@bp.get("/api/items/<int:iid>")
@require_perm("items.view")
def get_item(iid):
    return jsonify(_present(_get(get_db(), iid)))

def _ref(db, table, rid, field, old_val=None):
    if rid in (None, ""):
        return None
    try:
        rid = int(rid)
    except (TypeError, ValueError):
        raise Bad("Referência inválida.", field) from None
    row = db.execute(f"SELECT active FROM {table} WHERE id=? AND company_id=?", (rid, company_id())).fetchone()
    if not row or (not row["active"] and rid != old_val):
        raise Bad("Referência inválida ou inativa.", field)
    return rid

def _build(db, d, old=None):
    o = dict(old) if old else {}
    code = (_text(d, "code", "o código", 40, default=o.get("code")) or "").upper()
    if old and code != old["code"]:
        raise Bad("O código interno não pode ser alterado.", "code")
    if not CODE_RE.match(code):
        raise Bad("Código inválido: use letras, números e . _ / - (até 40).", "code")
    itype = d.get("item_type", o.get("item_type"))
    if itype not in ("material", "produto"):
        raise Bad("Tipo inválido: use material ou produto.", "item_type")
    unit_id = _ref(db, "units", d.get("unit_id", o.get("unit_id")), "unit_id", o.get("unit_id"))
    if unit_id is None:
        raise Bad("Informe a unidade de medida.", "unit_id")
    allow_frac = db.execute("SELECT allow_fraction FROM units WHERE id=? AND company_id=?", (unit_id, company_id())).fetchone()[0]
    try:
        min_milli = parse_qty(d["min_qty"], allow_frac) if "min_qty" in d else (o.get("min_qty_milli", 0))
        cost = parse_money(d["cost"]) if "cost" in d else o.get("avg_cost_e4", 0)
        price = (None if d["sale_price"] in (None, "") else parse_money(d["sale_price"])) if "sale_price" in d else o.get("sale_price_e4")
    except NumError as e:
        key = next((k for k in ("min_qty", "cost", "sale_price") if k in d), None)
        raise Bad(str(e), key) from None
    if old and not allow_frac and old["min_qty_milli"] % 1000 and "min_qty" not in d:
        raise Bad("O estoque mínimo atual é fracionado e a unidade não aceita fração. Informe um novo mínimo.", "min_qty")
    barcode = _text(d, "barcode", "o código de barras", 64, required=False, default=o.get("barcode"))
    if barcode and not re.match(r"^[A-Za-z0-9._-]+$", barcode):
        raise Bad("Código de barras deve ter apenas letras, números, ponto, hífen ou sublinhado.", "barcode")
    tl, te = _flag(d, "tracks_lot", o.get("tracks_lot", 0)), _flag(d, "tracks_expiry", o.get("tracks_expiry", 0))
    if te and not tl:
        raise Bad("Item com controle de validade precisa controlar lote.", "tracks_expiry")
    return {"code": code, "barcode": barcode, "name": _text(d, "name", "o nome", 150, default=o.get("name")),
            "description": _text(d, "description", "a descrição", 1000, required=False, default=o.get("description")),
            "item_type": itype, "category_id": _ref(db, "categories", d.get("category_id", o.get("category_id")), "category_id", o.get("category_id")),
            "unit_id": unit_id, "default_warehouse_id": _ref(db, "warehouses", d.get("default_warehouse_id", o.get("default_warehouse_id")), "default_warehouse_id", o.get("default_warehouse_id")),
            "min_qty_milli": min_milli, "avg_cost_e4": cost, "sale_price_e4": price,
            "tracks_lot": _flag(d, "tracks_lot", o.get("tracks_lot", 0)), "tracks_expiry": _flag(d, "tracks_expiry", o.get("tracks_expiry", 0)),
            "active": _flag(d, "active", o.get("active", 1))}

def _dup(e):
    m = str(e)
    if "items.code" in m or "company_id, code" in m:
        raise Bad("Já existe um item com este código.", "code", 409)
    if "ux_items_barcode" in m or "items.barcode" in m:
        raise Bad("Já existe um item com este código de barras.", "barcode", 409)
    raise Bad("Referência inválida.", code=400)

@bp.post("/api/items")
@require_perm("items.edit")
def create_item():
    db = get_db()
    try:
        with transaction(db):
            v = _build(db, _body()); v["active"] = 1
            cols = list(v) + ["company_id", "created_by"]
            iid = db.execute(f"INSERT INTO items({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", (*v.values(), company_id(), g.user["id"])).lastrowid
            acc.audit(db, company_id(), g.user["id"], "item.created", "item", iid, {"code": v["code"], "name": v["name"]}, request.remote_addr)
    except sqlite3.IntegrityError as e:
        _dup(e)
    return jsonify(_present(_get(db, iid))), 201

@bp.patch("/api/items/<int:iid>")
@require_perm("items.edit")
def update_item(iid):
    db = get_db()
    try:
        with transaction(db):
            old = _get(db, iid)
            d = _body()
            v = _build(db, d, old)
            if db.execute("SELECT 1 FROM movement_lines WHERE company_id=? AND item_id=? LIMIT 1", (company_id(), iid)).fetchone():
                for key, label in (("unit_id", "a unidade de medida"), ("tracks_lot", "o controle de lote"), ("tracks_expiry", "o controle de validade"), ("item_type", "o tipo"), ("avg_cost_e4", "o custo (calculado pelas entradas)")):
                    if v[key] != old[key]:
                        raise Bad(f"Item com movimentações: não é possível alterar {label}.", key, 409)
            changed = {k: [old[k], v[k]] for k in v if old[k] != v[k]}
            db.execute(f"UPDATE items SET {','.join(k + '=?' for k in v)}, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=? AND company_id=?",
                       (*v.values(), iid, company_id()))
            acc.audit(db, company_id(), g.user["id"], "item.updated", "item", iid, changed, request.remote_addr)
    except sqlite3.IntegrityError as e:
        _dup(e)
    return jsonify(_present(_get(db, iid)))

@bp.get("/materiais")
@login_required
def items_page():
    if "items.view" not in perms():
        return jsonify(error="Acesso negado."), 403
    return render_template("items.html")

@bp.get("/cadastros")
@login_required
def catalogs_page():
    if not ({"categories.manage", "warehouses.manage"} & perms()):
        return jsonify(error="Acesso negado."), 403
    return render_template("catalogs.html")
