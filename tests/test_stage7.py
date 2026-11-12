import re, unittest
from datetime import date, timedelta
try:
    from test_stage6 import B6
except ImportError:
    from tests.test_stage6 import B6
from app import dashboard as dash

class S7(B6):
    def scenario(self):
        """A (UN) e K (KG). Valores esperados (centavos): entradas 2000+4000+833; saída 1500 (a custo médio 3,00); ajuste -300; transferência fora."""
        cat = self.api(self.admin, "POST", "/api/categories", {"name": "Ferr"}).json["id"]
        kg = self.api(self.admin, "POST", "/api/units", {"code": "KG", "name": "Quilo", "allow_fraction": True}).json["id"]
        self.a, self.k = self.item("A", category_id=cat), self.item("K", unit_id=kg)
        self.entrada(self.a, 10, "2"); self.entrada(self.a, 10, "4"); self.sai = self.saida(self.a, 5).json["id"]
        self.ek = self.entrada(self.k, "2,5", "3,3333").json["id"]
        self.mv("transferencia", lines=[{"item_id": self.a, "from_warehouse_id": self.wh1, "to_warehouse_id": self.wh2, "qty": 2}])
        self.mv("ajuste", reason="contagem física", lines=[{"item_id": self.a, "warehouse_id": self.wh1, "qty": 1, "direction": "diminuir"}])
        return cat
    def dash(self, qs="", c=None): return self.api(c or self.admin, "GET", "/api/dashboard" + ("?" + qs if qs else ""))
    def rep(self, name, qs="", c=None): return self.api(c or self.admin, "GET", f"/api/reports/{name}" + ("?" + qs if qs else ""))

