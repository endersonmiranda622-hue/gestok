import os, re, shutil, tempfile, threading, unittest
from datetime import date, timedelta
from unittest import mock
from app import create_app, MIGRATIONS_DIR, stock
from app import db as dbm, accounts as acc

PW, PW2 = "Senha-forte-123", "Outra-senha-456"
FUT = (date.today() + timedelta(days=400)).isoformat()

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.path = os.path.join(self.tmp, "t.db")
        self.conn = dbm.connect(self.path); dbm.migrate(self.conn, MIGRATIONS_DIR)
        self.app = create_app({"DATABASE": self.path, "TESTING": True, "SECRET_KEY": "k" * 40})
        self.cidA, self.uidA = acc.create_company_with_admin(self.conn, "A", "Ana", "ana@a.com", PW)
        self.cidB, self.uidB = acc.create_company_with_admin(self.conn, "B", "Bruno", "bruno@b.com", PW)
        self.admin, self.adminB = self.client("ana@a.com", PW), self.client("bruno@b.com", PW)
        self.wh1 = self.conn.execute("SELECT id FROM warehouses WHERE company_id=?", (self.cidA,)).fetchone()[0]
        self.wh2 = self.api(self.admin, "POST", "/api/warehouses", {"name": "Filial"}).json["id"]
        self.unA = self.conn.execute("SELECT id FROM units WHERE company_id=?", (self.cidA,)).fetchone()[0]
        self.n = 0
    def tearDown(self):
        self.conn.close(); shutil.rmtree(self.tmp)
    def tok(self, c, url="/login"):
        return re.search(r'name="csrf_token" value="([^"]+)"', c.get(url).get_data(as_text=True)).group(1)
    def client(self, email, pw):
        c = self.app.test_client(); c.post("/login", data={"email": email, "password": pw, "csrf_token": self.tok(c)}); return c
    def api(self, c, m, u, b=None, headers=None):
        return c.open(u, method=m, json=b, headers={"X-CSRF-Token": self.tok(c, "/"), **(headers or {})})
    def mk(self, role, email):
        r = self.api(self.admin, "POST", "/api/users", {"name": "N " + role, "email": email, "role": role}); c = self.app.test_client()
        c.post(r.json["reset_path"], data={"password": PW2, "csrf_token": self.tok(c, r.json["reset_path"])}); return self.client(email, PW2), r.json["user"]["id"]
    def item(self, code="P1", c=None, **kw):
        r = self.api(c or self.admin, "POST", "/api/items", {"code": code, "name": "Item " + code, "item_type": "material", "unit_id": self.unA, **kw}); assert r.status_code == 201, r.json; return r.json["id"]
    def mv(self, kind, c=None, key=None, **body):
        self.n += 1
        return self.api(c or self.admin, "POST", "/api/movements", {"kind": kind, **body}, {"Idempotency-Key": key or f"key-{self.n}-{os.urandom(4).hex()}"})
    def entrada(self, item, qty, cost="2", wh=None, c=None, **line):
        return self.mv("entrada", c, lines=[{"item_id": item, "warehouse_id": wh or self.wh1, "qty": qty, "cost": cost, **line}])
    def saida(self, item, qty, wh=None, c=None, **line):
        return self.mv("saida", c, lines=[{"item_id": item, "warehouse_id": wh or self.wh1, "qty": qty, **line}])
    def bal(self, item, wh=None):
        r = self.conn.execute("SELECT qty_milli FROM stock_balances WHERE item_id=? AND warehouse_id=?", (item, wh or self.wh1)).fetchone(); return r[0] if r else 0
    def avg(self, item): return self.conn.execute("SELECT avg_cost_e4 FROM items WHERE id=?", (item,)).fetchone()[0]
    def consistent(self): self.assertEqual(stock.reconcile(self.conn), [])

