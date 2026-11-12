import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
try: from test_stage5 import Base
except ImportError: from tests.test_stage5 import Base
from app import accounts as acc

BEL = ZoneInfo("America/Belem")

class B6(Base):
    def today(self): return datetime.now(timezone.utc).astimezone(BEL).date()
    def set_last(self, item, days_ago, wh=None, hour=12, minute=0):
        """Define a última movimentação como 'há N dias' (data LOCAL de Belém, hora local escolhida)."""
        d = self.today() - timedelta(days=days_ago)
        utc = datetime(d.year, d.month, d.day, hour, minute, tzinfo=BEL).astimezone(timezone.utc)
        self.conn.execute("UPDATE stock_balances SET last_movement_at=? WHERE item_id=? AND warehouse_id=?", (acc.iso(utc), item, wh or self.wh1))
    def stale(self, qs="", c=None): return self.api(c or self.admin, "GET", "/api/stale" + ("?" + qs if qs else ""))

class StaleRuleTests(B6):
    def test_boundary_59_60_61_days(self):
        a, b, c = self.item("A"), self.item("B"), self.item("C")
        for i, d in ((a, 59), (b, 60), (c, 61)): self.entrada(i, 1, "10"); self.set_last(i, d)
        j = self.stale().json
        self.assertEqual({x["code"] for x in j["items"]}, {"B", "C"})
        self.assertEqual({x["code"]: x["days_stalled"] for x in j["items"]}, {"B": 60, "C": 61})
        self.assertEqual((j["stale_days"], j["count"], [x["code"] for x in j["items"]]), (60, 2, ["C", "B"]))   # mais antigo primeiro
    def test_zero_or_negative_balance_never_stale(self):
        a, b = self.item("A"), self.item("B"); self.entrada(a, 3); self.saida(a, 3); self.set_last(a, 200)
        self.conn.execute("INSERT INTO settings(company_id,key,value) VALUES (?,?,?)", (self.cidA, "allow_negative_stock", "1"))
        self.saida(b, 2); self.set_last(b, 200)
        self.assertEqual(self.stale().json["count"], 0)
    def test_items_without_history_are_listed_not_dated(self):
        a = self.item("SEM"); b = self.item("COM"); self.entrada(b, 1); self.set_last(b, 90)
        j = self.stale().json
        self.assertEqual([x["code"] for x in j["items"]], ["COM"]); self.assertEqual([x["code"] for x in j["no_history"]], ["SEM"])
    def test_editing_or_viewing_item_does_not_reset_clock(self):
        a = self.item("A"); self.entrada(a, 1); self.set_last(a, 70)
        before = self.conn.execute("SELECT last_movement_at FROM stock_balances WHERE item_id=?", (a,)).fetchone()[0]
        cat = self.api(self.admin, "POST", "/api/categories", {"name": "X"}).json["id"]
        self.api(self.admin, "PATCH", f"/api/items/{a}", {"name": "Outro nome", "category_id": cat, "description": "d", "min_qty": 5}); self.api(self.admin, "GET", f"/api/items/{a}"); self.api(self.admin, "GET", f"/api/stock/{a}")
        self.assertEqual(self.conn.execute("SELECT last_movement_at FROM stock_balances WHERE item_id=?", (a,)).fetchone()[0], before)
        self.assertEqual(self.stale().json["count"], 1)
    def test_real_movement_resets_clock_per_warehouse(self):
        a = self.item("A"); self.entrada(a, 4); self.set_last(a, 80)
        self.mv("transferencia", lines=[{"item_id": a, "from_warehouse_id": self.wh1, "to_warehouse_id": self.wh2, "qty": 1}])
        self.set_last(a, 80, self.wh1)   # origem volta a ficar velha; destino recebeu movimento agora
        j = self.stale().json; self.assertEqual([(x["warehouse_name"]) for x in j["items"]], ["Depósito principal"])
        self.saida(a, 1); self.assertEqual(self.stale().json["count"], 0)
    def test_timezone_uses_company_local_calendar_day(self):
        a, b = self.item("A"), self.item("B"); self.entrada(a, 1); self.entrada(b, 1)
        # 23:30 local do dia -60 já é o dia seguinte em UTC; ainda assim são 60 dias locais -> parado
        self.set_last(a, 60, hour=23, minute=30); self.set_last(b, 59, hour=0, minute=30)   # 00:30 local do dia -59 -> 59 dias
        self.assertEqual({x["code"]: x["days_stalled"] for x in self.stale().json["items"]}, {"A": 60})
    def test_valuation_rounding_and_sale_price_ignored(self):
        kg = self.api(self.admin, "POST", "/api/units", {"code": "KG", "name": "Quilo", "allow_fraction": True}).json["id"]
        a = self.item("K", unit_id=kg, sale_price="999"); self.entrada(a, "2,5", "3,3333"); self.set_last(a, 61)
        r = self.stale().json["items"][0]; self.assertEqual((r["value_cents"], r["value_display"]), (833, "R$ 8,33"))   # 2,5 x 3,3333 = 8,33325
        h = self.item("H"); self.entrada(h, 1, "0,005"); self.set_last(h, 61)
        self.assertEqual({x["code"]: x["value_cents"] for x in self.stale().json["items"]}["H"], 1)                       # 0,5 centavo -> 1 (meia-unidade p/ cima)
    def test_filters_sort_pagination(self):
        cat = self.api(self.admin, "POST", "/api/categories", {"name": "Ferr"}).json["id"]
        a = self.item("A", category_id=cat); b = self.item("B", item_type="produto"); c = self.item("C")
        self.entrada(a, 1, "10"); self.entrada(b, 1, "50", wh=self.wh2); self.entrada(c, 1, "1")
        for i, d, w in ((a, 70, self.wh1), (b, 90, self.wh2), (c, 65, self.wh1)): self.set_last(i, d, w)
        t = lambda qs: [x["code"] for x in self.stale(qs).json["items"]]
        self.assertEqual((t(f"category_id={cat}"), t(f"warehouse_id={self.wh2}"), t("item_type=produto"), t("sort=value"), t("sort=name")), (["A"], ["B"], ["B"], ["B", "A", "C"], ["A", "B", "C"]))
        lf, lt = self.today() - timedelta(days=75), self.today() - timedelta(days=66)
        self.assertEqual(t(f"last_from={lf}&last_to={lt}"), ["A"])
        j = self.stale("size=2&page=2").json; self.assertEqual((len(j["items"]), j["count"]), (1, 3))
        for qs in ("sort=x", "category_id=a", "item_type=z", "last_from=ontem", "size=x"): self.assertEqual(self.stale(qs).status_code, 400, qs)

