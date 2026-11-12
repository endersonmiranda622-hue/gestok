"""Painel de controle e relatórios. Mesma fonte/critério de analytics.py (valuation, alertas, estoque parado).
DEFINIÇÕES: valor de linha = qtd x custo da linha, arredondado ao centavo POR LINHA (meia-unidade p/ cima, simétrico).
Entradas = linhas de 'entrada' + linhas de estornos de entradas (reduzem). Saídas = linhas de 'saida' + estornos de saídas (reduzem).
Saldo do período = Entradas - Saídas (valor financeiro). Transferências ficam fora; ajustes aparecem à parte (líquido, com sinal)."""
from datetime import date, datetime, timedelta, timezone
from flask import Blueprint, g, jsonify, render_template, request
from . import analytics as an, get_db
from .authz import company_id, perms, require_perm
from .numbers import fmt_money, fmt_qty
from .security import login_required
from .stock import StockError, _local_bounds

bp = Blueprint("dashboard", __name__)
LV = "((CASE WHEN l.qty_milli<0 THEN -1 ELSE 1 END) * ((2*abs(l.qty_milli)*l.unit_cost_e4 + 100000) / 200000))"   # valor da linha (centavos, com sinal)
LINE_FROM = """FROM movement_lines l JOIN stock_movements m ON m.id=l.movement_id AND m.company_id=l.company_id
 LEFT JOIN stock_movements o ON o.id=m.reverses_id AND o.company_id=m.company_id
 JOIN items i ON i.id=l.item_id AND i.company_id=l.company_id JOIN units un ON un.id=i.unit_id AND un.company_id=i.company_id
 JOIN warehouses w ON w.id=l.warehouse_id AND w.company_id=l.company_id JOIN users u ON u.id=m.user_id AND u.company_id=m.company_id
 LEFT JOIN categories c ON c.id=i.category_id AND c.company_id=i.company_id LEFT JOIN lots lo ON lo.id=l.lot_id AND lo.company_id=l.company_id
 WHERE l.company_id=?"""
ORIGIN = "COALESCE(o.kind, m.kind)"

@bp.errorhandler(StockError)
def _err(e):
    return jsonify(error=e.msg, field=e.field), e.code

def _i(a, name):
    v = a.get(name)
    if v in (None, ""): return None
    try: return int(v)
    except ValueError: raise StockError(f"Parâmetro '{name}' inválido.", field=name) from None

def _period(db, cid, a):
    today = an._today(db, cid)
    try:
        f = datetime.strptime(a["from"], "%Y-%m-%d").date() if a.get("from") else today.replace(day=1)
        t = datetime.strptime(a["to"], "%Y-%m-%d").date() if a.get("to") else today
    except ValueError: raise StockError("Data inválida (use AAAA-MM-DD).") from None
    if f > t: raise StockError("A data inicial não pode ser posterior à final.", field="from")
    if (t - f).days > 366: raise StockError("O período máximo é de 366 dias.", field="to")
    return f, t, _local_bounds(db, cid, f.isoformat(), False), _local_bounds(db, cid, t.isoformat(), True)

def bucket(db, cid, occurred_at, monthly):
    d = an._local_date(db, cid, occurred_at)
    return d.strftime("%Y-%m") if monthly else d.isoformat()

def _buckets(f, t, monthly):
    out, d = [], f.replace(day=1) if monthly else f
    while d <= t:
        out.append(d.strftime("%Y-%m") if monthly else d.isoformat())
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1) if monthly else d + timedelta(days=1)
    return out

