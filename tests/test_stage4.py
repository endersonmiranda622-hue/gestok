import os, re, shutil, tempfile, unittest
from app import create_app, MIGRATIONS_DIR
from app import db as dbm, accounts as acc
from app.numbers import NumError, parse_qty, parse_money, fmt_qty, fmt_money

PW, PW2 = "Senha-forte-123", "Outra-senha-456"

class NumberTests(unittest.TestCase):
    def test_parsing(self):
        self.assertEqual(parse_qty("1,5"), 1500); self.assertEqual(parse_qty(2), 2000); self.assertEqual(parse_qty("1.234,567"), 1234567)
        self.assertEqual(parse_money("10,50"), 105000); self.assertEqual(parse_money(0.1), 1000); self.assertEqual(parse_money("0,0001"), 1)
    def test_rejections(self):
        for bad in ("-1", "abc", "", None, True, "NaN", "Infinity", "1e12", "99999999999"):
            with self.assertRaises(NumError): parse_qty(bad)
        with self.assertRaises(NumError): parse_qty("1,0001")
        with self.assertRaises(NumError): parse_money("1,00001")
        with self.assertRaises(NumError): parse_qty("1,5", allow_fraction=False)
    def test_formatting_ptbr(self):
        self.assertEqual(fmt_qty(1500), "1,5"); self.assertEqual(fmt_qty(1234000), "1.234"); self.assertEqual(fmt_qty(0), "0")
        self.assertEqual(fmt_money(105000), "R$ 10,50"); self.assertEqual(fmt_money(12345600), "R$ 1.234,56"); self.assertEqual(fmt_money(1), "R$ 0,0001")

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.path = os.path.join(self.tmp, "t.db")
        self.conn = dbm.connect(self.path); dbm.migrate(self.conn, MIGRATIONS_DIR)
        self.app = create_app({"DATABASE": self.path, "TESTING": True, "SECRET_KEY": "k" * 40})
        self.cidA, self.uidA = acc.create_company_with_admin(self.conn, "A", "Ana", "ana@a.com", PW)
        self.cidB, _ = acc.create_company_with_admin(self.conn, "B", "Bruno", "bruno@b.com", PW)
        self.admin, self.adminB = self.client("ana@a.com", PW), self.client("bruno@b.com", PW)
        self.unA = self.conn.execute("SELECT id FROM units WHERE company_id=?", (self.cidA,)).fetchone()[0]
        self.unB = self.conn.execute("SELECT id FROM units WHERE company_id=?", (self.cidB,)).fetchone()[0]
        self.whA = self.conn.execute("SELECT id FROM warehouses WHERE company_id=?", (self.cidA,)).fetchone()[0]
    def tearDown(self):
        self.conn.close(); shutil.rmtree(self.tmp)
    def tok(self, c, url="/login"):
        return re.search(r'name="csrf_token" value="([^"]+)"', c.get(url).get_data(as_text=True)).group(1)
    def client(self, email, pw):
        c = self.app.test_client(); c.post("/login", data={"email": email, "password": pw, "csrf_token": self.tok(c)}); return c
    def api(self, c, m, u, b=None):
        return c.open(u, method=m, json=b, headers={"X-CSRF-Token": self.tok(c, "/")})
    def mk(self, role, email):
        r = self.api(self.admin, "POST", "/api/users", {"name": "N", "email": email, "role": role}); c = self.app.test_client()
        c.post(r.json["reset_path"], data={"password": PW2, "csrf_token": self.tok(c, r.json["reset_path"])}); return self.client(email, PW2)
    def item(self, c=None, **kw):
        body = {"code": "p-001", "name": "Parafuso", "item_type": "material", "unit_id": self.unA, "cost": "1,50", "min_qty": 10, **kw}
        return self.api(c or self.admin, "POST", "/api/items", body)
    def add_movement(self, item_id, qty=5000):
        self.conn.execute("INSERT INTO stock_movements(id,company_id,kind,user_id,occurred_at) VALUES ('m1',?,'entrada',?,'2026-01-01T00:00:00Z')", (self.cidA, self.uidA))
        self.conn.execute("INSERT INTO movement_lines(movement_id,company_id,item_id,warehouse_id,qty_milli,unit_cost_e4) VALUES ('m1',?,?,?,?,15000)", (self.cidA, item_id, self.whA, qty))