class ValuationTests(B6):
    def test_totals_are_consistent_across_every_view(self):
        cat = self.api(self.admin, "POST", "/api/categories", {"name": "Ferr"}).json["id"]
        kg = self.api(self.admin, "POST", "/api/units", {"code": "KG", "name": "Quilo", "allow_fraction": True}).json["id"]
        a = self.item("A", category_id=cat); b = self.item("B", unit_id=kg); c = self.item("C", item_type="produto")
        self.entrada(a, 3, "2,50"); self.entrada(b, "1,5", "10"); self.entrada(c, 7, "0,33", wh=self.wh2)
        for i, w in ((a, self.wh1), (b, self.wh1), (c, self.wh2)): self.set_last(i, 100, w)
        v = self.api(self.admin, "GET", "/api/valuation").json
        self.assertEqual(v["total_cents"], 750 + 1500 + 231)
        for k in ("by_category", "by_warehouse", "by_type"): self.assertEqual(sum(x["value_cents"] for x in v[k]), v["total_cents"], k)
        s = self.stale().json; al = self.api(self.admin, "GET", "/api/alerts").json["summary"]
        self.assertEqual((s["value_cents"], al["stale_value_cents"], al["stale"], s["count"]), (v["total_cents"], v["total_cents"], 3, 3))
        self.assertEqual(sum(x["value_cents"] for x in self.stale().json["items"]), v["total_cents"])
    def test_quantities_never_summed_across_units(self):
        kg = self.api(self.admin, "POST", "/api/units", {"code": "KG", "name": "Quilo", "allow_fraction": True}).json["id"]
        a, b = self.item("A"), self.item("B", unit_id=kg); self.entrada(a, 3, "1"); self.entrada(b, "2,5", "1")
        sem = [x for x in self.api(self.admin, "GET", "/api/valuation").json["by_category"] if x["name"] == "Sem categoria"][0]
        self.assertEqual(sem["qty_by_unit"], {"UN": 3000, "KG": 2500})
    def test_negative_and_zero_balances_excluded(self):
        a = self.item("A"); self.entrada(a, 2, "5"); self.saida(a, 2)
        self.assertEqual(self.api(self.admin, "GET", "/api/valuation").json["total_cents"], 0)
    def test_average_cost_drives_value(self):
        a = self.item("A"); self.entrada(a, 10, "2"); self.entrada(a, 10, "4"); self.saida(a, 5)
        self.assertEqual(self.api(self.admin, "GET", "/api/valuation").json["total_cents"], 15 * 300)