def build_dashboard(db, cid, a):
    f, t, lo, hi = _period(db, cid, a)
    counts, _, _, today, lim = an.alert_counts(db, cid)
    val = an.valuation(db, cid)
    it = db.execute("SELECT count(*), COALESCE(sum(active),0) FROM items WHERE company_id=?", (cid,)).fetchone()
    ent = {"cents": 0, "qty": {}}; sai = {"cents": 0, "qty": {}}; aj = 0
    for r in db.execute(f"SELECT {ORIGIN} ok, un.code uc, sum(l.qty_milli) q, sum({LV}) v {LINE_FROM} AND m.occurred_at>=? AND m.occurred_at<? GROUP BY 1,2", (cid, lo, hi)):
        if r["ok"] == "entrada": ent["cents"] += r["v"]; ent["qty"][r["uc"]] = ent["qty"].get(r["uc"], 0) + r["q"]
        elif r["ok"] == "saida": sai["cents"] -= r["v"]; sai["qty"][r["uc"]] = sai["qty"].get(r["uc"], 0) - r["q"]
        elif r["ok"] == "ajuste": aj += r["v"]
    kinds = {r[0]: r[1] for r in db.execute("SELECT kind, count(*) FROM stock_movements WHERE company_id=? AND occurred_at>=? AND occurred_at<? GROUP BY 1", (cid, lo, hi))}
    monthly = (t - f).days > 92
    series = {b: {"bucket": b, "entradas_cents": 0, "saidas_cents": 0, "movements": 0} for b in _buckets(f, t, monthly)}
    for r in db.execute(f"SELECT m.id, m.occurred_at, {ORIGIN} ok, COALESCE(sum({LV}),0) v {LINE_FROM} AND m.occurred_at>=? AND m.occurred_at<? GROUP BY m.id", (cid, lo, hi)):
        s = series[bucket(db, cid, r["occurred_at"], monthly)]; s["movements"] += 1
        if r["ok"] == "entrada": s["entradas_cents"] += r["v"]
        elif r["ok"] == "saida": s["saidas_cents"] -= r["v"]
    exp = db.execute("SELECT i.code,i.name,lo.lot_code,lo.expiry_date,sum(lb.qty_milli) q FROM lot_balances lb JOIN lots lo ON lo.id=lb.lot_id AND lo.company_id=lb.company_id JOIN items i ON i.id=lo.item_id AND i.company_id=lo.company_id "
                     "WHERE lb.company_id=? AND lb.qty_milli>0 AND lo.expiry_date IS NOT NULL AND lo.expiry_date>=? AND lo.expiry_date<=? GROUP BY lo.id ORDER BY lo.expiry_date LIMIT 5", (cid, today.isoformat(), lim)).fetchall()
    m = lambda c: fmt_money(c * 100)
    return {"period": {"from": f.isoformat(), "to": t.isoformat(), "granularity": "mes" if monthly else "dia"}, "empty": it[0] == 0,
            "cards": {"total_value_cents": val["total_cents"], "total_value_display": val["total_display"], "items_total": it[0], "items_active": it[1],
                      "replenish": counts["replenish"], "zero": counts["zero"], "negative": counts["negative"], "stale_count": counts["stale"], "stale_value_cents": counts["stale_value_cents"],
                      "stale_value_display": counts["stale_value_display"], "stale_days": counts["stale_days"], "expiring": counts["expiring"], "expired": counts["expired"]},
            "period_metrics": {"entradas_cents": ent["cents"], "entradas_display": m(ent["cents"]), "saidas_cents": sai["cents"], "saidas_display": m(sai["cents"]),
                               "saldo_periodo_cents": ent["cents"] - sai["cents"], "saldo_periodo_display": m(ent["cents"] - sai["cents"]), "ajustes_cents": aj,
                               "entradas_qty_by_unit": ent["qty"], "saidas_qty_by_unit": sai["qty"], "movements_by_kind": kinds},
            "series": list(series.values()), "by_category": val["by_category"],
            "expiring_soon": [{**dict(r), "qty_display": fmt_qty(r["q"])} for r in exp],
            "definitions": {"entradas": "Linhas de entrada do período, líquidas de estornos de entradas, ao custo da linha.", "saidas": "Linhas de saída do período, líquidas de estornos de saídas, ao custo médio vigente na saída.",
                            "saldo_periodo": "Entradas - Saídas em valor financeiro. Transferências não entram; ajustes aparecem separados.",
                            "valor_estoque": "Saldos positivos x custo médio ponderado (nunca preço de venda)."}}

