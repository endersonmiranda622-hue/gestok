import os, re, shutil, tempfile, unittest
from app import create_app, MIGRATIONS_DIR
from app import db as dbm, accounts as acc

PW, PW2 = "Senha-forte-123", "Outra-senha-456"

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.path = os.path.join(self.tmp, "t.db")
        self.conn = dbm.connect(self.path); dbm.migrate(self.conn, MIGRATIONS_DIR)
        self.app = create_app({"DATABASE": self.path, "TESTING": True, "SECRET_KEY": "k" * 40})
        self.cidA, self.uidA = acc.create_company_with_admin(self.conn, "A", "Ana", "ana@a.com", PW)
        self.cidB, self.uidB = acc.create_company_with_admin(self.conn, "B", "Bruno", "bruno@b.com", PW)
        self.admin, self.adminB = self.client("ana@a.com", PW), self.client("bruno@b.com", PW)
    def tearDown(self):
        self.conn.close(); shutil.rmtree(self.tmp)
    def tok(self, c, url="/login"):
        return re.search(r'name="csrf_token" value="([^"]+)"', c.get(url).get_data(as_text=True)).group(1)
    def client(self, email, pw):
        c = self.app.test_client()
        r = c.post("/login", data={"email": email, "password": pw, "csrf_token": self.tok(c)})
        assert r.status_code == 302, r.status_code
        return c
    def api(self, c, method, url, body=None):
        return c.open(url, method=method, json=body, headers={"X-CSRF-Token": self.tok(c, "/")})
    def mk(self, admin_client, role, email):
        r = self.api(admin_client, "POST", "/api/users", {"name": "N " + role, "email": email, "role": role})
        assert r.status_code == 201, r.json
        path, c = r.json["reset_path"], self.app.test_client()
        c.post(path, data={"password": PW2, "csrf_token": self.tok(c, path)})
        return self.client(email, PW2), r.json["user"]["id"]

