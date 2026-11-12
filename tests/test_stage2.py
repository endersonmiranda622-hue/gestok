import os, re, shutil, tempfile, unittest
from app import create_app, MIGRATIONS_DIR
from app import db as dbm, accounts as acc

PW = "Senha-forte-123"

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(); self.path = os.path.join(self.tmp, "t.db")
        self.conn = dbm.connect(self.path); dbm.migrate(self.conn, MIGRATIONS_DIR)
        self.app = create_app({"DATABASE": self.path, "TESTING": True, "SECRET_KEY": "k" * 40})
        self.cid, self.uid = acc.create_company_with_admin(self.conn, "Empresa A", "Ana", "ana@a.com", PW)
        self.c = self.app.test_client()
    def tearDown(self):
        self.conn.close(); shutil.rmtree(self.tmp)
    def tok(self, c=None, url="/login"):
        r = (c or self.c).get(url)
        return re.search(r'name="csrf_token" value="([^"]+)"', r.get_data(as_text=True)).group(1)
    def login(self, email="ana@a.com", pw=PW, c=None):
        c = c or self.c
        return c.post("/login", data={"email": email, "password": pw, "csrf_token": self.tok(c)})

class AccountTests(Base):
    def test_admin_created_with_hash_permissions_audit(self):
        u = self.conn.execute("SELECT * FROM users WHERE id=?", (self.uid,)).fetchone()
        self.assertTrue(u["password_hash"].startswith("scrypt:")); self.assertNotIn(PW, u["password_hash"])
        self.assertEqual(u["role"], "admin")
        n = self.conn.execute("SELECT count(*) FROM role_permissions WHERE company_id=?", (self.cid,)).fetchone()[0]
        self.assertGreater(n, 20)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM audit_log WHERE action='company.created'").fetchone()[0], 1)
    def test_no_default_users_after_migration(self):
        c2 = dbm.connect(os.path.join(self.tmp, "n.db")); dbm.migrate(c2, MIGRATIONS_DIR)
        self.assertEqual(c2.execute("SELECT count(*) FROM users").fetchone()[0], 0); c2.close()
    def test_weak_password_and_bad_email_rejected(self):
        for pw in ("curta1", "somenteletrasaqui", "1234567890123"):
            with self.assertRaises(acc.ValidationError): acc.create_company_with_admin(self.conn, "X", "N", "x@x.com", pw)
        with self.assertRaises(acc.ValidationError): acc.create_company_with_admin(self.conn, "X", "N", "invalido", PW)
    def test_duplicate_email_rejected_and_no_partial_company(self):
        before = self.conn.execute("SELECT count(*) FROM companies").fetchone()[0]
        with self.assertRaises(acc.ValidationError): acc.create_company_with_admin(self.conn, "B", "N", "ANA@a.com", PW)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM companies").fetchone()[0], before)

class LoginTests(Base):
    def test_success_and_session_cookie_flags(self):
        r = self.login(); self.assertEqual(r.status_code, 302)
        self.assertEqual(self.c.get("/api/me").json["role"], "admin")
        self.assertIn("HttpOnly", r.headers["Set-Cookie"]); self.assertIn("SameSite=Lax", r.headers["Set-Cookie"])
    def test_wrong_password_and_unknown_email_look_identical(self):
        a = self.login(pw="errada-123456"); b = self.login(email="nao@existe.com")
        self.assertEqual((a.status_code, b.status_code), (401, 401))
        self.assertEqual(a.get_data(as_text=True).count("E-mail ou senha inválidos."), 1)
        self.assertEqual(a.get_data(as_text=True), b.get_data(as_text=True))
    def test_private_routes_require_auth(self):
        self.assertEqual(self.c.get("/").status_code, 302)
        self.assertEqual(self.c.get("/api/me").status_code, 401)
    def test_csrf_required_on_post(self):
        self.c.get("/login")
        self.assertEqual(self.c.post("/login", data={"email": "ana@a.com", "password": PW}).status_code, 400)
        self.assertEqual(self.c.post("/logout").status_code, 400)
    def test_logout_ends_session(self):
        self.login(); t = self.tok(url="/")
        self.assertEqual(self.c.post("/logout", data={"csrf_token": t}).status_code, 302)
        self.assertEqual(self.c.get("/api/me").status_code, 401)
        self.assertEqual(self.c.get("/logout").status_code, 405)
    def test_lockout_after_failures_blocks_even_correct_password(self):
        for _ in range(5): self.assertEqual(self.login(pw="errada-123456").status_code, 401)
        self.assertEqual(self.login().status_code, 429)
        self.assertEqual(self.c.get("/api/me").status_code, 401)
    def test_ip_lockout_across_emails(self):
        for i in range(20): self.login(email=f"x{i}@x.com", pw="errada-123456")
        self.assertEqual(self.login().status_code, 429)
    def test_inactive_user_cannot_login_and_existing_session_dies(self):
        self.login(); self.assertEqual(self.c.get("/api/me").status_code, 200)
        self.conn.execute("UPDATE users SET active=0 WHERE id=?", (self.uid,))
        self.assertEqual(self.c.get("/api/me").status_code, 401)
        self.assertEqual(self.login(c=self.app.test_client()).status_code, 401)
    def test_failed_and_successful_logins_are_audited(self):
        self.login(pw="errada-123456"); self.login()
        acts = [r[0] for r in self.conn.execute("SELECT action FROM audit_log WHERE user_id=?", (self.uid,))]
        self.assertIn("login.failed", acts); self.assertIn("login.success", acts)