class BasicFlowTests(Base):
    def test_entrada_saida_and_history(self):
        i = self.item(); r = self.entrada(i, 10); self.assertEqual(r.status_code, 201)
        self.assertEqual((self.bal(i), r.json["lines"][0]["qty_display"], r.json["user_name"]), (10000, "10", "Ana"))
        self.assertEqual(self.saida(i, 4).status_code, 201); self.assertEqual(self.bal(i), 6000)
        j = self.api(self.admin, "GET", "/api/movements").json; self.assertEqual(j["total"], 2)
        self.assertEqual(self.api(self.admin, "GET", f"/api/stock/{i}").json["warehouses"][0]["qty_milli"], 6000); self.consistent()
    def test_exit_above_balance_blocked_with_no_side_effects(self):
        i = self.item(); self.entrada(i, 5)
        r = self.saida(i, 6); self.assertEqual(r.status_code, 409); self.assertIn("disponível 5", r.json["error"])
        self.assertEqual((self.bal(i), self.conn.execute("SELECT count(*) FROM stock_movements").fetchone()[0]), (5000, 1))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM audit_log WHERE action='movement.rejected'").fetchone()[0], 1)
        self.assertEqual(self.saida(i, 5).status_code, 201); self.assertEqual(self.bal(i), 0)
    def test_negative_stock_only_with_explicit_policy_and_audited(self):
        i = self.item(); self.entrada(i, 1)
        self.conn.execute("INSERT INTO settings(company_id,key,value) VALUES (?,?,?)", (self.cidA, "allow_negative_stock", "1"))
        self.assertEqual(self.saida(i, 3).status_code, 201); self.assertEqual(self.bal(i), -2000)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM audit_log WHERE action='stock.negative_allowed'").fetchone()[0], 1)
    def test_transfer_is_atomic_and_moves_both_sides(self):
        i = self.item(); self.entrada(i, 10)
        r = self.mv("transferencia", lines=[{"item_id": i, "from_warehouse_id": self.wh1, "to_warehouse_id": self.wh2, "qty": 4}])
        self.assertEqual((r.status_code, len(r.json["lines"]), self.bal(i), self.bal(i, self.wh2)), (201, 2, 6000, 4000))
        bad = self.mv("transferencia", lines=[{"item_id": i, "from_warehouse_id": self.wh1, "to_warehouse_id": self.wh2, "qty": 7}])
        self.assertEqual((bad.status_code, self.bal(i), self.bal(i, self.wh2)), (409, 6000, 4000))
        same = self.mv("transferencia", lines=[{"item_id": i, "from_warehouse_id": self.wh1, "to_warehouse_id": self.wh1, "qty": 1}])
        self.assertEqual(same.status_code, 400); self.consistent()
    def test_multi_line_all_or_nothing(self):
        a, b = self.item("A"), self.item("B"); self.entrada(a, 5)
        r = self.mv("saida", lines=[{"item_id": a, "warehouse_id": self.wh1, "qty": 2}, {"item_id": b, "warehouse_id": self.wh1, "qty": 1}])
        self.assertEqual((r.status_code, self.bal(a)), (409, 5000)); self.consistent()
    def test_midway_failure_rolls_back_everything(self):
        a, b = self.item("A"), self.item("B"); self.entrada(a, 5); self.entrada(b, 5)
        real = stock._apply; calls = []
        def boom(*args, **kw):
            calls.append(1)
            if len(calls) == 2: raise RuntimeError("falha simulada")
            return real(*args, **kw)
        app = create_app({"DATABASE": self.path, "TESTING": False, "SECRET_KEY": "k" * 40})
        c = app.test_client(); c.post("/login", data={"email": "ana@a.com", "password": PW, "csrf_token": self.tok(c)})
        with mock.patch.object(stock, "_apply", boom):
            r = c.post("/api/movements", json={"kind": "saida", "lines": [{"item_id": a, "warehouse_id": self.wh1, "qty": 1}, {"item_id": b, "warehouse_id": self.wh1, "qty": 1}]},
                       headers={"X-CSRF-Token": self.tok(c, "/"), "Idempotency-Key": "falha-simulada-1"})
        self.assertEqual(r.status_code, 500)
        self.assertEqual((self.bal(a), self.bal(b), self.conn.execute("SELECT count(*) FROM stock_movements").fetchone()[0]), (5000, 5000, 2)); self.consistent()
    def test_validation(self):
        i = self.item(); self.entrada(i, 5)
        L = lambda **k: [{"item_id": i, "warehouse_id": self.wh1, "qty": 1, **k}]
        for body in ({"kind": "entrada", "lines": L(qty=0)}, {"kind": "entrada", "lines": L(qty=-1)}, {"kind": "entrada", "lines": L(qty="1,5")},
                     {"kind": "entrada", "lines": L(qty="abc")}, {"kind": "entrada", "lines": L(cost="-1")}, {"kind": "entrada", "lines": []}, {"kind": "entrada"},
                     {"kind": "entrada", "lines": L(item_id=99999)}, {"kind": "entrada", "lines": L(warehouse_id=99999)}, {"kind": "entrada", "lines": L(lot_code="L1")},
                     {"kind": "viagem", "lines": L()}, {"kind": "ajuste", "lines": L(direction="aumentar")}, {"kind": "entrada", "lines": ["x"]}):
            r = self.mv(body.pop("kind"), **body); self.assertEqual(r.status_code, 400, (body, r.json))
        self.assertEqual(self.bal(i), 5000)
    def test_inactive_item_or_warehouse_cannot_move(self):
        i = self.item(); self.api(self.admin, "PATCH", f"/api/items/{i}", {"active": False})
        self.assertEqual(self.entrada(i, 1).status_code, 400)
        j = self.item("Q"); w = self.api(self.admin, "POST", "/api/warehouses", {"name": "Velho"}).json["id"]; self.api(self.admin, "PATCH", f"/api/warehouses/{w}", {"active": False})
        self.assertEqual(self.entrada(j, 1, wh=w).status_code, 400)
    def test_fractional_units(self):
        kg = self.api(self.admin, "POST", "/api/units", {"code": "KG", "name": "Quilo", "allow_fraction": True}).json["id"]
        i = self.item("F", unit_id=kg); self.assertEqual(self.entrada(i, "2,5").status_code, 201); self.assertEqual(self.bal(i), 2500)
        self.assertEqual(self.entrada(i, "1,0005").status_code, 400)

