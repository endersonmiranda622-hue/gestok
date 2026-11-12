"""Estoque parado, valorização e alertas. UMA fonte de dados e UM critério de cálculo para cartões, tabelas e relatórios.
Valor (centavos) = qty_milli x custo_médio_e4 / 100000, arredondado meia-unidade p/ cima, por linha de saldo (item x depósito).
Somente saldos positivos entram. Custo de referência = custo médio ponderado do item (nunca o preço de venda)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import Blueprint, g, jsonify, render_template, request
from . import accounts as acc, get_db
from .authz import company_id, perms, require_perm
from .db import transaction
from .numbers import fmt_money, fmt_qty
from .security import login_required
from .stock import StockError, _local_bounds

bp = Blueprint("analytics", __name__)
VALUE = "((2*b.qty_milli*i.avg_cost_e4 + 100000) / 200000)"
BASE = """FROM stock_balances b JOIN items i ON i.id=b.item_id AND i.company_id=b.company_id
 JOIN units u ON u.id=i.unit_id AND u.company_id=i.company_id JOIN warehouses w ON w.id=b.warehouse_id AND w.company_id=b.company_id
 LEFT JOIN categories c ON c.id=i.category_id AND c.company_id=i.company_id WHERE b.company_id=? AND b.qty_milli>0"""
DEFAULTS = {"stale_days": 60, "expiry_alert_days": 30, "allow_negative_stock": 0, "default_page_size": 20}

@bp.errorhandler(StockError)
def _err(e):
    return jsonify(error=e.msg, field=e.field), e.code

def get_settings(db, cid):
    s = dict(DEFAULTS)
    for r in db.execute("SELECT key, value FROM settings WHERE company_id=?", (cid,)):
        if r["key"] in s:
            try: s[r["key"]] = int(r["value"])
            except ValueError: pass
    return s

def _tz(db, cid):
    return ZoneInfo(db.execute("SELECT timezone FROM companies WHERE id=?", (cid,)).fetchone()[0])

def _today(db, cid, now=None):
    return (now or datetime.now(timezone.utc)).astimezone(_tz(db, cid)).date()

def _local_date(db, cid, iso_utc):
    return datetime.strptime(iso_utc[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).astimezone(_tz(db, cid)).date()

def _int(name):
    v = request.args.get(name)
    if v in (None, ""): return None
    try: return int(v)
    except ValueError: raise StockError(f"Parâmetro '{name}' inválido.") from None

def _filters(args_ok=True):
    sql, p = "", []
    for name, col in (("category_id", "i.category_id"), ("warehouse_id", "b.warehouse_id")):
        v = _int(name)
        if v is not None: sql += f" AND {col}=?"; p.append(v)
    t = request.args.get("item_type")
    if t:
        if t not in ("material", "produto"): raise StockError("Tipo de item inválido.")
        sql += " AND i.item_type=?"; p.append(t)
    return sql, p

def _money(cents): return fmt_money(cents * 100)

def stale_report(db, cid, f_sql="", f_args=(), extra="", extra_args=(), sort="days", page=1, size=20, now=None):
    n = get_settings(db, cid)["stale_days"]; today = _today(db, cid, now)
    d0 = today - timedelta(days=n - 1)  # parado <=> data local da última movimentação <= hoje - n dias
    cutoff = acc.iso(datetime(d0.year, d0.month, d0.day, tzinfo=_tz(db, cid)).astimezone(timezone.utc))
    where = f"{BASE} AND b.last_movement_at < ?{f_sql}{extra}"; args = [cid, cutoff, *f_args, *extra_args]
    tot = db.execute(f"SELECT count(*), COALESCE(sum({VALUE}),0) {where}", args).fetchone()
    order = {"days": "b.last_movement_at ASC", "value": f"{VALUE} DESC", "name": "i.name ASC"}[sort]
    rows = db.execute(f"SELECT i.id item_id,i.code,i.name,i.item_type,c.name category_name,w.name warehouse_name,u.code unit_code,b.qty_milli,i.avg_cost_e4,"
                      f"{VALUE} value_cents,b.last_movement_at {where} ORDER BY {order}, i.code, w.name LIMIT ? OFFSET ?", args + [size, (page - 1) * size]).fetchall()
    out = []
    for r in rows:
        d = dict(r); d["days_stalled"] = (today - _local_date(db, cid, r["last_movement_at"])).days
        d.update(qty_display=fmt_qty(r["qty_milli"]), cost_display=fmt_money(r["avg_cost_e4"]), value_display=_money(r["value_cents"])); out.append(d)
    return {"stale_days": n, "count": tot[0], "value_cents": tot[1], "value_display": _money(tot[1]), "items": out}

def valuation(db, cid, f_sql="", f_args=()):
    a = [cid, *f_args]; res = {}
    res["total_cents"] = db.execute(f"SELECT COALESCE(sum({VALUE}),0) {BASE}{f_sql}", a).fetchone()[0]
    for key, expr, label in (("by_category", "COALESCE(c.name,'Sem categoria')", "name"), ("by_warehouse", "w.name", "name"), ("by_type", "i.item_type", "name")):
        vals = {r["k"]: r["v"] for r in db.execute(f"SELECT {expr} k, sum({VALUE}) v {BASE}{f_sql} GROUP BY 1", a)}
        qty = {}
        for r in db.execute(f"SELECT {expr} k, u.code uc, sum(b.qty_milli) q {BASE}{f_sql} GROUP BY 1,2", a):  # nunca soma unidades diferentes
            qty.setdefault(r["k"], {})[r["uc"]] = r["q"]
        res[key] = sorted(({"name": k, "value_cents": v, "value_display": _money(v), "qty_by_unit": qty.get(k, {})} for k, v in vals.items()), key=lambda x: -x["value_cents"])
    res["total_display"] = _money(res["total_cents"])
    return res

def alert_counts(db, cid, now=None):
    s = get_settings(db, cid); today = _today(db, cid, now)
    tot = f"SELECT i.id,i.code,i.name,i.min_qty_milli,u.code unit_code,COALESCE(sum(b.qty_milli),0) q FROM items i JOIN units u ON u.id=i.unit_id AND u.company_id=i.company_id " \
          f"LEFT JOIN stock_balances b ON b.item_id=i.id AND b.company_id=i.company_id WHERE i.company_id=? AND i.active=1 GROUP BY i.id"
    conds = {"zero": "q=0", "negative": "q<0", "replenish": "q>0 AND min_qty_milli>0 AND q<=min_qty_milli"}
    c = {k: db.execute(f"SELECT count(*) FROM ({tot}) WHERE {w}", (cid,)).fetchone()[0] for k, w in conds.items()}
    st = db.execute(f"SELECT count(*), COALESCE(sum({VALUE}),0) {BASE} AND b.last_movement_at < ?", (cid, _cutoff(db, cid, s["stale_days"], today))).fetchone()
    lim = (today + timedelta(days=s["expiry_alert_days"])).isoformat()
    ex = "FROM lot_balances lb JOIN lots lo ON lo.id=lb.lot_id AND lo.company_id=lb.company_id WHERE lb.company_id=? AND lb.qty_milli>0 AND lo.expiry_date IS NOT NULL"
    c["expiring"] = db.execute(f"SELECT count(*) {ex} AND lo.expiry_date>=? AND lo.expiry_date<=?", (cid, today.isoformat(), lim)).fetchone()[0]
    c["expired"] = db.execute(f"SELECT count(*) {ex} AND lo.expiry_date<?", (cid, today.isoformat())).fetchone()[0]
    since = acc.iso(datetime.now(timezone.utc) - timedelta(days=7))
    att = {r[0]: r[1] for r in db.execute("SELECT action, count(*) FROM audit_log WHERE company_id=? AND created_at>=? AND action IN ('movement.rejected','access.denied','stock.negative_allowed') GROUP BY 1", (cid, since))}
    return {**c, "stale": st[0], "stale_value_cents": st[1], "stale_value_display": _money(st[1]), "stale_days": s["stale_days"], "expiry_alert_days": s["expiry_alert_days"],
            "attention_7d": {"operacoes_rejeitadas": att.get("movement.rejected", 0), "acessos_negados": att.get("access.denied", 0), "estoque_negativo_autorizado": att.get("stock.negative_allowed", 0)}}, tot, conds, today, lim

def _cutoff(db, cid, n, today):
    d0 = today - timedelta(days=n - 1)
    return acc.iso(datetime(d0.year, d0.month, d0.day, tzinfo=_tz(db, cid)).astimezone(timezone.utc))

# ---------------- rotas ----------------
@bp.get("/api/settings")
@require_perm("items.view")
def read_settings():
    return jsonify(get_settings(get_db(), company_id()))

@bp.put("/api/settings")
@require_perm("settings.manage")
def write_settings():
    d = request.get_json(silent=True)
    if not isinstance(d, dict) or not d: raise StockError("Informe ao menos uma configuração.")
    rules = {"stale_days": (1, 3650), "expiry_alert_days": (0, 365), "allow_negative_stock": (0, 1), "default_page_size": (10, 100)}
    new = {}
    for k, v in d.items():
        if k not in rules: raise StockError(f"Configuração desconhecida: {k}.", field=k)
        try: iv = int(v) if not isinstance(v, bool) else int(v)
        except (TypeError, ValueError): raise StockError("Valor inválido.", field=k) from None
        if not rules[k][0] <= iv <= rules[k][1]: raise StockError(f"Valor fora do intervalo permitido ({rules[k][0]} a {rules[k][1]}).", field=k)
        new[k] = iv
    db, cid = get_db(), company_id()
    with transaction(db):
        old = get_settings(db, cid)
        for k, v in new.items():
            db.execute("INSERT INTO settings(company_id,key,value) VALUES (?,?,?) ON CONFLICT(company_id,key) DO UPDATE SET value=excluded.value", (cid, k, str(v)))
        acc.audit(db, cid, g.user["id"], "settings.updated", "settings", None, {"before": {k: old[k] for k in new}, "after": new}, request.remote_addr)
    return jsonify(get_settings(db, cid))

@bp.get("/api/stale")
@require_perm("reports.view")
def stale():
    db, cid = get_db(), company_id(); f_sql, f_args = _filters(); extra, ea = "", []
    for name, op, end in (("last_from", ">=", False), ("last_to", "<", True)):
        if request.args.get(name): extra += f" AND b.last_movement_at {op} ?"; ea.append(_local_bounds(db, cid, request.args[name], end))
    sort = request.args.get("sort", "days")
    if sort not in ("days", "value", "name"): raise StockError("Ordenação inválida.")
    try: page, size = max(int(request.args.get("page", 1)), 1), min(max(int(request.args.get("size", 20)), 1), 100)
    except ValueError: raise StockError("Parâmetros de paginação inválidos.") from None
    r = stale_report(db, cid, f_sql, f_args, extra, ea, sort, page, size); r.update(page=page, size=size)
    nh = db.execute("SELECT i.id,i.code,i.name FROM items i WHERE i.company_id=? AND i.active=1 AND NOT EXISTS (SELECT 1 FROM movement_lines l WHERE l.company_id=i.company_id AND l.item_id=i.id) ORDER BY i.code LIMIT 50", (cid,)).fetchall()
    r["no_history"] = [dict(x) for x in nh]  # sem data inventada: apenas listados
    return jsonify(r)

@bp.get("/api/valuation")
@require_perm("reports.view")
def valuation_api():
    f_sql, f_args = _filters()
    return jsonify(valuation(get_db(), company_id(), f_sql, f_args))

@bp.get("/api/alerts")
@require_perm("dashboard.view")
def alerts():
    db, cid = get_db(), company_id()
    counts, tot, conds, today, lim = alert_counts(db, cid)
    t = request.args.get("type")
    if not t: return jsonify(summary=counts)
    try: page, size = max(int(request.args.get("page", 1)), 1), min(max(int(request.args.get("size", 20)), 1), 100)
    except ValueError: raise StockError("Parâmetros de paginação inválidos.") from None
    off = (page - 1) * size
    if t in conds:
        rows = db.execute(f"SELECT id item_id,code,name,unit_code,q qty_milli,min_qty_milli FROM ({tot}) WHERE {conds[t]} ORDER BY code LIMIT ? OFFSET ?", (cid, size, off)).fetchall()
        items = [{**dict(r), "qty_display": fmt_qty(r["qty_milli"]), "min_qty_display": fmt_qty(r["min_qty_milli"])} for r in rows]
    elif t == "stale":
        items = stale_report(db, cid, page=page, size=size)["items"]
    elif t in ("expiring", "expired"):
        cond, a = ("lo.expiry_date>=? AND lo.expiry_date<=?", [today.isoformat(), lim]) if t == "expiring" else ("lo.expiry_date<?", [today.isoformat()])
        rows = db.execute(f"SELECT i.code,i.name,lo.lot_code,lo.expiry_date,w.name warehouse_name,lb.qty_milli FROM lot_balances lb JOIN lots lo ON lo.id=lb.lot_id AND lo.company_id=lb.company_id "
                          f"JOIN items i ON i.id=lo.item_id AND i.company_id=lo.company_id JOIN warehouses w ON w.id=lb.warehouse_id AND w.company_id=lb.company_id "
                          f"WHERE lb.company_id=? AND lb.qty_milli>0 AND lo.expiry_date IS NOT NULL AND {cond} ORDER BY lo.expiry_date, i.code LIMIT ? OFFSET ?", [cid, *a, size, off]).fetchall()
        items = [{**dict(r), "qty_display": fmt_qty(r["qty_milli"])} for r in rows]
    else:
        raise StockError("Tipo de alerta inválido.")
    return jsonify(type=t, total=counts[t], items=items, page=page, size=size)

@bp.get("/estoque-parado")
@login_required
def stale_page():
    if "reports.view" not in perms(): return jsonify(error="Acesso negado."), 403
    return render_template("stale.html")