class PermissionEnforcementTests(Base):
    def test_unauthenticated_api_blocked(self):
        c = self.app.test_client()
        for m, u in (("GET", "/api/users"), ("POST", "/api/users"), ("GET", "/api/permissions"), ("PATCH", "/api/users/1")):
            self.assertIn(c.open(u, method=m, json={}).status_code, (400, 401))  # 400 = CSRF antes da auth; ambos bloqueiam
        self.assertEqual(c.get("/api/users").status_code, 401)
        self.assertEqual(c.get("/usuarios").status_code, 302)
    def test_colaborador_and_gestor_cannot_manage_users(self):
        col, _ = self.mk(self.admin, "colaborador", "col@a.com"); ges, _ = self.mk(self.admin, "gestor", "ges@a.com")
        for c in (col, ges):
            self.assertEqual(self.api(c, "GET", "/api/users").status_code, 403)
            self.assertEqual(self.api(c, "POST", "/api/users", {"name": "x", "email": "z@z.com", "role": "colaborador"}).status_code, 403)
            self.assertEqual(self.api(c, "PATCH", f"/api/users/{self.uidA}", {"active": False}).status_code, 403)
            self.assertEqual(self.api(c, "GET", "/api/permissions").status_code, 403)
            self.assertEqual(c.get("/usuarios").status_code, 403)
        denied = self.conn.execute("SELECT count(*) FROM audit_log WHERE action='access.denied'").fetchone()[0]
        self.assertGreaterEqual(denied, 8)
    def test_permission_changes_apply_immediately_and_are_audited(self):
        col, _ = self.mk(self.admin, "colaborador", "col@a.com")
        self.assertIn("movements.create", col.get("/api/me").json["permissions"])
        r = self.api(self.admin, "PUT", "/api/permissions/colaborador", {"permissions": ["items.view"]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(col.get("/api/me").json["permissions"], ["items.view"])
        a = self.conn.execute("SELECT details FROM audit_log WHERE action='permissions.updated'").fetchone()[0]
        self.assertIn("movements.create", a)
    def test_admin_permissions_fixed_and_invalid_input_rejected(self):
        self.assertEqual(self.api(self.admin, "PUT", "/api/permissions/admin", {"permissions": []}).status_code, 400)
        self.assertEqual(self.api(self.admin, "PUT", "/api/permissions/gestor", {"permissions": ["voar"]}).status_code, 400)
        self.assertEqual(self.api(self.admin, "PUT", "/api/permissions/gestor", {"permissions": "x"}).status_code, 400)
    def test_permission_changes_isolated_per_company(self):
        self.api(self.admin, "PUT", "/api/permissions/gestor", {"permissions": []})
        ges, _ = self.mk(self.adminB, "gestor", "ges@b.com")
        self.assertIn("reports.view", ges.get("/api/me").json["permissions"])

class DelegatedManagerTests(Base):
    def setUp(self):
        super().setUp()
        self.api(self.admin, "PUT", "/api/permissions/gestor", {"permissions": ["users.manage", "items.view"]})
        self.ges, self.gid = self.mk(self.admin, "gestor", "ges@a.com")
    def test_delegated_gestor_manages_staff_but_never_admins_or_permissions(self):
        self.assertEqual(self.api(self.ges, "POST", "/api/users", {"name": "C", "email": "c@a.com", "role": "colaborador"}).status_code, 201)
        self.assertEqual(self.api(self.ges, "POST", "/api/users", {"name": "X", "email": "x@a.com", "role": "admin"}).status_code, 403)
        self.assertEqual(self.api(self.ges, "PATCH", f"/api/users/{self.uidA}", {"active": False}).status_code, 403)
        self.assertEqual(self.api(self.ges, "POST", f"/api/users/{self.uidA}/reset-link").status_code, 403)
        _, cid = self.mk(self.admin, "colaborador", "col@a.com")
        self.assertEqual(self.api(self.ges, "PATCH", f"/api/users/{cid}", {"role": "admin"}).status_code, 403)
        self.assertEqual(self.api(self.ges, "PUT", "/api/permissions/gestor", {"permissions": list(__import__("app.permissions", fromlist=["x"]).PERMS)}).status_code, 403)

class UserRulesTests(Base):
    def test_invite_flow_user_has_no_usable_password_until_link_used(self):
        r = self.api(self.admin, "POST", "/api/users", {"name": "Novo", "email": "novo@a.com", "role": "colaborador"})
        self.assertEqual(r.status_code, 201); self.assertNotIn("password", str(r.json).lower().replace("reset_path", ""))
        c = self.app.test_client()
        self.assertEqual(c.post("/login", data={"email": "novo@a.com", "password": "", "csrf_token": self.tok(c)}).status_code, 401)
        self.assertTrue(r.json["reset_path"].startswith("/reset/"))
    def test_validation_and_duplicates(self):
        ok = {"name": "N", "email": "n@a.com", "role": "colaborador"}
        self.assertEqual(self.api(self.admin, "POST", "/api/users", {**ok, "role": "root"}).status_code, 400)
        self.assertEqual(self.api(self.admin, "POST", "/api/users", {**ok, "email": "ruim"}).status_code, 400)
        self.assertEqual(self.api(self.admin, "POST", "/api/users", {**ok, "name": " "}).status_code, 400)
        self.assertEqual(self.api(self.admin, "POST", "/api/users", ok).status_code, 201)
        self.assertEqual(self.api(self.admin, "POST", "/api/users", ok).status_code, 409)
        self.assertEqual(self.api(self.admin, "POST", "/api/users", {**ok, "email": "BRUNO@b.com"}).status_code, 409)  # e-mail de outra empresa
    def test_cannot_deactivate_or_demote_last_admin_nor_self(self):
        self.assertEqual(self.api(self.admin, "PATCH", f"/api/users/{self.uidA}", {"active": False}).status_code, 400)
        self.assertEqual(self.api(self.admin, "PATCH", f"/api/users/{self.uidA}", {"role": "gestor"}).status_code, 400)
        adm2, id2 = self.mk(self.admin, "admin", "ana2@a.com")
        self.assertEqual(self.api(adm2, "PATCH", f"/api/users/{self.uidA}", {"active": False}).status_code, 200)
        self.assertEqual(self.api(adm2, "PATCH", f"/api/users/{id2}", {"active": False}).status_code, 400)  # agora é o último
    def test_deactivation_kills_session_and_is_audited(self):
        col, cid = self.mk(self.admin, "colaborador", "col@a.com")
        self.assertEqual(col.get("/api/me").status_code, 200)
        self.api(self.admin, "PATCH", f"/api/users/{cid}", {"active": False})
        self.assertEqual(col.get("/api/me").status_code, 401)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM audit_log WHERE action='user.updated'").fetchone()[0], 1)
    def test_role_change_applies_new_permissions(self):
        col, cid = self.mk(self.admin, "colaborador", "col@a.com")
        self.api(self.admin, "PATCH", f"/api/users/{cid}", {"role": "gestor"})
        c2 = self.client("col@a.com", PW2)
        self.assertEqual(c2.get("/api/me").json["role"], "gestor")
        self.assertEqual(col.get("/api/me").status_code, 401)  # sessão antiga encerrada por mudança de função
    def test_list_search_and_pagination(self):
        for i in range(5): self.mk(self.admin, "colaborador", f"u{i}@a.com")
        j = self.api(self.admin, "GET", "/api/users?size=2&page=2").json
        self.assertEqual((j["total"], len(j["items"]), j["page"]), (6, 2, 2))
        self.assertEqual(self.api(self.admin, "GET", "/api/users?q=u3@").json["total"], 1)
        self.assertEqual(self.api(self.admin, "GET", "/api/users?size=abc").status_code, 400)

class CompanyIsolationTests(Base):
    def test_cannot_read_modify_or_reset_other_company_user(self):
        for m, u, b in (("GET", f"/api/users/{self.uidB}", None), ("PATCH", f"/api/users/{self.uidB}", {"active": False}),
                        ("POST", f"/api/users/{self.uidB}/reset-link", None)):
            self.assertEqual(self.api(self.admin, m, u, b).status_code, 404, (m, u))
        self.assertEqual(self.conn.execute("SELECT active FROM users WHERE id=?", (self.uidB,)).fetchone()[0], 1)
    def test_lists_only_own_company(self):
        emails = {u["email"] for u in self.api(self.admin, "GET", "/api/users").json["items"]}
        self.assertEqual(emails, {"ana@a.com"})
    def test_company_id_in_body_is_ignored(self):
        r = self.api(self.admin, "POST", "/api/users", {"name": "N", "email": "n@a.com", "role": "colaborador", "company_id": self.cidB})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.conn.execute("SELECT company_id FROM users WHERE email='n@a.com'").fetchone()[0], self.cidA)
    def test_audit_entries_carry_the_actors_company(self):
        self.mk(self.admin, "colaborador", "col@a.com")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM audit_log WHERE action='user.created' AND company_id=?", (self.cidB,)).fetchone()[0], 0)

class PageTests(Base):
    def test_users_page_renders_for_admin_without_inline_script(self):
        h = self.admin.get("/usuarios").get_data(as_text=True)
        self.assertIn("Convidar usuário", h); self.assertNotIn("<script>", h)

if __name__ == "__main__":
    unittest.main()