class DashboardNumbers(S7):
    def test_empty_company_is_safe_and_flagged(self):
        d = self.dash(c=self.adminB).json
        self.assertTrue(d["empty"]); self.assertEqual((d["cards"]["total_value_cents"], d["cards"]["items_total"], d["period_metrics"]["entradas_cents"]), (0, 0, 0))
        self.assertTrue(all(s["movements"] == 0 for s in d["series"])); self.assertEqual(d["by_category"], [])
    def test_period_metrics_definitions(self):
        self.scenario(); p = self.dash().json["period_metrics"]
        self.assertEqual((p["entradas_cents"], p["saidas_cents"], p["saldo_periodo_cents"], p["ajustes_cents"]), (6833, 1500, 5333, -300))
        self.assertEqual((p["entradas_qty_by_unit"], p["saidas_qty_by_unit"]), ({"UN": 20000, "KG": 2500}, {"UN": 5000}))   # unidades nunca somadas
        self.assertEqual(p["movements_by_kind"], {"entrada": 3, "saida": 1, "transferencia": 1, "ajuste": 1})
        self.assertEqual(p["entradas_display"], "R$ 68,33")
    def test_reversals_reduce_their_origin_totals(self):
        self.scenario()
        self.mv("estorno", movement_id=self.sai, reason="devolução ao estoque"); self.mv("estorno", movement_id=self.ek, reason="lançado em duplicidade")
        p = self.dash().json["period_metrics"]
        self.assertEqual((p["entradas_cents"], p["saidas_cents"], p["entradas_qty_by_unit"].get("KG"), p["saidas_qty_by_unit"].get("UN")), (6000, 0, 0, 0))
    def test_all_views_share_the_same_total(self):
        self.scenario(); d = self.dash().json; total = 4200 + 833
        v = self.api(self.admin, "GET", "/api/valuation").json; pos = self.rep("position", "size=200").json
        self.assertEqual((d["cards"]["total_value_cents"], v["total_cents"], pos["totals"]["value_cents"], sum(r["value_cents"] for r in pos["rows"])), (total,) * 4)
        self.assertEqual(sum(x["value_cents"] for x in d["by_category"]), total)
        self.assertEqual(self.rep("valuation").json["totals"]["value_cents"], total)
    def test_stale_figures_match_stale_endpoint_and_alerts(self):
        self.scenario()
        for i, w in ((self.a, self.wh1), (self.a, self.wh2), (self.k, self.wh1)): self.set_last(i, 75, w)
        c = self.dash().json["cards"]; s = self.stale().json; r = self.rep("stale", "size=200").json
        self.assertEqual((c["stale_value_cents"], c["stale_count"], c["stale_days"]), (s["value_cents"], s["count"], 60)); self.assertEqual((r["totals"]["value_cents"], r["total_rows"]), (s["value_cents"], s["count"]))
        self.assertEqual(s["value_cents"], 4200 + 833)
    def test_series_totals_equal_period_totals_and_granularity(self):
        self.scenario(); d = self.dash().json; p = d["period_metrics"]
        self.assertEqual((sum(s["entradas_cents"] for s in d["series"]), sum(s["saidas_cents"] for s in d["series"]), sum(s["movements"] for s in d["series"])), (6833, 1500, 6))
        self.assertEqual(d["period"]["granularity"], "dia")
        wide = self.dash(f"from={date.today() - timedelta(days=150)}&to={date.today() + timedelta(days=1)}").json
        self.assertEqual(wide["period"]["granularity"], "mes"); self.assertEqual(sum(s["entradas_cents"] for s in wide["series"]), 6833); self.assertTrue(all(len(s["bucket"]) == 7 for s in wide["series"]))
    def test_period_filter_and_validation(self):
        self.scenario(); far = date.today() + timedelta(days=5)
        self.assertEqual(self.dash(f"from={far}&to={far}").json["period_metrics"]["entradas_cents"], 0)
        self.assertEqual(self.dash(f"from={date.today()}&to={date.today() + timedelta(days=1)}").json["period_metrics"]["entradas_cents"], 6833)
        for qs in ("from=ontem", "from=2026-05-02&to=2026-05-01", f"from={date.today() - timedelta(days=400)}&to={date.today()}"): self.assertEqual(self.dash(qs).status_code, 400, qs)
    def test_bucket_uses_company_timezone(self):
        self.assertEqual(dash.bucket(self.conn, self.cidA, "2026-03-10T02:30:00.000Z", False), "2026-03-09")
        self.assertEqual(dash.bucket(self.conn, self.cidA, "2026-03-01T02:00:00.000Z", True), "2026-02")
        self.assertEqual(dash._buckets(date(2026, 1, 31), date(2026, 3, 2), True), ["2026-01", "2026-02", "2026-03"]); self.assertEqual(len(dash._buckets(date(2026, 2, 27), date(2026, 3, 2), False)), 4)
    def test_alert_cards_and_expiring_list(self):
        i = self.item("L", tracks_lot=True, tracks_expiry=True, min_qty=5); self.item("Z", min_qty=1)
        self.entrada(i, 2, lot_code="L1", expiry_date=(date.today() + timedelta(days=10)).isoformat())
        d = self.dash().json; self.assertEqual((d["cards"]["replenish"], d["cards"]["zero"], d["cards"]["expiring"], d["cards"]["items_total"], d["cards"]["items_active"]), (1, 1, 1, 2, 2))
        self.assertEqual(d["expiring_soon"][0]["lot_code"], "L1"); self.assertFalse(d["empty"])