class SettingsTests(B6):
    def test_configurable_threshold_authorized_and_audited(self):
        a = self.item("A"); self.entrada(a, 1); self.set_last(a, 35)
        self.assertEqual(self.api(self.admin, "GET", "/api/settings").json, {"stale_days": 60, "expiry_alert_days": 30, "allow_negative_stock": 0, "default_page_size": 20})
        self.assertEqual(self.stale().json["count"], 0)
        col, _ = self.mk("colaborador", "c@a.com"); ges, _ = self.mk("gestor", "g@a.com")
        for c in (col, ges): self.assertEqual(self.api(c, "PUT", "/api/settings", {"stale_days": 10}).status_code, 403)
        self.assertEqual(self.api(self.admin, "PUT", "/api/settings", {"stale_days": 30}).json["stale_days"], 30)
        self.assertEqual(self.stale().json["count"], 1)
        det = self.conn.execute("SELECT details FROM audit_log WHERE action='settings.updated'").fetchone()[0]; self.assertIn('"before": {"stale_days": 60}', det)
        self.assertEqual(self.stale(c=self.adminB).json["count"], 0); self.assertEqual(self.api(self.adminB, "GET", "/api/settings").json["stale_days"], 60)
    def test_invalid_settings_rejected(self):
        for body in ({"stale_days": 0}, {"stale_days": 4000}, {"stale_days": "x"}, {"expiry_alert_days": -1}, {"allow_negative_stock": 2}, {"senha": 1}, {}):
            self.assertEqual(self.api(self.admin, "PUT", "/api/settings", body).status_code, 400, body)
    def test_negative_stock_policy_toggle_is_enforced_by_engine(self):
        a = self.item("A"); self.entrada(a, 1)
        self.assertEqual(self.saida(a, 2).status_code, 409)
        self.api(self.admin, "PUT", "/api/settings", {"allow_negative_stock": True}); self.assertEqual(self.saida(a, 2).status_code, 201)
        self.api(self.admin, "PUT", "/api/settings", {"allow_negative_stock": 0}); self.assertEqual(self.saida(a, 1).status_code, 409)