class ResetTests(Base):
    def test_reset_flow_single_use_and_kills_old_sessions(self):
        other = self.app.test_client(); self.login(c=other)
        tok = acc.issue_reset_token(self.conn, "ana@a.com")
        self.assertEqual(self.c.get(f"/reset/{tok}").status_code, 200)
        new = "Nova-senha-456"
        r = self.c.post(f"/reset/{tok}", data={"password": new, "csrf_token": self.tok(url=f"/reset/{tok}")})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get(f"/reset/{tok}").status_code, 400)           # uso único
        self.assertEqual(other.get("/api/me").status_code, 401)                  # sessão antiga invalidada
        self.assertEqual(self.login(pw=PW).status_code, 401)                     # senha antiga morreu
        self.assertEqual(self.login(pw=new, c=self.app.test_client()).status_code, 302)
    def test_token_stored_hashed_only(self):
        tok = acc.issue_reset_token(self.conn, "ana@a.com")
        self.assertIsNone(self.conn.execute("SELECT 1 FROM password_reset_tokens WHERE token_hash=?", (tok,)).fetchone())
    def test_expired_token_rejected(self):
        tok = acc.issue_reset_token(self.conn, "ana@a.com")
        self.conn.execute("UPDATE password_reset_tokens SET expires_at='2000-01-01T00:00:00.000Z'")
        self.assertEqual(self.c.get(f"/reset/{tok}").status_code, 400)
        with self.assertRaises(acc.ValidationError): acc.reset_password(self.conn, tok, "Nova-senha-456")
    def test_weak_new_password_keeps_token_usable(self):
        tok = acc.issue_reset_token(self.conn, "ana@a.com")
        r = self.c.post(f"/reset/{tok}", data={"password": "fraca", "csrf_token": self.tok(url=f"/reset/{tok}")})
        self.assertEqual(r.status_code, 400); self.assertTrue(acc.reset_token_valid(self.conn, tok))
    def test_forgot_response_identical_and_no_token_for_unknown(self):
        t = self.tok(url="/forgot")
        a = self.c.post("/forgot", data={"email": "ana@a.com", "csrf_token": t}).get_data(as_text=True)
        b = self.c.post("/forgot", data={"email": "ninguem@x.com", "csrf_token": t}).get_data(as_text=True)
        self.assertEqual(a, b)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM password_reset_tokens").fetchone()[0], 1)
    def test_reset_requests_rate_limited(self):
        got = [acc.issue_reset_token(self.conn, "ana@a.com") for _ in range(5)]
        self.assertEqual(sum(1 for g in got if g), 3)
    def test_garbage_token_rejected(self):
        self.assertEqual(self.c.get("/reset/lixo").status_code, 400)

class CliTests(Base):
    def test_cli_create_admin_and_reset_link(self):
        import subprocess, sys
        env = dict(os.environ, GESTOK_DATABASE_PATH=self.path, GESTOK_ADMIN_PASSWORD="Outra-senha-789")
        root = os.path.dirname(os.path.dirname(__file__))
        r = subprocess.run([sys.executable, "manage.py", "create-admin", "--company", "Empresa B", "--name", "Bia", "--email", "bia@b.com"],
                           cwd=root, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([sys.executable, "manage.py", "reset-link", "--email", "bia@b.com"], cwd=root, env=env, capture_output=True, text=True)
        self.assertIn("/reset/", r.stdout)
        r = subprocess.run([sys.executable, "manage.py", "create-admin", "--company", "C", "--name", "C", "--email", "bia@b.com"],
                           cwd=root, env=env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0); self.assertIn("já está cadastrado", r.stderr)

if __name__ == "__main__":
    unittest.main()