class IdempotencyTests(Base):
    def test_replay_returns_same_movement_without_duplicating(self):
        i = self.item(); body = {"kind": "entrada", "lines": [{"item_id": i, "warehouse_id": self.wh1, "qty": 3, "cost": "1"}]}
        a = self.api(self.admin, "POST", "/api/movements", body, {"Idempotency-Key": "chave-unica-1"})
        b = self.api(self.admin, "POST", "/api/movements", body, {"Idempotency-Key": "chave-unica-1"})
        self.assertEqual((a.status_code, b.status_code, a.json["id"] == b.json["id"], b.headers.get("Idempotent-Replay")), (201, 200, True, "true"))
        self.assertEqual(self.bal(i), 3000)
    def test_same_key_different_payload_conflicts_and_key_required(self):
        i = self.item(); self.entrada(i, 1)
        k = {"Idempotency-Key": "chave-unica-2"}; mk = lambda q: {"kind": "entrada", "lines": [{"item_id": i, "warehouse_id": self.wh1, "qty": q}]}
        self.assertEqual(self.api(self.admin, "POST", "/api/movements", mk(1), k).status_code, 201)
        self.assertEqual(self.api(self.admin, "POST", "/api/movements", mk(2), k).status_code, 409)
        self.assertEqual(self.api(self.admin, "POST", "/api/movements", mk(1)).status_code, 400)
        self.assertEqual(self.api(self.admin, "POST", "/api/movements", mk(1), {"Idempotency-Key": "curta"}).status_code, 400)
    def test_same_key_allowed_in_different_companies(self):
        a, b = self.item(), self.item("P1", c=self.adminB, unit_id=self.conn.execute("SELECT id FROM units WHERE company_id=?", (self.cidB,)).fetchone()[0])
        whb = self.conn.execute("SELECT id FROM warehouses WHERE company_id=?", (self.cidB,)).fetchone()[0]
        k = {"Idempotency-Key": "mesma-chave-1"}
        self.assertEqual(self.api(self.admin, "POST", "/api/movements", {"kind": "entrada", "lines": [{"item_id": a, "warehouse_id": self.wh1, "qty": 1}]}, k).status_code, 201)
        self.assertEqual(self.api(self.adminB, "POST", "/api/movements", {"kind": "entrada", "lines": [{"item_id": b, "warehouse_id": whb, "qty": 1}]}, k).status_code, 201)

