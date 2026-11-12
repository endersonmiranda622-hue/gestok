"""Motor de estoque. Regras: saldo só muda junto com uma movimentação, na mesma transação (BEGIN IMMEDIATE);
histórico é imutável; correções por estorno/ajuste; custo médio ponderado por item (ver README)."""
import hashlib, json, re, uuid
from datetime import date, datetime, timedelta, timezone
from . import accounts as acc
from .db import transaction
from .numbers import NumError, fmt_qty, parse_money, parse_qty

class StockError(Exception):
    def __init__(self, msg, code=400, field=None):
        super().__init__(msg); self.msg, self.code, self.field = msg, code, field

class Insufficient(StockError):
    def __init__(self, msg):
        super().__init__(msg, 409)

DIRECT = ("entrada", "saida", "transferencia")
MAX_LINES = 100
_LOT = re.compile(r"^[A-Z0-9][A-Z0-9._/-]{0,39}$")

def _err(msg, field=None, code=400):
    raise StockError(msg, code, field)

def _ref(db, cid, table, rid, field, label, need_active=True):
    try:
        rid = int(rid)
    except (TypeError, ValueError):
        _err(f"{label} inválido.", field)
    r = db.execute(f"SELECT * FROM {table} WHERE id=? AND company_id=?", (rid, cid)).fetchone()
    if not r or (need_active and not r["active"]):
        _err(f"{label} inválido ou inativo.", field)
    return r

def _line(db, cid, kind, raw, n):
    if not isinstance(raw, dict):
        _err(f"Linha {n}: formato inválido.")
    item = _ref(db, cid, "items", raw.get("item_id"), "item_id", f"Linha {n}: item")
    allow = db.execute("SELECT allow_fraction FROM units WHERE id=? AND company_id=?", (item["unit_id"], cid)).fetchone()[0]
    try:
        qty = parse_qty(raw.get("qty"), allow)
    except NumError as e:
        _err(f"Linha {n}: {e}", "qty")
    if qty == 0:
        _err(f"Linha {n}: a quantidade deve ser maior que zero.", "qty")
    out = {"item_id": item["id"], "qty_milli": qty, "lot_code": None, "expiry": None}
    code = (raw.get("lot_code") or "").strip().upper()
    if item["tracks_lot"]:
        if not _LOT.match(code):
            _err(f"Linha {n}: informe o lote de {item['code']} (letras, números, . _ / -).", "lot_code")
        out["lot_code"] = code
    elif code:
        _err(f"Linha {n}: o item {item['code']} não controla lote.", "lot_code")
    if kind == "transferencia":
        a = _ref(db, cid, "warehouses", raw.get("from_warehouse_id"), "from_warehouse_id", f"Linha {n}: depósito de origem")
        b = _ref(db, cid, "warehouses", raw.get("to_warehouse_id"), "to_warehouse_id", f"Linha {n}: depósito de destino")
        if a["id"] == b["id"]:
            _err(f"Linha {n}: origem e destino devem ser diferentes.", "to_warehouse_id")
        out.update(from_warehouse_id=a["id"], to_warehouse_id=b["id"])
        return out
    out["warehouse_id"] = _ref(db, cid, "warehouses", raw.get("warehouse_id"), "warehouse_id", f"Linha {n}: depósito")["id"]
    if kind == "entrada":
        c = raw.get("cost")
        try:
            out["cost_e4"] = None if c in (None, "") else parse_money(c)
        except NumError as e:
            _err(f"Linha {n}: {e}", "cost")
        exp = raw.get("expiry_date")
        if item["tracks_expiry"]:
            try:
                d = datetime.strptime(str(exp), "%Y-%m-%d").date()
            except ValueError:
                _err(f"Linha {n}: informe a validade (AAAA-MM-DD).", "expiry_date")
            if d < datetime.now(timezone.utc).date():
                _err(f"Linha {n}: lote já vencido; não é possível dar entrada.", "expiry_date")
            out["expiry"] = d.isoformat()
        elif exp:
            _err(f"Linha {n}: o item não controla validade.", "expiry_date")
    elif kind == "ajuste":
        if raw.get("direction") not in ("aumentar", "diminuir"):
            _err(f"Linha {n}: informe a direção (aumentar ou diminuir).", "direction")
        if raw["direction"] == "diminuir":
            out["qty_milli"] = -qty
    return out