class ItemCrudTests(Base):
    def test_create_edit_persist_and_audit(self):
        r = self.item(); self.assertEqual(r.status_code, 201); i = r.json
        self.assertEqual((i["code"], i["avg_cost_e4"], i["min_qty_milli"], i["cost_display"]), ("P-001", 15000, 10000, "R$ 1,50"))
        self.assertEqual(self.api(self.admin, "PATCH", f"/api/items/{i['id']}", {"name": "Parafuso M8", "sale_price": "3,25", "barcode": "789"}).json["price_display"], "R$ 3,25")
        fresh = create_app({"DATABASE": self.path, "TESTING": True, "SECRET_KEY": "k" * 40})   # "reinício" da aplicação
        c = fresh.test_client(); c.post("/login", data={"email": "ana@a.com", "password": PW, "csrf_token": self.tok(c)})
        self.assertEqual(c.get(f"/api/items/{i['id']}").json["name"], "Parafuso M8")
        acts = [r[0] for r in self.conn.execute("SELECT action FROM audit_log WHERE entity='item'")]
        self.assertEqual(sorted(acts), ["item.created", "item.updated"])
    def test_duplicate_code_case_insensitive_and_barcode(self):
        self.assertEqual(self.item(barcode="111").status_code, 201)
        self.assertEqual(self.item(code="P-001", name="x").status_code, 409)
        self.assertEqual(self.item(code="p-001", name="x").status_code, 409)
        self.assertEqual(self.item(code="P-002", barcode="111").status_code, 409)
        self.assertEqual(self.item(self.adminB, code="P-001", unit_id=self.unB).status_code, 201)  # outra empresa pode repetir
    def test_validation(self):
        bad = [{"cost": "-1"}, {"min_qty": "1,5555"}, {"min_qty": "1,5"}, {"sale_price": "abc"}, {"item_type": "servico"}, {"name": " "}, {"code": "a b"},
               {"code": ""}, {"barcode": "12 34"}, {"unit_id": None}, {"unit_id": 99999}, {"category_id": 99999}, {"default_warehouse_id": 99999}]
        for i, b in enumerate(bad):
            r = self.item(**{"code": f"X{i}", **b}); self.assertEqual(r.status_code, 400, (b, r.json))
        self.assertEqual(self.api(self.admin, "POST", "/api/items", None).status_code, 400)
    def test_fraction_allowed_only_with_fractional_unit(self):
        kg = self.api(self.admin, "POST", "/api/units", {"code": "kg", "name": "Quilo", "allow_fraction": True}).json["id"]
        self.assertEqual(self.item(code="A1", unit_id=kg, min_qty="2,5").status_code, 201)
        self.assertEqual(self.item(code="A2", unit_id=self.unA, min_qty="2,5").status_code, 400)
    def test_cannot_reference_other_company_data(self):
        cat = self.api(self.adminB, "POST", "/api/categories", {"name": "Ferr"}).json["id"]
        self.assertEqual(self.item(code="Z1", category_id=cat).status_code, 400)
        self.assertEqual(self.item(code="Z2", unit_id=self.unB).status_code, 400)
        r = self.item(code="Z3", company_id=self.cidB)
        self.assertEqual(self.conn.execute("SELECT company_id FROM items WHERE code='Z3'").fetchone()[0], self.cidA)
    def test_code_immutable_and_no_delete_endpoint(self):
        i = self.item().json["id"]
        self.assertEqual(self.api(self.admin, "PATCH", f"/api/items/{i}", {"code": "OUTRO"}).status_code, 400)
        self.assertEqual(self.api(self.admin, "DELETE", f"/api/items/{i}").status_code, 405)
    def test_deactivate_keeps_record_and_hides_by_default(self):
        i = self.item().json["id"]
        self.assertEqual(self.api(self.admin, "PATCH", f"/api/items/{i}", {"active": False}).json["active"], 0)
        self.assertEqual(self.api(self.admin, "GET", "/api/items").json["total"], 0)
        self.assertEqual(self.api(self.admin, "GET", "/api/items?active=all").json["total"], 1)
        self.assertEqual(self.api(self.admin, "GET", f"/api/items/{i}").status_code, 200)
    def test_movements_lock_unit_type_and_cost(self):
        i = self.item().json["id"]; self.add_movement(i)
        for body in ({"item_type": "produto"}, {"cost": "9"}, {"unit_id": self.api(self.admin, "POST", "/api/units", {"code": "CX", "name": "Caixa"}).json["id"]}):
            self.assertEqual(self.api(self.admin, "PATCH", f"/api/items/{i}", body).status_code, 409, body)
        self.assertEqual(self.api(self.admin, "PATCH", f"/api/items/{i}", {"name": "Novo nome"}).status_code, 200)
    def test_current_quantity_is_sum_of_balances(self):
        i = self.item().json["id"]
        w2 = self.api(self.admin, "POST", "/api/warehouses", {"name": "Filial"}).json["id"]
        self.conn.executemany("INSERT INTO stock_balances(company_id,item_id,warehouse_id,qty_milli) VALUES (?,?,?,?)", [(self.cidA, i, self.whA, 4000), (self.cidA, i, w2, 2500)])
        it = self.api(self.admin, "GET", f"/api/items/{i}").json
        self.assertEqual((it["qty_milli"], it["qty_display"]), (6500, "6,5"))
        self.assertEqual(self.api(self.admin, "GET", "/api/items?below_min=1").json["total"], 1)   # 6,5 <= mínimo 10
        self.assertEqual(self.api(self.admin, "GET", "/api/items?below_min=1&q=zzz").json["total"], 0)