class ConcurrencyTests(Base):
    def _clients(self, n):
        cs = []
        for _ in range(n):
            c = self.app.test_client(); c.post("/login", data={"email": "ana@a.com", "password": PW, "csrf_token": self.tok(c)}); cs.append((c, self.tok(c, "/")))
        return cs
    def _run(self, calls):
        out, ts = [None] * len(calls), []
        def w(i, fn): out[i] = fn()
        for i, fn in enumerate(calls): ts.append(threading.Thread(target=w, args=(i, fn)))
        [t.start() for t in ts]; [t.join(60) for t in ts]; return out
    def test_concurrent_exits_never_oversell(self):
        i = self.item(); self.entrada(i, 10); cs = self._clients(8)
        body = {"kind": "saida", "lines": [{"item_id": i, "warehouse_id": self.wh1, "qty": 3}]}
        res = self._run([(lambda c=c, t=t, n=n: c.post("/api/movements", json=body, headers={"X-CSRF-Token": t, "Idempotency-Key": f"conc-exit-{n}-xx"}).status_code) for n, (c, t) in enumerate(cs)])
        self.assertEqual((res.count(201), res.count(409)), (3, 5), res)
        self.assertEqual(self.bal(i), 1000); self.consistent()
    def test_concurrent_retries_with_same_key_create_one_movement(self):
        i = self.item(); cs = self._clients(6)
        body = {"kind": "entrada", "lines": [{"item_id": i, "warehouse_id": self.wh1, "qty": 2, "cost": "1"}]}
        res = self._run([(lambda c=c, t=t: c.post("/api/movements", json=body, headers={"X-CSRF-Token": t, "Idempotency-Key": "retry-same-key-1"}).json["id"]) for c, t in cs])
        self.assertEqual(len(set(res)), 1); self.assertEqual(self.bal(i), 2000)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM stock_movements").fetchone()[0], 1); self.consistent()
    def test_concurrent_entries_sum_exactly(self):
        i = self.item(); cs = self._clients(8)
        body = {"kind": "entrada", "lines": [{"item_id": i, "warehouse_id": self.wh1, "qty": 1, "cost": "1"}]}
        res = self._run([(lambda c=c, t=t, n=n: c.post("/api/movements", json=body, headers={"X-CSRF-Token": t, "Idempotency-Key": f"conc-in-{n}-xxx"}).status_code) for n, (c, t) in enumerate(cs)])
        self.assertEqual((res.count(201), self.bal(i)), (8, 8000)); self.consistent()

class CostTests(Base):
    def test_weighted_average_and_exit_valuation(self):
        i = self.item(); self.entrada(i, 10, "2"); self.entrada(i, 10, "4"); self.assertEqual(self.avg(i), 30000)
        r = self.saida(i, 5); self.assertEqual(r.json["lines"][0]["unit_cost_e4"], 30000); self.assertEqual(self.avg(i), 30000)
        self.entrada(i, 15, "6"); self.assertEqual(self.avg(i), 45000)  # (15 un a 3,00 + 15 un a 6,00) / 30 = 4,50
    def test_rounding_half_up_and_reset_after_zero(self):
        # 1 un a 1,0000 + 1 un a 1,0001 = média exata 1,00005 -> arredonda para cima: 1,0001
        i = self.item(); self.entrada(i, 1, "1"); self.entrada(i, 1, "1,0001"); self.assertEqual(self.avg(i), 10001)
        # com saldo zerado, a próxima entrada redefine o custo médio
        self.saida(i, 2); self.entrada(i, 1, "9"); self.assertEqual(self.avg(i), 90000)
    def test_entry_without_cost_keeps_average_and_uses_it(self):
        i = self.item(); self.entrada(i, 2, "5"); r = self.entrada(i, 3, cost=None)
        self.assertEqual((self.avg(i), r.json["lines"][0]["unit_cost_e4"]), (50000, 50000))
    def test_transfer_and_adjust_do_not_change_average(self):
        i = self.item(); self.entrada(i, 4, "3")
        self.mv("transferencia", lines=[{"item_id": i, "from_warehouse_id": self.wh1, "to_warehouse_id": self.wh2, "qty": 1}])
        self.mv("ajuste", reason="contagem física", lines=[{"item_id": i, "warehouse_id": self.wh1, "qty": 1, "direction": "diminuir"}]); self.assertEqual(self.avg(i), 30000)