def normalize(db, cid, body):
    kind = body.get("kind")
    reason = (body.get("reason") or "").strip()
    if len(reason) > 300:
        _err("O motivo deve ter até 300 caracteres.", "reason")
    if kind == "estorno":
        if len(reason) < 5:
            _err("Informe o motivo do estorno (mínimo 5 caracteres).", "reason")
        mid = str(body.get("movement_id") or "")
        o = db.execute("SELECT id, kind FROM stock_movements WHERE id=? AND company_id=?", (mid, cid)).fetchone()
        if not o:
            _err("Movimentação original não encontrada.", "movement_id", 404)
        if o["kind"] == "estorno":
            _err("Um estorno não pode ser estornado.", "movement_id")
        if db.execute("SELECT 1 FROM stock_movements WHERE reverses_id=? AND company_id=?", (mid, cid)).fetchone():
            _err("Esta movimentação já foi estornada.", "movement_id", 409)
        return {"kind": kind, "reason": reason, "movement_id": mid}
    if kind not in DIRECT + ("ajuste",):
        _err("Tipo de movimentação inválido.", "kind")
    if kind == "ajuste" and len(reason) < 5:
        _err("Informe o motivo do ajuste (mínimo 5 caracteres).", "reason")
    lines = body.get("lines")
    if not isinstance(lines, list) or not 1 <= len(lines) <= MAX_LINES:
        _err(f"Informe de 1 a {MAX_LINES} linhas.", "lines")
    return {"kind": kind, "reason": reason, "lines": [_line(db, cid, kind, l, i + 1) for i, l in enumerate(lines)]}

def _lot(db, cid, item_id, code, expiry, create):
    r = db.execute("SELECT id, expiry_date FROM lots WHERE company_id=? AND item_id=? AND lot_code=?", (cid, item_id, code)).fetchone()
    if r:
        if create and (r["expiry_date"] or None) != expiry:
            _err(f"O lote {code} já existe com outra validade.", "expiry_date", 409)
        return r["id"]
    if not create:
        _err(f"Lote {code} não encontrado para este item.", "lot_code", 404)
    return db.execute("INSERT INTO lots(company_id,item_id,lot_code,expiry_date) VALUES (?,?,?,?)", (cid, item_id, code, expiry)).lastrowid

def _apply(db, cid, mid, now, item_id, wh, lot_id, delta, cost, allow_neg, negs, set_avg=False):
    """Grava a linha e move o saldo. Se o saldo ficar negativo (sem política), levanta Insufficient e a transação inteira é desfeita."""
    it = db.execute("SELECT code, avg_cost_e4 FROM items WHERE id=? AND company_id=?", (item_id, cid)).fetchone()
    avg = it["avg_cost_e4"]
    if set_avg:  # entrada com custo informado: custo médio ponderado (arredondamento meia-unidade para cima)
        total = db.execute("SELECT COALESCE(sum(qty_milli),0) FROM stock_balances WHERE company_id=? AND item_id=?", (cid, item_id)).fetchone()[0]
        den = total + delta
        new_avg = cost if total <= 0 else (2 * (total * avg + delta * cost) + den) // (2 * den)
        db.execute("UPDATE items SET avg_cost_e4=? WHERE id=? AND company_id=?", (new_avg, item_id, cid))
    line_cost = avg if cost is None else cost
    db.execute("INSERT INTO movement_lines(movement_id,company_id,item_id,warehouse_id,lot_id,qty_milli,unit_cost_e4) VALUES (?,?,?,?,?,?,?)",
               (mid, cid, item_id, wh, lot_id, delta, line_cost))
    db.execute("INSERT INTO stock_balances(company_id,item_id,warehouse_id,qty_milli,last_movement_at) VALUES (?,?,?,?,?) "
               "ON CONFLICT(company_id,item_id,warehouse_id) DO UPDATE SET qty_milli=qty_milli+excluded.qty_milli, last_movement_at=excluded.last_movement_at",
               (cid, item_id, wh, delta, now))
    new = db.execute("SELECT qty_milli FROM stock_balances WHERE company_id=? AND item_id=? AND warehouse_id=?", (cid, item_id, wh)).fetchone()[0]
    if new < 0 and not allow_neg:
        raise Insufficient(f"Saldo insuficiente de {it['code']} no depósito: disponível {fmt_qty(new - delta)}.")
    if new < 0:
        negs.append({"item_id": item_id, "warehouse_id": wh, "balance_milli": new})
    if lot_id:
        db.execute("INSERT INTO lot_balances(company_id,lot_id,warehouse_id,qty_milli) VALUES (?,?,?,?) "
                   "ON CONFLICT(company_id,lot_id,warehouse_id) DO UPDATE SET qty_milli=qty_milli+excluded.qty_milli", (cid, lot_id, wh, delta))
        ln = db.execute("SELECT qty_milli FROM lot_balances WHERE company_id=? AND lot_id=? AND warehouse_id=?", (cid, lot_id, wh)).fetchone()[0]
        if ln < 0 and not allow_neg:
            raise Insufficient(f"Saldo insuficiente do lote de {it['code']}: disponível {fmt_qty(ln - delta)}.")