@bp.get("/api/dashboard")
@require_perm("dashboard.view")
def dashboard_api():
    return jsonify(build_dashboard(get_db(), company_id(), request.args))

# ---------------- relatórios ----------------
def _cols(*spec): return [{"key": k, "label": l, "type": t} for k, l, t in spec]
LINE_COLS = _cols(("occurred_at", "Data/hora", "datetime"), ("kind", "Tipo", "text"), ("item_code", "Código", "text"), ("item_name", "Item", "text"), ("category_name", "Categoria", "text"),
                  ("warehouse_name", "Depósito", "text"), ("lot_code", "Lote", "text"), ("qty_milli", "Quantidade", "qty"), ("unit_code", "Un.", "text"), ("unit_cost_e4", "Custo unit.", "e4"),
                  ("value_cents", "Valor", "cents"), ("user_name", "Usuário", "text"), ("reason", "Motivo", "text"))

def _line_filters(db, cid, a, period=True):
    w, p = "", []
    if period:
        _, _, lo, hi = _period(db, cid, a); w += " AND m.occurred_at>=? AND m.occurred_at<?"; p += [lo, hi]
    for name, col in (("item_id", "l.item_id"), ("category_id", "i.category_id"), ("warehouse_id", "l.warehouse_id"), ("user_id", "m.user_id")):
        v = _i(a, name)
        if v is not None: w += f" AND {col}=?"; p.append(v)
    if a.get("item_type"):
        if a["item_type"] not in ("material", "produto"): raise StockError("Tipo de item inválido.")
        w += " AND i.item_type=?"; p.append(a["item_type"])
    return w, p

def _paged(sql, args, page, size):
    return (sql + " LIMIT ? OFFSET ?", args + [size, (page - 1) * size]) if size else (sql, args)

def _lines(db, cid, a, page, size, origin=None, kinds=None):
    w, p = _line_filters(db, cid, a)
    if origin: w += f" AND {ORIGIN}=?"; p.append(origin)
    if kinds: w += f" AND m.kind IN ({','.join('?' * len(kinds))})"; p += kinds
    if a.get("kind") and not kinds and not origin:
        if a["kind"] not in ("entrada", "saida", "transferencia", "ajuste", "estorno"): raise StockError("Tipo de movimentação inválido.")
        w += " AND m.kind=?"; p.append(a["kind"])
    tot = db.execute(f"SELECT count(*), COALESCE(sum({LV}),0) {LINE_FROM}{w}", [cid, *p]).fetchone()
    sql, args = _paged(f"SELECT m.occurred_at, m.kind, i.code item_code, i.name item_name, c.name category_name, w.name warehouse_name, lo.lot_code, l.qty_milli, un.code unit_code, l.unit_cost_e4, {LV} value_cents, u.name user_name, m.reason, m.id movement_id, m.reverses_id "
                       f"{LINE_FROM}{w} ORDER BY m.occurred_at DESC, l.id DESC", [cid, *p], page, size)
    return {"columns": LINE_COLS, "rows": [dict(r) for r in db.execute(sql, args)], "total_rows": tot[0], "totals": {"value_cents": tot[1]}}

def r_position(db, cid, a, page, size):
    fs, fa = an._filters()
    tot = db.execute(f"SELECT count(*), COALESCE(sum({an.VALUE}),0) {an.BASE}{fs}", [cid, *fa]).fetchone()
    sql, args = _paged(f"SELECT i.code item_code, i.name item_name, c.name category_name, w.name warehouse_name, u.code unit_code, b.qty_milli, i.avg_cost_e4 unit_cost_e4, {an.VALUE} value_cents, b.last_movement_at "
                       f"{an.BASE}{fs} ORDER BY i.code, w.name", [cid, *fa], page, size)
    return {"columns": _cols(("item_code", "Código", "text"), ("item_name", "Item", "text"), ("category_name", "Categoria", "text"), ("warehouse_name", "Depósito", "text"), ("qty_milli", "Quantidade", "qty"),
                             ("unit_code", "Un.", "text"), ("unit_cost_e4", "Custo médio", "e4"), ("value_cents", "Valor", "cents"), ("last_movement_at", "Última mov.", "datetime")),
            "rows": [dict(r) for r in db.execute(sql, args)], "total_rows": tot[0], "totals": {"value_cents": tot[1]}}