class LotTests(Base):
    def setUp(self):
        super().setUp(); self.i = self.item("L", tracks_lot=True, tracks_expiry=True)
    def test_lot_required_and_expiry_rules(self):
        self.assertEqual(self.entrada(self.i, 5).status_code, 400)
        self.assertEqual(self.entrada(self.i, 5, lot_code="L1").status_code, 400)
        self.assertEqual(self.entrada(self.i, 5, lot_code="L1", expiry_date="2001-01-01").status_code, 400)
        self.assertEqual(self.entrada(self.i, 5, lot_code="l1", expiry_date=FUT).status_code, 201)
        self.assertEqual(self.entrada(self.i, 5, lot_code="L1", expiry_date=(date.today() + timedelta(days=500)).isoformat()).status_code, 409)
    def test_exit_checks_lot_balance_not_just_total(self):
        self.entrada(self.i, 5, lot_code="L1", expiry_date=FUT); self.entrada(self.i, 5, lot_code="L2", expiry_date=FUT)
        self.assertEqual(self.saida(self.i, 6, lot_code="L1").status_code, 409)
        self.assertEqual(self.saida(self.i, 5, lot_code="L1").status_code, 201)
        self.assertEqual(self.saida(self.i, 1, lot_code="L9").status_code, 404); self.assertEqual(self.saida(self.i, 1).status_code, 400)
        j = self.api(self.admin, "GET", f"/api/stock/{self.i}").json; self.assertEqual({(l["lot_code"], l["qty_milli"]) for l in j["lots"]}, {("L1", 0), ("L2", 5000)}); self.consistent()
    def test_validity_requires_lot_flag_and_flags_locked_after_movement(self):
        self.assertEqual(self.api(self.admin, "POST", "/api/items", {"code": "V", "name": "v", "item_type": "material", "unit_id": self.unA, "tracks_expiry": True}).status_code, 400)
        self.entrada(self.i, 1, lot_code="L1", expiry_date=FUT)
        self.assertEqual(self.api(self.admin, "PATCH", f"/api/items/{self.i}", {"tracks_lot": False, "tracks_expiry": False}).status_code, 409)

class ReversalAndAdjustmentTests(Base):
    def test_reversal_restores_balance_keeps_original_and_is_single_use(self):
        i = self.item(); m = self.entrada(i, 5).json["id"]
        r = self.mv("estorno", movement_id=m, reason="lançado por engano"); self.assertEqual((r.status_code, self.bal(i)), (201, 0))
        self.assertEqual(self.conn.execute("SELECT reverses_id FROM stock_movements WHERE id=?", (r.json["id"],)).fetchone()[0], m)
        self.assertEqual(self.api(self.admin, "GET", f"/api/movements/{m}").json["lines"][0]["qty_milli"], 5000)
        self.assertEqual(self.mv("estorno", movement_id=m, reason="outra vez ok").status_code, 409)
        self.assertEqual(self.mv("estorno", movement_id=r.json["id"], reason="estornar estorno").status_code, 400)
        self.assertEqual(self.mv("estorno", movement_id=m, reason="x").status_code, 400); self.consistent()
    def test_reversing_entry_blocked_when_stock_already_consumed(self):
        i = self.item(); m = self.entrada(i, 5).json["id"]; self.saida(i, 4)
        self.assertEqual(self.mv("estorno", movement_id=m, reason="tentativa tardia").status_code, 409); self.assertEqual(self.bal(i), 1000)
    def test_reversal_of_transfer_and_exit(self):
        i = self.item(); self.entrada(i, 5)
        t = self.mv("transferencia", lines=[{"item_id": i, "from_warehouse_id": self.wh1, "to_warehouse_id": self.wh2, "qty": 2}]).json["id"]
        self.mv("estorno", movement_id=t, reason="destino errado"); self.assertEqual((self.bal(i), self.bal(i, self.wh2)), (5000, 0))
        s = self.saida(i, 2).json["id"]; self.mv("estorno", movement_id=s, reason="devolvido ao estoque"); self.assertEqual(self.bal(i), 5000); self.consistent()
    def test_history_is_immutable_at_db_level(self):
        i = self.item(); self.entrada(i, 1)
        with self.assertRaises(Exception): self.conn.execute("UPDATE movement_lines SET qty_milli=99")
        with self.assertRaises(Exception): self.conn.execute("DELETE FROM stock_movements")
    def test_adjustment_requires_reason_and_direction(self):
        i = self.item(); self.entrada(i, 5)
        ln = [{"item_id": i, "warehouse_id": self.wh1, "qty": 2, "direction": "diminuir"}]
        self.assertEqual(self.mv("ajuste", lines=ln).status_code, 400)
        self.assertEqual(self.mv("ajuste", reason="inventário", lines=ln).status_code, 201); self.assertEqual(self.bal(i), 3000)
        self.assertEqual(self.mv("ajuste", reason="inventário", lines=[{**ln[0], "qty": 9}]).status_code, 409)