def _post(db, cid, user_id, body, key, req_hash, ip):
    """Núcleo: exige transação já aberta. Retorna id da movimentação."""
    norm = normalize(db, cid, body)
    kind, mid, now = norm["kind"], uuid.uuid4().hex, acc.now_iso()
    allow_neg = (db.execute("SELECT value FROM settings WHERE company_id=? AND key='allow_negative_stock'", (cid,)).fetchone() or ["0"])[0] == "1"
    db.execute("INSERT INTO stock_movements(id,company_id,kind,user_id,reason,reverses_id,idempotency_key,occurred_at,request_hash) VALUES (?,?,?,?,?,?,?,?,?)",
               (mid, cid, kind, user_id, norm["reason"] or None, norm.get("movement_id"), key, now, req_hash))
    negs = []
    if kind == "estorno":
        for l in db.execute("SELECT * FROM movement_lines WHERE movement_id=? AND company_id=? ORDER BY id", (norm["movement_id"], cid)).fetchall():
            _apply(db, cid, mid, now, l["item_id"], l["warehouse_id"], l["lot_id"], -l["qty_milli"], l["unit_cost_e4"], allow_neg, negs)
    else:
        for l in norm["lines"]:
            lot_id = _lot(db, cid, l["item_id"], l["lot_code"], l["expiry"], kind == "entrada") if l["lot_code"] else None
            avg = db.execute("SELECT avg_cost_e4 FROM items WHERE id=?", (l["item_id"],)).fetchone()[0]
            if kind == "entrada":
                _apply(db, cid, mid, now, l["item_id"], l["warehouse_id"], lot_id, l["qty_milli"], l["cost_e4"], allow_neg, negs, set_avg=l["cost_e4"] is not None)
            elif kind == "saida":
                _apply(db, cid, mid, now, l["item_id"], l["warehouse_id"], lot_id, -l["qty_milli"], None, allow_neg, negs)
            elif kind == "ajuste":
                _apply(db, cid, mid, now, l["item_id"], l["warehouse_id"], lot_id, l["qty_milli"], None, allow_neg, negs)
            else:  # transferência: duas linhas na mesma transação, custo médio inalterado
                _apply(db, cid, mid, now, l["item_id"], l["from_warehouse_id"], lot_id, -l["qty_milli"], None, allow_neg, negs)
                _apply(db, cid, mid, now, l["item_id"], l["to_warehouse_id"], lot_id, l["qty_milli"], None, allow_neg, negs)
    acc.audit(db, cid, user_id, "movement.created", "movement", mid, {"kind": kind}, ip)
    if negs:
        acc.audit(db, cid, user_id, "stock.negative_allowed", "movement", mid, {"balances": negs}, ip)
    return mid

def post_movement(db, cid, user_id, body, key, ip=None):
    """Retorna (movimentação, repetida?). A mesma chave com o mesmo conteúdo devolve a original; conteúdo diferente = 409."""
    if not re.match(r"^[A-Za-z0-9._:-]{8,100}$", key or ""):
        _err("Informe uma chave de idempotência (8 a 100 caracteres).", "idempotency_key")
    req_hash = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    try:
        with transaction(db):
            ex = db.execute("SELECT id, request_hash FROM stock_movements WHERE company_id=? AND idempotency_key=?", (cid, key)).fetchone()
            if ex:
                if ex["request_hash"] != req_hash:
                    _err("Esta chave de idempotência já foi usada com outro conteúdo.", "idempotency_key", 409)
                return get_movement(db, cid, ex["id"]), True
            mid = _post(db, cid, user_id, body, key, req_hash, ip)
    except Insufficient as e:
        acc.audit(db, cid, user_id, "movement.rejected", details={"reason": e.msg, "kind": body.get("kind")}, ip=ip)  # fora da transação desfeita
        raise
    except Exception as e:
        if "UNIQUE" in str(e) and "reverses_id" in str(e):
            _err("Esta movimentação já foi estornada.", "movement_id", 409)
        raise
    return get_movement(db, cid, mid), False

_SEL = """SELECT m.*, u.name AS user_name FROM stock_movements m JOIN users u ON u.id=m.user_id AND u.company_id=m.company_id"""