class SearchTests(Base):
    def setUp(self):
        super().setUp()
        cat = self.api(self.admin, "POST", "/api/categories", {"name": "Ferragens"}).json["id"]
        self.item(code="A1", name="Parafuso", barcode="7891", category_id=cat); self.item(code="B2", name="Tinta azul", item_type="produto")
        self.item(code="C3", name="100% algodão")
    def total(self, qs): return self.api(self.admin, "GET", "/api/items?" + qs).json["total"]
    def test_search_fields(self):
        self.assertEqual((self.total("q=a1"), self.total("q=7891"), self.total("q=tinta"), self.total("q=ferrag")), (1, 1, 1, 1))
    def test_like_wildcards_are_literal(self):
        self.assertEqual((self.total("q=%25"), self.total("q=_")), (1, 0))
    def test_filters_sort_pagination(self):
        self.assertEqual(self.total("item_type=produto"), 1)
        j = self.api(self.admin, "GET", "/api/items?sort=code&dir=desc&size=2").json
        self.assertEqual(([x["code"] for x in j["items"]], j["total"]), (["C3", "B2"], 3))
        self.assertEqual(len(self.api(self.admin, "GET", "/api/items?sort=code&size=2&page=2").json["items"]), 1)
    def test_bad_params_rejected_not_500(self):
        for qs in ("sort=senha", "size=abc", "active=talvez"): self.assertEqual(self.api(self.admin, "GET", "/api/items?" + qs).status_code, 400, qs)
        self.assertEqual(self.api(self.admin, "GET", "/api/items?q=%27%20OR%201=1--").json["total"], 0)
    def test_search_never_leaks_other_company(self):
        self.item(self.adminB, code="A1", unit_id=self.unB, name="Parafuso B")
        self.assertEqual({x["name"] for x in self.api(self.admin, "GET", "/api/items?q=parafuso").json["items"]}, {"Parafuso"})

class PermissionTests(Base):
    def test_matrix(self):
        col, ges = self.mk("colaborador", "c@a.com"), self.mk("gestor", "g@a.com"); i = self.item().json["id"]
        for c in (col, ges):
            self.assertEqual(self.api(c, "GET", "/api/items").status_code, 200)
            self.assertEqual(self.api(c, "POST", "/api/items", {"code": "N"}).status_code, 403)
            self.assertEqual(self.api(c, "PATCH", f"/api/items/{i}", {"name": "x"}).status_code, 403)
            for t in ("categories", "warehouses", "units"): self.assertEqual(self.api(c, "POST", f"/api/{t}", {"name": "x", "code": "x"}).status_code, 403)
        self.api(self.admin, "PUT", "/api/permissions/gestor", {"permissions": ["items.view", "items.edit"]})
        self.assertEqual(self.api(ges, "PATCH", f"/api/items/{i}", {"name": "Editado"}).status_code, 200)
        self.assertEqual(self.app.test_client().get("/api/items").status_code, 401)
        self.assertEqual(col.get("/cadastros").status_code, 403); self.assertEqual(col.get("/materiais").status_code, 200)