class PermissionAndRequestTests(Base):
    def test_matrix(self):
        col, _ = self.mk("colaborador", "c@a.com"); ges, _ = self.mk("gestor", "g@a.com"); i = self.item(); self.entrada(i, 5)
        for c in (col, ges):
            self.assertEqual(self.entrada(i, 1, c=c).status_code, 201); self.assertEqual(self.saida(i, 1, c=c).status_code, 201)
            self.assertEqual(self.mv("ajuste", c, reason="inventário", lines=[{"item_id": i, "warehouse_id": self.wh1, "qty": 1, "direction": "aumentar"}]).status_code, 403)
            self.assertEqual(self.mv("estorno", c, movement_id="x", reason="quero estornar").status_code, 403)
        self.assertEqual(self.app.test_client().post("/api/movements", json={}).status_code, 400)
        self.api(self.admin, "PUT", "/api/permissions/colaborador", {"permissions": ["items.view", "movements.view_own"]})
        self.assertEqual(self.entrada(i, 1, c=col).status_code, 403)
    def test_view_own_vs_view_all(self):
        col, cid = self.mk("colaborador", "c@a.com"); ges, _ = self.mk("gestor", "g@a.com"); i = self.item(); self.entrada(i, 5); self.entrada(i, 1, c=col)
        mine = self.api(col, "GET", "/api/movements").json; self.assertEqual((mine["total"], {m["user_id"] for m in mine["items"]}), (1, {cid}))
        self.assertEqual(self.api(col, "GET", f"/api/movements?user_id={self.uidA}").json["total"], 1)
        self.assertEqual(self.api(ges, "GET", "/api/movements").json["total"], 2)
        first = self.api(ges, "GET", "/api/movements").json["items"]; other = [m for m in first if m["user_id"] != cid][0]["id"]
        self.assertEqual(self.api(col, "GET", f"/api/movements/{other}").status_code, 404)
    def test_request_approve_flow(self):
        col, cid = self.mk("colaborador", "c@a.com"); i = self.item(); self.entrada(i, 5)
        body = {"kind": "ajuste", "reason": "quebra constatada", "lines": [{"item_id": i, "warehouse_id": self.wh1, "qty": 2, "direction": "diminuir"}]}
        r = self.api(col, "POST", "/api/adjustment-requests", body); self.assertEqual(r.status_code, 201); rid = r.json["id"]
        self.assertEqual(self.bal(i), 5000)                                                                 # pedido não mexe no estoque
        self.assertEqual(self.api(col, "POST", f"/api/adjustment-requests/{rid}/approve").status_code, 403)  # colaborador não aprova
        self.assertEqual(self.api(self.admin, "GET", "/api/adjustment-requests").json["total"], 1)
        ok = self.api(self.admin, "POST", f"/api/adjustment-requests/{rid}/approve"); self.assertEqual((ok.status_code, self.bal(i)), (200, 3000))
        self.assertEqual(self.conn.execute("SELECT user_id FROM stock_movements WHERE id=?", (ok.json["movement_id"],)).fetchone()[0], self.uidA)
        self.assertEqual(self.api(self.admin, "POST", f"/api/adjustment-requests/{rid}/approve").status_code, 409)
        self.assertEqual(self.api(col, "GET", "/api/adjustment-requests").json["items"][0]["status"], "aprovada")
    def test_reject_needs_note_and_no_self_approval_and_estorno_request(self):
        col, _ = self.mk("colaborador", "c@a.com"); i = self.item(); m = self.entrada(i, 5).json["id"]
        rid = self.api(col, "POST", "/api/adjustment-requests", {"kind": "estorno", "movement_id": m, "reason": "entrada duplicada"}).json["id"]
        self.assertEqual(self.api(self.admin, "POST", f"/api/adjustment-requests/{rid}/reject", {}).status_code, 400)
        self.assertEqual(self.api(self.admin, "POST", f"/api/adjustment-requests/{rid}/reject", {"note": "conferido, está correto"}).status_code, 200); self.assertEqual(self.bal(i), 5000)
        own = self.api(self.admin, "POST", "/api/adjustment-requests", {"kind": "estorno", "movement_id": m, "reason": "teste auto"}).json["id"]
        self.assertEqual(self.api(self.admin, "POST", f"/api/adjustment-requests/{own}/approve").status_code, 403)
        self.assertEqual(self.api(col, "POST", "/api/adjustment-requests", {"kind": "entrada", "lines": []}).status_code, 400)
    def test_approval_revalidates_current_stock(self):
        col, _ = self.mk("colaborador", "c@a.com"); i = self.item(); self.entrada(i, 5)
        rid = self.api(col, "POST", "/api/adjustment-requests", {"kind": "ajuste", "reason": "baixa por perda", "lines": [{"item_id": i, "warehouse_id": self.wh1, "qty": 4, "direction": "diminuir"}]}).json["id"]
        self.saida(i, 5); self.assertEqual(self.api(self.admin, "POST", f"/api/adjustment-requests/{rid}/approve").status_code, 409)
        self.assertEqual(self.conn.execute("SELECT status FROM adjustment_requests WHERE id=?", (rid,)).fetchone()[0], "pendente")