class AlertTests(B6):
    def test_zero_replenish_negative_are_disjoint_and_ignore_inactive(self):
        z, r, ok, neg, off = (self.item(c, min_qty=m) for c, m in (("Z", 5), ("R", 5), ("OK", 5), ("N", 0), ("OFF", 5)))
        self.entrada(r, 5); self.entrada(ok, 6); self.entrada(neg, 1)
        self.conn.execute("INSERT INTO settings(company_id,key,value) VALUES (?,?,?)", (self.cidA, "allow_negative_stock", "1")); self.saida(neg, 3)
        self.api(self.admin, "PATCH", f"/api/items/{off}", {"active": False})
        s = self.api(self.admin, "GET", "/api/alerts").json["summary"]; self.assertEqual((s["zero"], s["replenish"], s["negative"]), (1, 1, 1))
        d = lambda t: [x["code"] for x in self.api(self.admin, "GET", f"/api/alerts?type={t}").json["items"]]
        self.assertEqual((d("zero"), d("replenish"), d("negative")), (["Z"], ["R"], ["N"]))
        self.assertEqual(self.api(self.admin, "GET", "/api/items?below_min=1").json["total"], 2)    # filtro da lista = mínimo>0 e saldo<=mínimo: inclui Z (zerado) e R
    def test_expiry_alerts_with_configurable_window(self):
        i = self.item("L", tracks_lot=True, tracks_expiry=True); f = lambda days: (date.today() + timedelta(days=days)).isoformat()
        self.entrada(i, 1, lot_code="SOON", expiry_date=f(10)); self.entrada(i, 1, lot_code="LATER", expiry_date=f(45)); self.entrada(i, 1, lot_code="OLD", expiry_date=f(5))
        self.conn.execute("UPDATE lots SET expiry_date=? WHERE lot_code='OLD'", (f(-3),))
        s = lambda: self.api(self.admin, "GET", "/api/alerts").json["summary"]
        self.assertEqual((s()["expiring"], s()["expired"]), (1, 1))
        self.assertEqual([x["lot_code"] for x in self.api(self.admin, "GET", "/api/alerts?type=expired").json["items"]], ["OLD"])
        self.api(self.admin, "PUT", "/api/settings", {"expiry_alert_days": 60}); self.assertEqual(s()["expiring"], 2)
        self.saida(i, 1, lot_code="SOON"); self.assertEqual(s()["expiring"], 1)     # lote sem saldo deixa de alertar
    def test_attention_counters_and_detail_pagination(self):
        a = self.item("A"); self.entrada(a, 1); self.saida(a, 9); col, _ = self.mk("colaborador", "c@a.com"); self.api(col, "GET", "/api/users")
        att = self.api(self.admin, "GET", "/api/alerts").json["summary"]["attention_7d"]; self.assertEqual((att["operacoes_rejeitadas"], att["acessos_negados"]), (1, 1))
        for k in range(3): self.item(f"Z{k}")
        j = self.api(self.admin, "GET", "/api/alerts?type=zero&size=2&page=2").json; self.assertEqual((j["total"], len(j["items"])), (3, 1))
        self.assertEqual(self.api(self.admin, "GET", "/api/alerts?type=foo").status_code, 400)

class AccessTests(B6):
    def test_permissions_and_isolation(self):
        a = self.item("A"); self.entrada(a, 1); self.set_last(a, 90)
        col, _ = self.mk("colaborador", "c@a.com"); ges, _ = self.mk("gestor", "g@a.com")
        for u in ("/api/stale", "/api/valuation", "/api/alerts"):
            self.assertEqual(self.api(col, "GET", u).status_code, 403, u); self.assertEqual(self.api(ges, "GET", u).status_code, 200, u)
            self.assertEqual(self.app.test_client().get(u).status_code, 401, u)
            self.assertEqual(self.api(self.adminB, "GET", u).status_code, 200)
        self.assertEqual(self.stale(c=self.adminB).json["count"], 0); self.assertEqual(self.api(self.adminB, "GET", "/api/valuation").json["total_cents"], 0)
        self.assertEqual(col.get("/estoque-parado").status_code, 403)
        h = self.admin.get("/estoque-parado").get_data(as_text=True); self.assertIn("Estoque parado", h)
        import re; self.assertIsNone(re.search(r"<script>(?!</script>)", h))

if __name__ == "__main__":
    unittest.main()
