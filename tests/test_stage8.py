import csv, io, re, shutil, subprocess, tempfile, unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo
try:
    from test_stage7 import S7
except ImportError:
    from tests.test_stage7 import S7
from app import dashboard as dash, exports
from openpyxl import load_workbook

class X8(S7):
    def ex(self, name, fmt="csv", qs="", c=None):
        return (c or self.admin).get(f"/api/reports/{name}/export?format={fmt}" + ("&" + qs if qs else ""))
    def rows(self, r):
        return list(csv.reader(io.StringIO(r.data.decode("utf-8-sig")), delimiter=";"))

class CsvTests(X8):
    def test_format_headers_and_values(self):
        self.scenario(); r = self.ex("entries"); t = self.rows(r)
        self.assertEqual((r.status_code, r.headers["Content-Type"], r.data[:3]), (200, "text/csv; charset=utf-8", b"\xef\xbb\xbf"))
        self.assertRegex(r.headers["Content-Disposition"], r'^attachment; filename="gestok_entries_\d{8}\.csv"$')
        self.assertEqual((t[0][:3], len(t)), (["Data/hora", "Tipo", "Código"], 1 + 3 + 1))
        k = [x for x in t if x[2] == "K"][0]; self.assertEqual((k[7], k[9], k[10]), ("2,5", "3,3333", "8,33"))     # vírgula decimal, sem R$ nem milhar
        self.assertEqual((t[-1][0], t[-1][10]), ("TOTAL", "68,33"))
    def test_filters_and_period_respected(self):
        self.scenario()
        self.assertEqual(len(self.rows(self.ex("history", qs="kind=ajuste"))), 1 + 1 + 1)
        self.assertEqual(len(self.rows(self.ex("history", qs=f"item_id={self.k}"))), 1 + 1 + 1)
        far = date.today() + timedelta(days=9); t = self.rows(self.ex("history", qs=f"from={far}&to={far}")); self.assertEqual([x[0] for x in t[1:]], ["TOTAL"]); self.assertEqual(t[-1][10], "0,00")
    def test_datetimes_are_exported_in_company_local_time(self):
        self.scenario(); occ = self.api(self.admin, "GET", "/api/movements").json["items"][0]["occurred_at"]
        want = datetime.strptime(occ[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).astimezone(ZoneInfo("America/Belem")).strftime("%d/%m/%Y %H:%M")
        self.assertEqual(self.rows(self.ex("history"))[1][0], want)
    def test_formula_injection_neutralized(self):
        evil = ["=HYPERLINK(\"http://x\")", "+cmd|' /C calc'!A0", "@SUM(1)", "-2+3"]
        for n, name in enumerate(evil):
            i = self.item(f"E{n}", name=name); self.entrada(i, 1)
        self.mv("ajuste", reason="=1+1 motivo", lines=[{"item_id": self.item("E9"), "warehouse_id": self.wh1, "qty": 1, "direction": "aumentar"}])
        t = self.rows(self.ex("history", qs="size=200")); body = [c for row in t for c in row]
        for e in evil + ["=1+1 motivo"]: self.assertIn("'" + e, body); self.assertNotIn(e, body)
        self.assertTrue(all(not c.startswith(("=", "+", "@")) for c in [x[3] for x in t[1:]] + [x[12] for x in t[1:]]))

class XlsxPdfTests(X8):
    def test_xlsx_native_types_and_total(self):
        self.scenario(); r = self.ex("entries", "xlsx"); ws = load_workbook(io.BytesIO(r.data)).active
        self.assertEqual((r.status_code, r.data[:2], ws.title, ws["A1"].value), (200, b"PK", "Entradas", "Entradas"))
        self.assertEqual([c.value for c in ws[5]][:3], ["Data/hora", "Tipo", "Código"]); self.assertIsInstance(ws["A6"].value, datetime)
        vals = {ws.cell(row, 3).value: ws.cell(row, 11).value for row in range(6, 9)}; self.assertEqual(vals["K"], 8.33)
        self.assertEqual((ws.cell(9, 1).value, ws.cell(9, 11).value), ("TOTAL", 68.33)); self.assertEqual(ws.cell(6, 11).number_format, '"R$" #,##0.00')
    def test_xlsx_cells_are_never_formulas(self):
        for n, name in enumerate(["=1+1", "+1+1", "@A1", "-1+1"]): self.entrada(self.item(f"F{n}", name=name), 1)
        ws = load_workbook(io.BytesIO(self.ex("history", "xlsx", "size=200").data)).active
        names = [ws.cell(r, 4).value for r in range(6, ws.max_row + 1)]
        for e in ("=1+1", "+1+1", "@A1", "-1+1"): self.assertIn("'" + e, names)
        self.assertTrue(all(ws.cell(r, c).data_type != "f" for r in range(1, ws.max_row + 1) for c in range(1, 14)))
    def test_pdf_is_valid_and_has_content(self):
        self.scenario(); r = self.ex("history", "pdf")
        self.assertEqual((r.status_code, r.headers["Content-Type"], r.data[:4]), (200, "application/pdf", b"%PDF")); self.assertIn(b"%%EOF", r.data[-64:]); self.assertGreater(len(r.data), 1500)
    @unittest.skipUnless(shutil.which("pdftotext"), "pdftotext indisponível: conteúdo do PDF não verificado")
    def test_pdf_text(self):
        self.scenario(); p = tempfile.mktemp(suffix=".pdf"); open(p, "wb").write(self.ex("entries", "pdf").data)
        txt = subprocess.run(["pdftotext", "-layout", p, "-"], capture_output=True, text=True).stdout; self.assertIn("Entradas", txt); self.assertIn("R$ 68,33", txt)
    def test_every_report_in_every_format(self):
        self.scenario(); magic = {"csv": b"\xef\xbb\xbf", "xlsx": b"PK", "pdf": b"%PDF"}
        for n in [x["name"] for x in self.api(self.admin, "GET", "/api/reports").json["items"]]:
            for f, m in magic.items():
                r = self.ex(n, f); self.assertEqual(r.status_code, 200, (n, f)); self.assertTrue(r.data.startswith(m), (n, f))

class ExportSecurityTests(X8):
    def test_permissions_isolation_errors_and_audit(self):
        self.scenario(); col, _ = self.mk("colaborador", "c@a.com"); ges, _ = self.mk("gestor", "g@a.com")
        self.assertEqual(self.ex("history", c=col).status_code, 403); self.assertEqual(self.ex("history", c=ges).status_code, 200); self.assertEqual(self.ex("history", c=self.app.test_client()).status_code, 401)
        self.assertEqual((self.ex("history", "doc").status_code, self.ex("nope").status_code, self.ex("history", qs="item_id=x").status_code, self.ex("history", qs="kind=zzz").status_code), (400, 404, 400, 400))
        self.assertEqual(len(self.rows(self.ex("history", c=self.adminB))), 2)       # só cabeçalho + TOTAL 0
        self.assertEqual(self.ex("history", c=self.adminB, qs=f"item_id={self.a}").data.decode("utf-8-sig").count("Item A"), 0)
        q = lambda cid: self.conn.execute("SELECT details, user_id FROM audit_log WHERE action='report.exported' AND company_id=? ORDER BY id", (cid,)).fetchall()
        a, b = q(self.cidA), q(self.cidB)       # só exportações concluídas são auditadas, cada uma na sua empresa
        self.assertEqual((len(a), len(b)), (1, 2)); self.assertIn('"format": "csv"', a[0][0]); self.assertNotEqual(a[0][1], self.uidA)   # a de A foi feita pelo gestor
    def test_row_cap(self):
        self.scenario()
        with mock.patch.object(exports, "MAX_ROWS", 2): r = self.ex("history"); self.assertEqual(r.status_code, 413); self.assertIn("Refine os filtros", r.get_json()["error"])
    def test_sanitizer_unit(self):
        for s in ("=a", "+a", "-a", "@a", "\ta", "\ra"): self.assertEqual(exports.safe(s), "'" + s)
        for s in ("abc", "", "1=1", "a-b"): self.assertEqual(exports.safe(s), s)

class CompanyTests(X8):
    def test_update_validation_audit_isolation(self):
        self.assertEqual(self.api(self.admin, "GET", "/api/company").json["timezone"], "America/Belem")
        r = self.api(self.admin, "PUT", "/api/company", {"name": " Nova Ltda ", "document": "12.345.678/0001-90", "timezone": "UTC"}); self.assertEqual((r.status_code, r.json["name"]), (200, "Nova Ltda"))
        self.assertEqual(dash.bucket(self.conn, self.cidA, "2026-03-10T02:30:00.000Z", False), "2026-03-10")            # novo fuso passa a valer nos cálculos
        for b in ({"name": " "}, {"timezone": "Marte/Olimpo"}, {"document": "x" * 31}, {"name": "x" * 121}): self.assertEqual(self.api(self.admin, "PUT", "/api/company", b).status_code, 400, b)
        self.assertEqual(self.api(self.adminB, "GET", "/api/company").json["name"], "B")
        d = self.conn.execute("SELECT details FROM audit_log WHERE action='company.updated'").fetchone()[0]; self.assertIn('"before"', d); self.assertIn("Nova Ltda", d)
        col, _ = self.mk("colaborador", "c@a.com"); ges, _ = self.mk("gestor", "g@a.com")
        for c in (col, ges): self.assertEqual(self.api(c, "PUT", "/api/company", {"name": "Hack"}).status_code, 403); self.assertEqual(self.api(c, "GET", "/api/company").status_code, 200)
    def test_page_size_setting_bounds(self):
        self.assertEqual(self.api(self.admin, "PUT", "/api/settings", {"default_page_size": 50}).json["default_page_size"], 50)
        for v in (5, 101, "x"): self.assertEqual(self.api(self.admin, "PUT", "/api/settings", {"default_page_size": v}).status_code, 400)

class ProfileTests(X8):
    def test_profile_read_and_rename(self):
        col, _ = self.mk("colaborador", "c@a.com"); self.assertEqual(self.api(col, "GET", "/api/profile").json["role"], "colaborador")
        self.assertEqual(self.api(col, "PATCH", "/api/profile", {"name": "Novo Nome"}).status_code, 200); self.assertEqual(self.api(col, "GET", "/api/me").json["name"], "Novo Nome")
        self.assertEqual(self.api(col, "PATCH", "/api/profile", {"name": " "}).status_code, 400); self.assertEqual(self.app.test_client().get("/api/profile").status_code, 401)
    def test_change_password_keeps_this_session_kills_others(self):
        col, _ = self.mk("colaborador", "c@a.com"); other = self.client("c@a.com", "Outra-senha-456"); new = "Terceira-senha-789"
        self.assertEqual(self.api(col, "POST", "/api/profile/password", {"current_password": "Outra-senha-456", "new_password": new}).status_code, 200)
        self.assertEqual((col.get("/api/me").status_code, other.get("/api/me").status_code), (200, 401))
        self.assertEqual(self.client("c@a.com", new).get("/api/me").status_code, 200)
        c2 = self.app.test_client(); self.assertEqual(c2.post("/login", data={"email": "c@a.com", "password": "Outra-senha-456", "csrf_token": self.tok(c2)}).status_code, 401)
    def test_change_password_rules_and_lockout(self):
        col, _ = self.mk("colaborador", "c@a.com"); cur = "Outra-senha-456"; call = lambda c, n: self.api(col, "POST", "/api/profile/password", {"current_password": c, "new_password": n}).status_code
        self.assertEqual((call("errada-123456", "Nova-senha-123"), call(cur, "curta1"), call(cur, cur), call(cur, "semnumerosnasenha")), (400, 400, 400, 400))
        for _ in range(4): call("errada-123456", "Nova-senha-123")
        self.assertEqual(call(cur, "Nova-senha-123"), 429)

class AuditTests(X8):
    def test_scope_by_role_and_filters(self):
        self.scenario(); ges, _ = self.mk("gestor", "g@a.com"); col, _ = self.mk("colaborador", "c@a.com")
        self.api(self.admin, "PUT", "/api/permissions/gestor", {"permissions": ["audit.view", "reports.view", "items.view"]}); self.ex("history")
        adm = self.api(self.admin, "GET", "/api/audit?size=100").json; acts = {x["action"] for x in adm["items"]}
        self.assertTrue({"login.success", "movement.created", "user.created", "permissions.updated", "report.exported", "item.created"} <= acts)
        g = self.api(ges, "GET", "/api/audit?size=100").json; ga = {x["action"] for x in g["items"]}
        self.assertTrue(ga and all(a.split(".")[0] in ("movement", "adjustment", "stock", "item", "report") for a in ga)); self.assertTrue(all(x["ip"] is None for x in g["items"])); self.assertTrue(all(x["ip"] for x in adm["items"] if x["action"] == "login.success"))
        self.assertEqual(self.api(col, "GET", "/api/audit").status_code, 403); self.assertEqual(self.app.test_client().get("/api/audit").status_code, 401)
        self.assertEqual(self.api(self.admin, "GET", "/api/audit?action=movement.*&size=100").json["total"], 6)
        self.assertEqual(self.api(self.admin, "GET", "/api/audit?action=login.failed").json["total"], 0)
        far = date.today() + timedelta(days=9); self.assertEqual(self.api(self.admin, "GET", f"/api/audit?from={far}").json["total"], 0)
        self.assertEqual(self.api(self.admin, "GET", "/api/audit?size=2&page=2").json["page"], 2)
        for qs in ("user_id=x", "size=x", "from=ontem"): self.assertEqual(self.api(self.admin, "GET", "/api/audit?" + qs).status_code, 400, qs)
    def test_isolation_and_details_parsed(self):
        self.scenario(); b = self.api(self.adminB, "GET", "/api/audit?size=100").json
        self.assertTrue(all(x["user_name"] in ("Bruno", None) for x in b["items"])); self.assertNotIn("movement.created", {x["action"] for x in b["items"]})
        c = [x for x in self.api(self.admin, "GET", "/api/audit?action=movement.created").json["items"]][0]; self.assertIn("kind", c["details"])

class PageTests8(X8):
    def test_pages_and_access(self):
        col, _ = self.mk("colaborador", "c@a.com"); ges, _ = self.mk("gestor", "g@a.com")
        for u in ("/configuracoes", "/auditoria", "/perfil"):
            h = self.admin.get(u).get_data(as_text=True); self.assertIsNone(re.search(r"<script>(?!</script>)", h), u)
        self.assertIn('data-admin="1"', self.admin.get("/configuracoes").get_data(as_text=True))
        self.assertEqual((col.get("/configuracoes").status_code, col.get("/auditoria").status_code, col.get("/perfil").status_code, ges.get("/configuracoes").status_code, ges.get("/auditoria").status_code), (403, 403, 200, 403, 403))
        self.assertIn("Meu perfil", col.get("/perfil").get_data(as_text=True)); self.assertIn('id="xpdf"', self.admin.get("/relatorios").get_data(as_text=True))

if __name__ == "__main__":
    unittest.main()
