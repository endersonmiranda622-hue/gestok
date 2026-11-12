import json, re
from flask import Blueprint, g, jsonify, render_template, request
from . import accounts as acc, get_db, stock
from .authz import _deny, company_id, has_perm, perms, require_perm
from .db import transaction
from .stock import StockError
from .security import login_required

bp = Blueprint("movements", __name__)

@bp.errorhandler(StockError)
def _stock_err(e):
    return jsonify(error=e.msg, field=e.field), e.code

def _body():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        raise StockError("Corpo da requisição inválido.")
    return d

def _page():
    try:
        return max(int(request.args.get("page", 1)), 1), min(max(int(request.args.get("size", 20)), 1), 100)
    except ValueError:
        raise StockError("Parâmetros de paginação inválidos.") from None

@bp.post("/api/movements")
@login_required
def create_movement():
    body = _body()
    kind = body.get("kind")
    need = "movements.create" if kind in stock.DIRECT else "adjustments.approve" if kind in ("ajuste", "estorno") else None
    if need is None:
        raise StockError("Tipo de movimentação inválido.", field="kind")
    if not has_perm(need):
        return _deny(need)
    key = request.headers.get("Idempotency-Key") or body.get("idempotency_key")
    body = {k: v for k, v in body.items() if k != "idempotency_key"}
    mov, replay = stock.post_movement(get_db(), company_id(), g.user["id"], body, key, request.remote_addr)
    resp = jsonify(mov); resp.status_code = 200 if replay else 201
    if replay:
        resp.headers["Idempotent-Replay"] = "true"
    return resp

@bp.get("/api/movements")
@login_required
def list_movements():
    p = perms()
    if "movements.view_all" not in p and "movements.view_own" not in p:
        return _deny("movements.view_all")
    page, size = _page()
    own = None if "movements.view_all" in p else g.user["id"]
    items, total = stock.list_movements(get_db(), company_id(), request.args, own, page, size)
    return jsonify(items=items, total=total, page=page, size=size)

@bp.get("/api/movements/<mid>")
@login_required
def get_movement(mid):
    p = perms()
    m = stock.get_movement(get_db(), company_id(), mid)
    if "movements.view_all" not in p and not ("movements.view_own" in p and m and m["user_id"] == g.user["id"]):
        return _deny("movements.view_all") if "movements.view_own" not in p else (jsonify(error="Movimentação não encontrada."), 404)
    return jsonify(m) if m else (jsonify(error="Movimentação não encontrada."), 404)

@bp.get("/api/stock/<int:item_id>")
@require_perm("items.view")
def item_stock(item_id):
    db, cid = get_db(), company_id()
    if not db.execute("SELECT 1 FROM items WHERE id=? AND company_id=?", (item_id, cid)).fetchone():
        return jsonify(error="Item não encontrado."), 404
    wh = [dict(r) for r in db.execute("SELECT b.warehouse_id, w.name AS warehouse_name, b.qty_milli, b.last_movement_at FROM stock_balances b JOIN warehouses w ON w.id=b.warehouse_id AND w.company_id=b.company_id WHERE b.company_id=? AND b.item_id=? ORDER BY w.name", (cid, item_id))]
    lots = [dict(r) for r in db.execute("SELECT lo.lot_code, lo.expiry_date, lb.warehouse_id, lb.qty_milli FROM lot_balances lb JOIN lots lo ON lo.id=lb.lot_id AND lo.company_id=lb.company_id WHERE lb.company_id=? AND lo.item_id=? ORDER BY lo.expiry_date, lo.lot_code", (cid, item_id))]
    return jsonify(warehouses=wh, lots=lots)