class ReportTests(S7):
    def test_every_report_has_stable_shape(self):
        self.scenario(); names = [r["name"] for r in self.api(self.admin, "GET", "/api/reports").json["items"]]
        self.assertEqual(len(names), 10)
        for n in names:
            r = self.rep(n); self.assertEqual(r.status_code, 200, n); j = r.json
            self.assertTrue(all(k in j for k in ("columns", "rows", "total_rows", "totals", "title", "page", "size")), n)
            for row in j["rows"]: self.assertTrue(all(c["key"] in row for c in j["columns"]), (n, row))
    def test_entries_exits_history_adjustments(self):
        self.scenario(); e, x, h, a = (self.rep(n, "size=200").json for n in ("entries", "exits", "history", "adjustments"))
        self.assertEqual((e["total_rows"], e["totals"]["value_cents"], x["total_rows"], x["totals"]["value_cents"], h["total_rows"], a["total_rows"]), (3, 6833, 1, -1500, 7, 1))
        self.mv("estorno", movement_id=self.ek, reason="lançado em duplicidade")
        e, a = self.rep("entries", "size=200").json, self.rep("adjustments", "size=200").json
        self.assertEqual((e["total_rows"], e["totals"]["value_cents"], a["total_rows"]), (4, 6000, 2)); self.assertTrue(any(r["reverses_id"] == self.ek for r in a["rows"]))
    def test_filters(self):
        cat = self.scenario(); g = lambda n, qs: self.rep(n, qs).json["total_rows"]
        self.assertEqual((g("history", f"item_id={self.k}"), g("history", f"category_id={cat}"), g("history", f"warehouse_id={self.wh2}"), g("history", "kind=ajuste"), g("history", "item_type=material")), (1, 6, 1, 1, 7))
        self.assertEqual((g("history", f"user_id={self.uidA}"), g("history", "user_id=99999"), g("position", f"category_id={cat}"), g("position", f"warehouse_id={self.wh2}")), (7, 0, 2, 1))
        far = date.today() + timedelta(days=9); self.assertEqual(g("history", f"from={far}&to={far}"), 0)
        for n, qs in (("history", "kind=x"), ("history", "item_id=a"), ("history", "item_type=z"), ("history", "from=x"), ("position", "category_id=a")): self.assertEqual(self.rep(n, qs).status_code, 400, (n, qs))
    def test_pagination(self):
        self.scenario(); j = self.rep("history", "size=3&page=3").json; self.assertEqual((len(j["rows"]), j["total_rows"]), (1, 7))
        self.assertEqual(self.rep("history", "size=x").status_code, 400)
    def test_by_user_and_lots_and_replenish(self):
        self.scenario(); col, cid = self.mk("colaborador", "c@a.com"); self.entrada(self.a, 1, "3", c=col)
        r = {x["user_name"]: x for x in self.rep("by_user").json["rows"]}
        self.assertEqual((r["Ana"]["movements"], r["Ana"]["entradas_cents"], r["Ana"]["saidas_cents"], r["Ana"]["ajustes"], r["N colaborador"]["entradas_cents"]), (6, 6833, 1500, 1, 300))
        self.assertEqual(self.rep("by_user", f"user_id={cid}").json["total_rows"], 1)
        li = self.item("L", tracks_lot=True, tracks_expiry=True, min_qty=9); self.entrada(li, 2, lot_code="L1", expiry_date=(date.today() + timedelta(days=10)).isoformat())
        lots = self.rep("lots").json["rows"]; self.assertEqual((len(lots), lots[0]["days_to_expiry"], lots[0]["qty_milli"]), (1, 10, 2000))
        self.assertEqual([x["item_code"] for x in self.rep("replenish").json["rows"]], ["L"])
    def test_permissions_and_isolation(self):
        self.scenario(); col, _ = self.mk("colaborador", "c@a.com"); ges, _ = self.mk("gestor", "g@a.com")
        for u in ("/api/dashboard", "/api/reports", "/api/reports/history"):
            self.assertEqual(self.api(col, "GET", u).status_code, 403, u); self.assertEqual(self.api(ges, "GET", u).status_code, 200, u); self.assertEqual(self.app.test_client().get(u).status_code, 401, u)
        self.assertEqual(self.rep("nope").status_code, 404)
        for n in ("history", "position", "entries", "by_user", "stale", "valuation", "lots", "replenish"): self.assertEqual(self.rep(n, c=self.adminB).json["total_rows"] if n not in ("valuation",) else 0, 0, n)
        self.assertTrue(self.dash(c=self.adminB).json["empty"])

class PageTests7(S7):
    def test_pages(self):
        col, _ = self.mk("colaborador", "c@a.com")
        h = self.admin.get("/").get_data(as_text=True); self.assertIn("Painel de controle", h); self.assertIn("Comece por aqui", h)
        self.assertNotIn("Painel de controle", col.get("/").get_data(as_text=True)); self.assertEqual(col.get("/relatorios").status_code, 403)
        for u in ("/", "/relatorios"):
            self.assertIsNone(re.search(r"<script>(?!</script>)", self.admin.get(u).get_data(as_text=True)), u)
        self.assertIn("Relatórios", self.admin.get("/relatorios").get_data(as_text=True))

if __name__ == "__main__":
    unittest.main()