def r_valuation(db, cid, a, page, size):
    v = an.valuation(db, cid, *an._filters())
    rows = [{"group": g, "name": x["name"], "value_cents": x["value_cents"], "qty_by_unit": ", ".join(f"{fmt_qty(q)} {u}" for u, q in x["qty_by_unit"].items())}
            for g, k in (("Categoria", "by_category"), ("Depósito", "by_warehouse"), ("Tipo", "by_type")) for x in v[k]]
    return {"columns": _cols(("group", "Agrupamento", "text"), ("name", "Nome", "text"), ("qty_by_unit", "Quantidade por unidade", "text"), ("value_cents", "Valor", "cents")),
            "rows": rows, "total_rows": len(rows), "totals": {"value_cents": v["total_cents"]}}

def r_stale(db, cid, a, page, size):
    fs, fa = an._filters(); r = an.stale_report(db, cid, fs, fa, page=page, size=size or 10**6)
    return {"columns": _cols(("code", "Código", "text"), ("name", "Item", "text"), ("category_name", "Categoria", "text"), ("warehouse_name", "Depósito", "text"), ("qty_milli", "Qtd parada", "qty"),
                             ("unit_code", "Un.", "text"), ("avg_cost_e4", "Custo unit.", "e4"), ("value_cents", "Valor parado", "cents"), ("last_movement_at", "Última mov.", "datetime"), ("days_stalled", "Dias", "int")),
            "rows": r["items"], "total_rows": r["count"], "totals": {"value_cents": r["value_cents"]}}

def r_replenish(db, cid, a, page, size):
    counts, tot, conds, *_ = an.alert_counts(db, cid)
    sql, args = _paged(f"SELECT code item_code, name item_name, unit_code, q qty_milli, min_qty_milli FROM ({tot}) WHERE {conds['replenish']} OR {conds['zero']} OR {conds['negative']} ORDER BY code", [cid], page, size)
    n = counts["replenish"] + counts["zero"] + counts["negative"]
    return {"columns": _cols(("item_code", "Código", "text"), ("item_name", "Item", "text"), ("qty_milli", "Saldo", "qty"), ("min_qty_milli", "Mínimo", "qty"), ("unit_code", "Un.", "text")),
            "rows": [dict(r) for r in db.execute(sql, args)], "total_rows": n, "totals": {}}

def r_lots(db, cid, a, page, size):
    today = an._today(db, cid); w, p = "", []
    for name, col in (("item_id", "lo.item_id"), ("category_id", "i.category_id"), ("warehouse_id", "lb.warehouse_id")):
        v = _i(a, name)
        if v is not None: w += f" AND {col}=?"; p.append(v)
    base = ("FROM lot_balances lb JOIN lots lo ON lo.id=lb.lot_id AND lo.company_id=lb.company_id JOIN items i ON i.id=lo.item_id AND i.company_id=lo.company_id JOIN units u ON u.id=i.unit_id AND u.company_id=i.company_id "
            f"JOIN warehouses w ON w.id=lb.warehouse_id AND w.company_id=lb.company_id LEFT JOIN categories c ON c.id=i.category_id AND c.company_id=i.company_id WHERE lb.company_id=? AND lb.qty_milli>0{w}")
    n = db.execute(f"SELECT count(*) {base}", [cid, *p]).fetchone()[0]
    sql, args = _paged(f"SELECT i.code item_code, i.name item_name, c.name category_name, lo.lot_code, lo.expiry_date, w.name warehouse_name, lb.qty_milli, u.code unit_code {base} ORDER BY lo.expiry_date IS NULL, lo.expiry_date, i.code", [cid, *p], page, size)
    rows = [dict(r) for r in db.execute(sql, args)]
    for r in rows: r["days_to_expiry"] = (date.fromisoformat(r["expiry_date"]) - today).days if r["expiry_date"] else None
    return {"columns": _cols(("item_code", "Código", "text"), ("item_name", "Item", "text"), ("lot_code", "Lote", "text"), ("expiry_date", "Validade", "date"), ("days_to_expiry", "Dias p/ vencer", "int"),
                             ("warehouse_name", "Depósito", "text"), ("qty_milli", "Quantidade", "qty"), ("unit_code", "Un.", "text")), "rows": rows, "total_rows": n, "totals": {}}