# ---- solicitações de ajuste/estorno (colaborador solicita; quem tem adjustments.approve decide) ----
@bp.post("/api/adjustment-requests")
@require_perm("adjustments.request")
def request_adjustment():
    body = _body(); db = get_db()
    if body.get("kind") not in ("ajuste", "estorno"):
        raise StockError("Solicite apenas ajuste ou estorno.", field="kind")
    body = {k: v for k, v in body.items() if k != "idempotency_key"}
    norm = stock.normalize(db, company_id(), body)  # valida agora; será validado de novo na aprovação
    with transaction(db):
        rid = db.execute("INSERT INTO adjustment_requests(company_id,requested_by,kind,payload,reason) VALUES (?,?,?,?,?)",
                         (company_id(), g.user["id"], norm["kind"], json.dumps(body, ensure_ascii=False), norm["reason"])).lastrowid
        acc.audit(db, company_id(), g.user["id"], "adjustment.requested", "adjustment_request", rid, {"kind": norm["kind"]}, request.remote_addr)
    return jsonify(id=rid, status="pendente"), 201

@bp.get("/api/adjustment-requests")
@require_perm("adjustments.request")
def list_requests():
    page, size = _page(); w, a = ["r.company_id=?"], [company_id()]
    if not has_perm("adjustments.approve"):
        w.append("r.requested_by=?"); a.append(g.user["id"])
    if request.args.get("status") in ("pendente", "aprovada", "rejeitada"):
        w.append("r.status=?"); a.append(request.args["status"])
    db = get_db(); sql = " AND ".join(w)
    total = db.execute(f"SELECT count(*) FROM adjustment_requests r WHERE {sql}", a).fetchone()[0]
    rows = db.execute(f"SELECT r.id,r.kind,r.reason,r.status,r.requested_by,u.name AS requester,r.decided_by,r.decided_at,r.decision_note,r.movement_id,r.created_at,r.payload "
                      f"FROM adjustment_requests r JOIN users u ON u.id=r.requested_by AND u.company_id=r.company_id WHERE {sql} ORDER BY r.id DESC LIMIT ? OFFSET ?", a + [size, (page - 1) * size]).fetchall()
    return jsonify(items=[{**dict(r), "payload": json.loads(r["payload"])} for r in rows], total=total, page=page, size=size)

def _decide(rid, approve):
    db, cid, me = get_db(), company_id(), g.user
    note = ((request.get_json(silent=True) or {}).get("note") or "").strip()
    if not approve and len(note) < 3:
        raise StockError("Informe o motivo da rejeição.", field="note")
    with transaction(db):
        r = db.execute("SELECT * FROM adjustment_requests WHERE id=? AND company_id=?", (rid, cid)).fetchone()
        if not r: raise StockError("Solicitação não encontrada.", 404)
        if r["status"] != "pendente": raise StockError("Esta solicitação já foi decidida.", 409)
        if r["requested_by"] == me["id"]: raise StockError("Você não pode decidir a sua própria solicitação.", 403)
        mid = None
        if approve:
            body = json.loads(r["payload"])
            mid = stock._post(db, cid, me["id"], body, f"adjreq-{rid}", "adjreq", request.remote_addr)
        db.execute("UPDATE adjustment_requests SET status=?, decided_by=?, decided_at=?, decision_note=?, movement_id=? WHERE id=?",
                   ("aprovada" if approve else "rejeitada", me["id"], acc.now_iso(), note or None, mid, rid))
        acc.audit(db, cid, me["id"], "adjustment.approved" if approve else "adjustment.rejected", "adjustment_request", rid, {"movement_id": mid}, request.remote_addr)
    return jsonify(id=rid, status="aprovada" if approve else "rejeitada", movement_id=mid)

@bp.post("/api/adjustment-requests/<int:rid>/approve")
@require_perm("adjustments.approve")
def approve(rid):
    try:
        return _decide(rid, True)
    except stock.Insufficient as e:
        acc.audit(get_db(), company_id(), g.user["id"], "movement.rejected", details={"reason": e.msg, "request": rid}, ip=request.remote_addr)
        raise

@bp.post("/api/adjustment-requests/<int:rid>/reject")
@require_perm("adjustments.approve")
def reject(rid):
    return _decide(rid, False)

@bp.get("/movimentacoes")
@login_required
def page():
    p = perms()
    if not ({"movements.create", "movements.view_all", "movements.view_own"} & p):
        return jsonify(error="Acesso negado."), 403
    return render_template("movements.html")