class SimpleCatalogTests(Base):
    def test_duplicates_and_company_scope(self):
        self.assertEqual(self.api(self.admin, "POST", "/api/categories", {"name": "Ferr"}).status_code, 201)
        self.assertEqual(self.api(self.admin, "POST", "/api/categories", {"name": "Ferr"}).status_code, 409)
        self.assertEqual(self.api(self.adminB, "POST", "/api/categories", {"name": "Ferr"}).status_code, 201)
        self.assertEqual({c["name"] for c in self.api(self.admin, "GET", "/api/categories").json["items"]}, {"Ferr"})
        other = self.conn.execute("SELECT id FROM categories WHERE company_id=?", (self.cidB,)).fetchone()[0]
        self.assertEqual(self.api(self.admin, "PATCH", f"/api/categories/{other}", {"name": "hack"}).status_code, 404)
    def test_inactive_category_not_selectable_but_history_stays(self):
        c = self.api(self.admin, "POST", "/api/categories", {"name": "Old"}).json["id"]; i = self.item(category_id=c).json["id"]
        self.api(self.admin, "PATCH", f"/api/categories/{c}", {"active": False})
        self.assertEqual(self.api(self.admin, "GET", f"/api/items/{i}").json["category_name"], "Old")
        self.assertEqual(self.item(code="N2", category_id=c).status_code, 400)
        self.assertEqual(self.api(self.admin, "PATCH", f"/api/items/{i}", {"name": "ok"}).status_code, 200)   # mantém categoria já inativa
    def test_warehouse_with_stock_cannot_be_deactivated(self):
        i = self.item().json["id"]
        self.conn.execute("INSERT INTO stock_balances(company_id,item_id,warehouse_id,qty_milli) VALUES (?,?,?,?)", (self.cidA, i, self.whA, 1000))
        self.assertEqual(self.api(self.admin, "PATCH", f"/api/warehouses/{self.whA}", {"active": False}).status_code, 409)
        w = self.api(self.admin, "POST", "/api/warehouses", {"name": "Vazio"}).json["id"]
        self.assertEqual(self.api(self.admin, "PATCH", f"/api/warehouses/{w}", {"active": False}).status_code, 200)
    def test_used_unit_is_protected(self):
        self.item()
        self.assertEqual(self.api(self.admin, "PATCH", f"/api/units/{self.unA}", {"allow_fraction": True}).status_code, 409)
        self.assertEqual(self.api(self.admin, "PATCH", f"/api/units/{self.unA}", {"active": False}).status_code, 409)
        self.assertEqual(self.api(self.admin, "PATCH", f"/api/units/{self.unA}", {"name": "Unidade comum"}).status_code, 200)
    def test_unit_code_normalized_and_validated(self):
        self.assertEqual(self.api(self.admin, "POST", "/api/units", {"code": "kg", "name": "Quilo"}).json["code"], "KG")
        self.assertEqual(self.api(self.admin, "POST", "/api/units", {"code": "muitolongo", "name": "x"}).status_code, 400)
        self.assertEqual(self.api(self.admin, "POST", "/api/units", {"code": "KG", "name": "Outro"}).status_code, 409)

class PageTests(Base):
    def test_pages_render_and_have_no_inline_scripts(self):
        for u in ("/materiais", "/cadastros"):
            h = self.admin.get(u).get_data(as_text=True); self.assertNotRegex(h, r"<script>(?!</script>)"); self.assertIn("csrf", h)
        self.assertIn("Cadastre o primeiro", self.admin.get("/materiais").get_data(as_text=True))

if __name__ == "__main__":
    unittest.main()