def r_by_user(db, cid, a, page, size):
    w, p = _line_filters(db, cid, a)
    sql, args = _paged(f"SELECT u.name user_name, count(DISTINCT m.id) movements, COALESCE(sum(CASE WHEN {ORIGIN}='entrada' THEN {LV} END),0) entradas_cents, -COALESCE(sum(CASE WHEN {ORIGIN}='saida' THEN {LV} END),0) saidas_cents, "
                       f"count(DISTINCT CASE WHEN m.kind='ajuste' THEN m.id END) ajustes, count(DISTINCT CASE WHEN m.kind='estorno' THEN m.id END) estornos {LINE_FROM}{w} GROUP BY m.user_id ORDER BY u.name", [cid, *p], page, size)
    rows = [dict(r) for r in db.execute(sql, args)]
    return {"columns": _cols(("user_name", "Usuário", "text"), ("movements", "Movimentações", "int"), ("entradas_cents", "Entradas (valor)", "cents"), ("saidas_cents", "Saídas (valor)", "cents"), ("ajustes", "Ajustes", "int"), ("estornos", "Estornos", "int")),
            "rows": rows, "total_rows": db.execute(f"SELECT count(DISTINCT m.user_id) {LINE_FROM}{w}", [cid, *p]).fetchone()[0], "totals": {}}

REPORTS = {
    "position": ("Posição atual do estoque", r_position), "entries": ("Entradas", lambda *x: _lines(*x[:2], x[2], x[3], x[4], origin="entrada")),
    "exits": ("Saídas", lambda *x: _lines(*x[:2], x[2], x[3], x[4], origin="saida")), "history": ("Histórico de movimentações", lambda *x: _lines(*x[:2], x[2], x[3], x[4])),
    "stale": ("Estoque parado", r_stale), "valuation": ("Valor financeiro do estoque", r_valuation), "replenish": ("Alertas de reposição", r_replenish),
    "lots": ("Lotes e validades", r_lots), "by_user": ("Movimentações por usuário", r_by_user),
    "adjustments": ("Ajustes e estornos", lambda *x: _lines(*x[:2], x[2], x[3], x[4], kinds=["ajuste", "estorno"]))}

def run_report(db, cid, name, a, page=1, size=20):
    if name not in REPORTS: raise StockError("Relatório não encontrado.", 404)
    r = REPORTS[name][1](db, cid, a, page, size); r.update(name=name, title=REPORTS[name][0], page=page, size=size); return r

@bp.get("/api/reports")
@require_perm("reports.view")
def list_reports():
    return jsonify(items=[{"name": k, "title": v[0]} for k, v in REPORTS.items()])

@bp.get("/api/reports/<name>")
@require_perm("reports.view")
def report_api(name):
    try: page, size = max(int(request.args.get("page", 1)), 1), min(max(int(request.args.get("size", 20)), 1), 200)
    except ValueError: raise StockError("Parâmetros de paginação inválidos.") from None
    return jsonify(run_report(get_db(), company_id(), name, request.args, page, size))

@bp.get("/relatorios")
@login_required
def reports_page():
    if "reports.view" not in perms(): return jsonify(error="Acesso negado."), 403
    return render_template("reports.html")