def _attach(db, cid, rows):
    ids = [r["id"] for r in rows]
    lines = {}
    if ids:
        q = ",".join("?" * len(ids))
        for l in db.execute(f"""SELECT l.movement_id, l.item_id, i.code AS item_code, i.name AS item_name, l.warehouse_id, w.name AS warehouse_name,
            l.lot_id, lo.lot_code, lo.expiry_date, l.qty_milli, l.unit_cost_e4 FROM movement_lines l
            JOIN items i ON i.id=l.item_id AND i.company_id=l.company_id JOIN warehouses w ON w.id=l.warehouse_id AND w.company_id=l.company_id
            LEFT JOIN lots lo ON lo.id=l.lot_id AND lo.company_id=l.company_id WHERE l.company_id=? AND l.movement_id IN ({q}) ORDER BY l.id""", [cid] + ids):
            d = dict(l); d["qty_display"] = fmt_qty(abs(d["qty_milli"])); lines.setdefault(d.pop("movement_id"), []).append(d)
    out = []
    for r in rows:
        d = dict(r); d.pop("request_hash", None); d["lines"] = lines.get(r["id"], []); out.append(d)
    return out

def get_movement(db, cid, mid):
    r = db.execute(_SEL + " WHERE m.id=? AND m.company_id=?", (mid, cid)).fetchone()
    return _attach(db, cid, [r])[0] if r else None

def _local_bounds(db, cid, d, end):
    from zoneinfo import ZoneInfo
    tz = db.execute("SELECT timezone FROM companies WHERE id=?", (cid,)).fetchone()[0]
    try:
        day = datetime.strptime(d, "%Y-%m-%d") + (timedelta(days=1) if end else timedelta())
    except ValueError:
        _err("Data inválida (use AAAA-MM-DD).")
    return acc.iso(day.replace(tzinfo=ZoneInfo(tz)).astimezone(timezone.utc))

def list_movements(db, cid, args, only_user_id, page, size):
    where, p = ["m.company_id=?"], [cid]
    if only_user_id:
        where.append("m.user_id=?"); p.append(only_user_id)
    for k, col in (("kind", "m.kind"), ("user_id", "m.user_id")):
        if args.get(k) and not (k == "user_id" and only_user_id):
            where.append(f"{col}=?"); p.append(args[k])
    if args.get("item_id") or args.get("warehouse_id"):
        sub, sp = ["l.movement_id=m.id", "l.company_id=m.company_id"], []
        for k in ("item_id", "warehouse_id"):
            if args.get(k): sub.append(f"l.{k}=?"); sp.append(args[k])
        where.append(f"EXISTS (SELECT 1 FROM movement_lines l WHERE {' AND '.join(sub)})"); p += sp
    if args.get("from"): where.append("m.occurred_at>=?"); p.append(_local_bounds(db, cid, args["from"], False))
    if args.get("to"): where.append("m.occurred_at<?"); p.append(_local_bounds(db, cid, args["to"], True))
    w = " AND ".join(where)
    total = db.execute(f"SELECT count(*) FROM stock_movements m WHERE {w}", p).fetchone()[0]
    rows = db.execute(f"{_SEL} WHERE {w} ORDER BY m.occurred_at DESC, m.rowid DESC LIMIT ? OFFSET ?", p + [size, (page - 1) * size]).fetchall()
    return _attach(db, cid, rows), total

def reconcile(db, cid=None):
    """Compara saldos materializados com a soma do histórico. Retorna lista de divergências (vazia = consistente)."""
    f, a = ("AND company_id=?", (cid,)) if cid else ("", ())
    bad = []
    for kind, tbl, key, expected in (
        ("saldo", "stock_balances", "item_id, warehouse_id", "SELECT company_id, item_id, warehouse_id, sum(qty_milli) q FROM movement_lines WHERE 1=1 %s GROUP BY 1,2,3"),
        ("lote", "lot_balances", "lot_id, warehouse_id", "SELECT company_id, lot_id, warehouse_id, NULL x, sum(qty_milli) q FROM movement_lines WHERE lot_id IS NOT NULL %s GROUP BY 1,2,3")):
        exp = {}
        for r in db.execute(expected % f, a):
            exp[tuple(r[:3])] = r["q"]
        cols = "company_id, " + key
        have = {tuple(r[:3]): r[3] for r in db.execute(f"SELECT {cols}, qty_milli FROM {tbl} WHERE 1=1 {f}", a)}
        for k in set(exp) | set(have):
            if exp.get(k, 0) != have.get(k, 0):
                bad.append({"tipo": kind, "chave": k, "historico": exp.get(k, 0), "materializado": have.get(k, 0)})
    return bad