class IsolationAndFilterTests(Base):
    def test_cross_company_blocked(self):
        i = self.item(); self.entrada(i, 5)
        unB = self.conn.execute("SELECT id FROM units WHERE company_id=?", (self.cidB,)).fetchone()[0]; whB = self.conn.execute("SELECT id FROM warehouses WHERE company_id=?", (self.cidB,)).fetchone()[0]
        self.assertEqual(self.entrada(i, 1, c=self.adminB, wh=whB).status_code, 400)
        iB = self.item("B1", c=self.adminB, unit_id=unB); self.assertEqual(self.entrada(iB, 1, wh=self.wh1).status_code, 400)
        m = self.api(self.admin, "GET", "/api/movements").json["items"][0]["id"]
        self.assertEqual(self.api(self.adminB, "GET", f"/api/movements/{m}").status_code, 404)
        self.assertEqual(self.mv("estorno", self.adminB, movement_id=m, reason="invasão tentada").status_code, 404)
        self.assertEqual(self.api(self.adminB, "GET", "/api/movements").json["total"], 0); self.assertEqual(self.api(self.adminB, "GET", f"/api/stock/{i}").status_code, 404)
    def test_filters_and_dates(self):
        a, b = self.item("A"), self.item("B"); self.entrada(a, 5); self.entrada(b, 5, wh=self.wh2); self.saida(a, 1)
        g = lambda q: self.api(self.admin, "GET", "/api/movements?" + q).json["total"]
        self.assertEqual((g("kind=saida"), g(f"item_id={b}"), g(f"warehouse_id={self.wh2}"), g(f"user_id={self.uidA}")), (1, 1, 1, 3))
        self.assertEqual((g(f"from={date.today() + timedelta(days=2)}"), g(f"from={date.today() - timedelta(days=2)}&to={date.today() + timedelta(days=2)}")), (0, 3))
        self.assertEqual(self.api(self.admin, "GET", "/api/movements?from=ontem").status_code, 400); self.assertEqual(self.api(self.admin, "GET", "/api/movements?size=x").status_code, 400)
        self.assertEqual(self.api(self.admin, "GET", "/api/movements?size=2&page=2").json["items"].__len__(), 1)

class ReconcileTests(Base):
    def test_detects_tampered_balance(self):
        i = self.item(); self.entrada(i, 5); self.consistent()
        self.conn.execute("UPDATE stock_balances SET qty_milli=qty_milli+1"); bad = stock.reconcile(self.conn)
        self.assertEqual((len(bad), bad[0]["tipo"]), (1, "saldo"))
    def test_cli_reconcile(self):
        import subprocess, sys
        i = self.item(); self.entrada(i, 5); env = dict(os.environ, GESTOK_DATABASE_PATH=self.path); root = os.path.dirname(os.path.dirname(__file__))
        self.assertEqual(subprocess.run([sys.executable, "manage.py", "reconcile"], cwd=root, env=env, capture_output=True).returncode, 0)
        self.conn.execute("UPDATE stock_balances SET qty_milli=0")
        self.assertEqual(subprocess.run([sys.executable, "manage.py", "reconcile"], cwd=root, env=env, capture_output=True).returncode, 1)

class PageTests(Base):
    def test_page_renders_without_inline_script(self):
        h = self.admin.get("/movimentacoes").get_data(as_text=True); self.assertIn("Nova movimentação", h); self.assertNotRegex(h, r"<script>(?!</script>)")
        col, _ = self.mk("colaborador", "c@a.com"); self.assertNotIn("Ajuste</option>", col.get("/movimentacoes").get_data(as_text=True))

if __name__ == "__main__":
    unittest.main()
