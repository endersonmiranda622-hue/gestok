import os, re, shutil, sqlite3, subprocess, sys, tempfile, threading, time, unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock
try:
    from test_stage5 import Base, PW, PW2
except ImportError:
    from tests.test_stage5 import Base, PW, PW2
from app import create_app, MIGRATIONS_DIR, ops, stock
from app import db as dbm, accounts as acc
from app.config import ConfigError, load_config
from app.permissions import PERMS

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = {"auth.login", "auth.forgot", "auth.reset", "healthz", "static"}
LOGIN_ONLY_API = {"/api/me", "/api/profile", "/api/profile/password"}

def _url(app, rule, method):
    vals = {a: (1 if type(rule._converters[a]).__name__ == "IntegerConverter" else "x") for a in rule.arguments}
    return app.url_map.bind("localhost").build(rule.endpoint, vals, method=method)

class SessionTests(Base):
    def test_logout_revokes_server_side_so_stolen_cookie_dies(self):
        c = self.client("ana@a.com", PW)
        with c.session_transaction() as s: sid = s["sid"]
        cookie = c.get_cookie("session").value
        self.assertEqual(c.get("/api/me").status_code, 200)
        c.post("/logout", data={"csrf_token": self.tok(c, "/")})
        thief = self.app.test_client(); thief.set_cookie("session", cookie, domain="localhost")
        self.assertEqual(thief.get("/api/me").status_code, 401)
        self.assertIsNotNone(self.conn.execute("SELECT revoked_at FROM user_sessions WHERE revoked_at IS NOT NULL").fetchone())
    def test_only_hash_stored_and_logout_affects_only_that_session(self):
        a, b = self.client("ana@a.com", PW), self.client("ana@a.com", PW)
        with a.session_transaction() as s: sid = s["sid"]
        raw = b"".join(str(tuple(r)).encode() for r in self.conn.execute("SELECT * FROM user_sessions"))
        self.assertNotIn(sid.encode(), raw); self.assertEqual(self.conn.execute("SELECT length(token_hash) FROM user_sessions").fetchone()[0], 64)
        a.post("/logout", data={"csrf_token": self.tok(a, "/")}); self.assertEqual((a.get("/api/me").status_code, b.get("/api/me").status_code), (401, 200))
    def test_idle_and_absolute_expiry(self):
        c = self.client("ana@a.com", PW); self.assertEqual(c.get("/api/me").status_code, 200)
        self.conn.execute("UPDATE user_sessions SET last_seen_at=?", (acc.now_iso(-121),)); self.assertEqual(c.get("/api/me").status_code, 401)
        d = self.client("ana@a.com", PW); self.conn.execute("UPDATE user_sessions SET expires_at=? WHERE revoked_at IS NULL", (acc.now_iso(-1),)); self.assertEqual(d.get("/api/me").status_code, 401)
    def seen(self, c):   # "última atividade" da sessão DESTE cliente (o setUp já criou outras sessões)
        import hashlib
        with c.session_transaction() as s: h = hashlib.sha256(s["sid"].encode()).hexdigest()
        return self.conn.execute("SELECT last_seen_at FROM user_sessions WHERE token_hash=?", (h,)).fetchone()[0]
    def test_activity_refreshes_idle_timer_but_throttled(self):
        c = self.client("ana@a.com", PW); self.conn.execute("UPDATE user_sessions SET last_seen_at=?", (acc.now_iso(-100),))
        self.assertLess(self.seen(c), acc.now_iso(-90)); self.assertEqual(c.get("/api/me").status_code, 200)
        t = self.seen(c); self.assertGreater(t, acc.now_iso(-2))                       # renovou
        time.sleep(0.05); c.get("/api/me"); self.assertEqual(self.seen(c), t)         # segunda requisição em seguida não grava de novo
    def test_login_rotates_session_and_csrf(self):
        c = self.app.test_client(); before = self.tok(c)
        c.post("/login", data={"email": "ana@a.com", "password": PW, "csrf_token": before}); self.assertNotEqual(self.tok(c, "/"), before)
    def test_cookie_with_invalid_or_forged_sid_rejected(self):
        c = self.client("ana@a.com", PW)
        with c.session_transaction() as s: s["sid"] = "forjado"
        self.assertEqual(c.get("/api/me").status_code, 401)
        d = self.client("ana@a.com", PW)
        with d.session_transaction() as s: s.pop("sid")
        self.assertEqual(d.get("/api/me").status_code, 401)

class ProductionConfigTests(Base):
    ENV = {"GESTOK_ENV": "production", "GESTOK_SECRET_KEY": "s" * 48}
    def prod(self, **extra):
        with mock.patch.dict(os.environ, {**self.ENV, **{k: str(v) for k, v in extra.items()}}):
            return create_app({"DATABASE": self.path})
    def test_https_enforced_cookie_hardened_hsts(self):
        c = self.prod().test_client(); H = "https://localhost"
        r = c.get("/login"); self.assertEqual((r.status_code, r.headers["Location"]), (308, "https://localhost/login"))
        self.assertEqual(c.post("/login", data={"email": "ana@a.com", "password": PW}).status_code, 400)           # credencial nunca aceita em HTTP
        self.assertEqual(c.get("/healthz").status_code, 200)
        tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login", base_url=H).get_data(as_text=True)).group(1)
        r = c.post("/login", data={"email": "ana@a.com", "password": PW, "csrf_token": tok}, base_url=H); ck = r.headers["Set-Cookie"]
        self.assertEqual(r.status_code, 302); self.assertTrue(ck.startswith("__Host-gestok="))
        for part in ("Secure", "HttpOnly", "SameSite=Lax", "Path=/"): self.assertIn(part, ck)
        self.assertNotIn("Domain", ck); self.assertIn("max-age=31536000", r.headers["Strict-Transport-Security"]); self.assertEqual(c.get("/api/me", base_url=H).status_code, 200)
    def test_config_fails_closed(self):
        for env in ({}, {"GESTOK_SECRET_KEY": "curta"}, {"GESTOK_SECRET_KEY": "s" * 48, "GESTOK_TRUSTED_PROXIES": "9"}, {"GESTOK_SECRET_KEY": "s" * 48, "GESTOK_TRUSTED_PROXIES": "x"}):
            with self.assertRaises(ConfigError): load_config({"GESTOK_ENV": "production", **env})
        self.assertTrue(load_config({"GESTOK_ENV": "production", "GESTOK_SECRET_KEY": "s" * 48})["FORCE_HTTPS"])
    def test_forwarded_headers_ignored_unless_proxy_configured(self):
        app = create_app({"DATABASE": self.path}); c = app.test_client()          # desenvolvimento, sem proxy confiável
        for i in range(20):
            c.post("/login", data={"email": f"x{i}@x.com", "password": "errada-123456", "csrf_token": self.tok(c)}, headers={"X-Forwarded-For": f"9.9.9.{i}"})
        r = c.post("/login", data={"email": "ana@a.com", "password": PW, "csrf_token": self.tok(c)}, headers={"X-Forwarded-For": "7.7.7.7"}); self.assertEqual(r.status_code, 429)
    def test_forwarded_headers_used_when_proxy_configured(self):
        app = create_app({"DATABASE": self.path, "TRUSTED_PROXIES": 1}); c = app.test_client()
        post = lambda email, pw, ip: c.post("/login", data={"email": email, "password": pw, "csrf_token": self.tok(c)}, headers={"X-Forwarded-For": ip}).status_code
        for i in range(20): post(f"x{i}@x.com", "errada-123456", "1.1.1.1")
        self.assertEqual((post("ana@a.com", PW, "1.1.1.1"), post("ana@a.com", PW, "2.2.2.2")), (429, 302))
    def test_errors_never_leak_internals_and_keep_security_headers(self):
        app = create_app({"DATABASE": self.path, "TESTING": False}); app.add_url_rule("/boom", "boom", lambda: 1 / 0); c = app.test_client()
        r = c.get("/boom"); body = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 500); self.assertNotIn("ZeroDivision", body); self.assertNotIn("Traceback", body); self.assertIn("default-src", r.headers["Content-Security-Policy"])
        r = c.get("/nada"); self.assertEqual((r.status_code, r.headers["X-Content-Type-Options"], r.headers["X-Frame-Options"]), (404, "nosniff", "DENY"))
        for h in ("Permissions-Policy", "Cross-Origin-Opener-Policy", "Referrer-Policy"): self.assertIn(h, r.headers)

class RouteSweepTests(Base):
    def _rules(self):
        return [(r, m) for r in self.app.url_map.iter_rules() if r.endpoint not in PUBLIC for m in sorted(r.methods - {"HEAD", "OPTIONS"})]
    def test_every_private_route_rejects_anonymous(self):
        c = self.app.test_client(); rules = self._rules(); bad = []
        for r, m in rules:   # token CSRF válido a cada requisição (o /logout limpa a sessão): assim o que barra é a AUTENTICAÇÃO, não o CSRF
            code = c.open(_url(self.app, r, m), method=m, json={}, headers={"X-CSRF-Token": self.tok(c)}).status_code
            if code not in (302, 401): bad.append((r.rule, m, code))
        self.assertGreaterEqual(len(rules), 50); self.assertEqual(bad, [])      # piso de sanidade: a varredura não pode ficar vazia
    def test_every_api_route_enforces_permission_server_side(self):
        self.api(self.admin, "PUT", "/api/permissions/colaborador", {"permissions": []}); col, _ = self.mk("colaborador", "c@a.com")
        h = {"X-CSRF-Token": self.tok(col, "/")}; bad, n = [], 0
        for r, m in self._rules():
            if r.endpoint == "auth.logout": continue
            url = _url(self.app, r, m); code = col.open(url, method=m, json={"kind": "entrada"}, headers=h).status_code
            allowed = url in LOGIN_ONLY_API or url in ("/", "/perfil")
            n += 1
            if (code == 403) == allowed and not (allowed and code in (200, 400)): bad.append((r.rule, m, code))
        self.assertGreaterEqual(n, 50); self.assertEqual(bad, [])
    def test_admin_only_routes_stay_closed_even_to_a_gestor_with_every_permission(self):
        self.api(self.admin, "PUT", "/api/permissions/gestor", {"permissions": list(PERMS)}); ges, _ = self.mk("gestor", "g@a.com")
        self.assertEqual(self.api(ges, "GET", "/api/permissions").status_code, 403)
        for role in ("gestor", "colaborador"): self.assertEqual(self.api(ges, "PUT", f"/api/permissions/{role}", {"permissions": []}).status_code, 403)
        self.assertEqual(self.api(ges, "POST", "/api/users", {"name": "x", "email": "adm@a.com", "role": "admin"}).status_code, 403)
        self.assertEqual(self.api(ges, "PATCH", f"/api/users/{self.uidA}", {"active": False}).status_code, 403)

class TenantAndInjectionSweepTests(Base):
    def test_company_b_never_sees_any_company_a_data_on_any_endpoint(self):
        self.api(self.admin, "PUT", "/api/company", {"name": "Empresa SECRETA A"})
        cat = self.api(self.admin, "POST", "/api/categories", {"name": "CatSecretaA"}).json["id"]; wh = self.api(self.admin, "POST", "/api/warehouses", {"name": "DepSecretoA"}).json["id"]
        i = self.item("TOPSECRETA", name="ItemSecretoA", category_id=cat, tracks_lot=True, tracks_expiry=True, min_qty=99)
        m = self.entrada(i, 5, "7", wh=wh, lot_code="LOTESECRETOA", expiry_date="2099-01-01").json["id"]
        self.mv("ajuste", reason="MotivoSecretoA", lines=[{"item_id": i, "warehouse_id": wh, "qty": 1, "direction": "diminuir", "lot_code": "LOTESECRETOA"}])
        r = self.api(self.admin, "POST", "/api/users", {"name": "UsuarioSecretoA", "email": "secreto@a.com", "role": "gestor"}); col, _ = self.mk("colaborador", "c@a.com")
        self.api(col, "POST", "/api/adjustment-requests", {"kind": "estorno", "movement_id": m, "reason": "PedidoSecretoA"})
        urls = ["/api/items?active=all&size=100", f"/api/items/{i}", "/api/categories", "/api/units", "/api/warehouses", "/api/users", f"/api/users/{self.uidA}", "/api/movements?size=100", f"/api/movements/{m}", f"/api/stock/{i}",
                "/api/stale", "/api/valuation", "/api/alerts", "/api/alerts?type=zero", "/api/alerts?type=replenish", "/api/alerts?type=expiring", "/api/dashboard", "/api/audit?size=100", "/api/company",
                "/api/settings", "/api/adjustment-requests", "/api/profile", "/api/me", "/api/items?q=secret", "/api/users?q=secret", "/materiais", "/usuarios", "/"]
        for n in [x["name"] for x in self.api(self.adminB, "GET", "/api/reports").json["items"]]:
            urls += [f"/api/reports/{n}?size=200", f"/api/reports/{n}/export?format=csv", f"/api/reports/{n}/export?format=xlsx"]
        for u in urls:
            r = self.adminB.get(u); blob = r.data.decode("utf-8", "ignore").lower()
            if u.endswith("xlsx"):
                import io, zipfile; blob = "".join(zipfile.ZipFile(io.BytesIO(r.data)).read(x).decode("utf-8", "ignore").lower() for x in zipfile.ZipFile(io.BytesIO(r.data)).namelist())
            self.assertNotIn("secret", blob, u); self.assertLess(r.status_code, 500, u)
        self.assertEqual(self.adminB.get(f"/api/items/{i}").status_code, 404); self.assertEqual(self.adminB.get(f"/api/stock/{i}").status_code, 404)
    def test_sql_injection_payloads_never_break_or_leak(self):
        payloads = ["' OR '1'='1", "'; DROP TABLE items;--", "\" OR 1=1 --", "%", "_", "\\", "1 UNION SELECT password_hash FROM users", "x' AND (SELECT count(*) FROM users)>0--"]
        i = self.item("S1")
        urls = ["/api/items?q={}", "/api/users?q={}", "/api/audit?action={}", "/api/movements?kind={}", "/api/items?category_id={}", "/api/stale?warehouse_id={}", "/api/reports/history?item_id={}", "/api/reports/history?kind={}", f"/api/stock/{{}}", "/api/alerts?type={}", "/api/reports/{}"]
        from urllib.parse import quote
        for u in urls:
            for p in payloads:
                r = self.admin.get(u.format(quote(p, safe="")))
                self.assertIn(r.status_code, (200, 400, 404), (u, p, r.status_code)); self.assertNotIn("scrypt:", r.get_data(as_text=True))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM items").fetchone()[0], 1)
        self.assertEqual(self.api(self.admin, "GET", "/api/items?q=%27%20OR%20%271%27%3D%271").json["total"], 0)
        evil = "x'); DROP TABLE users;--"; r = self.api(self.admin, "POST", "/api/items", {"code": "SQLI", "name": evil, "item_type": "material", "unit_id": self.unA}); self.assertEqual(r.json["name"], evil)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM users").fetchone()[0], 2)
    def test_stored_values_are_html_escaped_in_server_rendered_pages(self):
        self.api(self.admin, "PUT", "/api/company", {"name": "<script>alert(1)</script>"}); col, _ = self.mk("colaborador", "c@a.com")
        self.api(col, "PATCH", "/api/profile", {"name": "<img src=x onerror=alert(1)>"})
        for u in ("/", "/materiais", "/perfil"):
            for who in (self.admin, col):
                h = who.get(u).get_data(as_text=True); self.assertNotIn("<script>alert(1)", h, u); self.assertNotIn("<img src=x", h, u)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", self.admin.get("/").get_data(as_text=True))       # nome da empresa (menu lateral)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", col.get("/").get_data(as_text=True))                 # nome do usuário ("Olá, ...")
    def test_static_code_hygiene(self):
        for f in list((ROOT / "app/static/js").glob("*.js")):
            t = f.read_text(); [self.assertNotIn(bad, t, f.name) for bad in ("innerHTML", "eval(", "document.write", "outerHTML", "insertAdjacentHTML")]
        for f in (ROOT / "app/templates").glob("*.html"):
            t = f.read_text(); self.assertNotIn("|safe", t, f.name); self.assertNotIn("autoescape false", t, f.name); self.assertIsNone(re.search(r"<script>(?!</script>)", t), f.name)
            self.assertNotRegex(t, r"\son(click|load|error|submit)=", f.name)
            self.assertNotRegex(t, r"\sstyle=", f.name)      # a CSP (default-src 'self') bloqueia style="..." inline: a tela ficaria sem o estilo
        for f in (ROOT / "app").glob("*.py"): self.assertNotRegex(f.read_text(), r"(?i)\b(password|secret)\s*=\s*[\"'][^\"']{6,}[\"']", f.name)

class BackupRestoreTests(Base):
    def seed(self):
        i = self.item("A1"); self.entrada(i, 10, "2"); self.saida(i, 3); self.mv("transferencia", lines=[{"item_id": i, "from_warehouse_id": self.wh1, "to_warehouse_id": self.wh2, "qty": 2}]); return i
    def bdir(self): return os.path.join(self.tmp, "bk")
    def test_backup_verified_private_checksummed_and_free_of_secrets(self):
        self.seed(); t, h = ops.backup(self.path, self.bdir())
        self.assertEqual(oct(os.stat(t).st_mode & 0o777), "0o600"); self.assertTrue(Path(t + ".sha256").read_text().startswith(h))
        raw = Path(t).read_bytes(); self.assertNotIn(PW.encode(), raw); self.assertNotIn(b"k" * 40, raw)
    def test_retention_keeps_newest_and_ignores_other_files(self):
        self.seed(); os.makedirs(self.bdir()); other = os.path.join(self.bdir(), "notas.txt"); Path(other).write_text("x")
        made = [ops.backup(self.path, self.bdir(), keep=2)[0] for _ in range(4)]
        left = sorted(f for f in os.listdir(self.bdir()) if f.endswith(".db")); self.assertEqual(left, sorted(os.path.basename(m) for m in made[-2:]))
        self.assertTrue(os.path.exists(other)); self.assertEqual(len([f for f in os.listdir(self.bdir()) if f.endswith(".sha256")]), 2)
    def test_backup_during_concurrent_writes_is_consistent(self):
        i = self.item("A1"); self.entrada(i, 1000, "1"); c = self.client("ana@a.com", PW); tk = self.tok(c, "/"); stop = []
        def writer():
            for n in range(60):
                if stop: break
                c.post("/api/movements", json={"kind": "saida", "lines": [{"item_id": i, "warehouse_id": self.wh1, "qty": 1}]}, headers={"X-CSRF-Token": tk, "Idempotency-Key": f"bk-w-{n}-xxxxx"})
        t = threading.Thread(target=writer); t.start(); time.sleep(0.2); path, _ = ops.backup(self.path, self.bdir()); t.join(60)
        b = dbm.connect(path); self.assertEqual((dbm.integrity_report(b)["integrity_check"], stock.reconcile(b)), (["ok"], [])); b.close()
    def test_disaster_recovery_full_cycle(self):
        i = self.seed(); path, _ = ops.backup(self.path, self.bdir()); before = self.conn.execute("SELECT count(*) FROM stock_movements").fetchone()[0]; self.conn.close()
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(self.path + ext): os.remove(self.path + ext)                                   # desastre: banco perdido
        info = ops.restore(path, self.path); self.assertEqual((info["movements"], info["reconcile_issues"], info["safety_copy"]), (before, 0, None))
        self.conn = dbm.connect(self.path); app = create_app({"DATABASE": self.path, "TESTING": True, "SECRET_KEY": "k" * 40}); c = app.test_client()
        c.post("/login", data={"email": "ana@a.com", "password": PW, "csrf_token": self.tok(c)})            # senha original continua valendo
        self.assertEqual(c.get(f"/api/stock/{i}").json["warehouses"][0]["qty_milli"] + c.get(f"/api/stock/{i}").json["warehouses"][1]["qty_milli"], 7000); self.assertEqual(stock.reconcile(self.conn), [])
    def test_restore_refusals(self):
        self.seed(); good, _ = ops.backup(self.path, self.bdir())
        with self.assertRaisesRegex(ops.OpsError, "--force"): ops.restore(good, self.path)                       # não sobrescreve sem --force
        tampered = os.path.join(self.bdir(), "gestok-20200101-000000-000000.db"); shutil.copy(good, tampered); shutil.copy(good + ".sha256", tampered + ".sha256")
        with open(tampered, "ab") as f: f.write(b"x")
        with self.assertRaisesRegex(ops.OpsError, "SHA-256"): ops.restore(tampered, self.path + ".novo")
        nosum = os.path.join(self.bdir(), "sem-soma.db"); shutil.copy(good, nosum)
        with self.assertRaisesRegex(ops.OpsError, "sha256"): ops.restore(nosum, self.path + ".novo")
        bad = os.path.join(self.bdir(), "corrompido.db"); Path(bad).write_bytes(b"isto nao e um banco sqlite" * 500)
        import hashlib; Path(bad + ".sha256").write_text(hashlib.sha256(Path(bad).read_bytes()).hexdigest() + "  corrompido.db\n")
        with self.assertRaises(ops.OpsError): ops.restore(bad, self.path + ".novo")
        self.assertFalse(os.path.exists(self.path + ".novo")); self.assertEqual(self.conn.execute("SELECT count(*) FROM stock_movements").fetchone()[0], 3)
    def test_force_restore_reverts_data_and_keeps_safety_copy_and_refuses_in_use(self):
        i = self.seed(); good, _ = ops.backup(self.path, self.bdir()); self.entrada(i, 100, "1"); newer = self.conn.execute("SELECT count(*) FROM stock_movements").fetchone()[0]
        holder = sqlite3.connect(self.path, isolation_level=None); holder.execute("BEGIN IMMEDIATE")
        with self.assertRaisesRegex(ops.OpsError, "em uso"): ops.restore(good, self.path, force=True)
        holder.execute("ROLLBACK"); holder.close(); self.conn.close()
        info = ops.restore(good, self.path, force=True); self.assertEqual(info["movements"], newer - 1)
        safe = sqlite3.connect(info["safety_copy"]); self.assertEqual(safe.execute("SELECT count(*) FROM stock_movements").fetchone()[0], newer); safe.close(); self.conn = dbm.connect(self.path)
    def test_restore_of_older_schema_then_migrate_upgrades(self):
        old = os.path.join(self.tmp, "old_migs"); os.makedirs(old)
        for f in sorted(MIGRATIONS_DIR.glob("*.sql"))[:3]: shutil.copy(f, old)
        p = os.path.join(self.tmp, "legacy.db"); c = dbm.connect(p); dbm.migrate(c, old); acc.create_company_with_admin(c, "Legada", "Leo", "leo@l.com", PW); c.close()
        b, _ = ops.backup(p, self.bdir()); target = os.path.join(self.tmp, "novo.db"); info = ops.restore(b, target)
        self.assertEqual(info["schema"], "003_movements_support"); c = dbm.connect(target); self.assertEqual(dbm.migrate(c, MIGRATIONS_DIR), ["004_user_sessions"])
        self.assertEqual(c.execute("SELECT count(*) FROM users").fetchone()[0], 1); c.close()

class DatabaseFileTests(Base):
    def test_new_database_and_wal_files_are_private_and_doctor_agrees(self):
        p = os.path.join(self.tmp, "novo.db"); c = dbm.connect(p); dbm.migrate(c, MIGRATIONS_DIR); c.execute("CREATE TABLE t(x)"); c.execute("INSERT INTO t VALUES (1)")
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(p + ext): self.assertEqual(os.stat(p + ext).st_mode & 0o077, 0, p + ext)    # nenhum acesso para grupo/outros
        res = ops.doctor({**load_config({}), "DATABASE": p}); c.close()
        self.assertIn(("OK", "Permissões do arquivo do banco: 0o600"), res)
    def test_existing_database_permissions_are_not_changed_by_connecting(self):
        os.chmod(self.path, 0o640); dbm.connect(self.path).close(); self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o640)   # não mexe no que o operador já definiu

class CliOpsTests(Base):
    def run_cli(self, *args, **env):
        e = {k: v for k, v in os.environ.items() if not k.startswith("GESTOK_")}; e.update(GESTOK_DATABASE_PATH=self.path, **{k: str(v) for k, v in env.items()})
        return subprocess.run([sys.executable, "manage.py", *args], cwd=ROOT, env=e, capture_output=True, text=True)
    def test_backup_restore_doctor_commands(self):
        i = self.item("A1"); self.entrada(i, 5); d = os.path.join(self.tmp, "bk"); r = self.run_cli("backup", "--dest", d, "--keep", "3")
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("SHA-256", r.stdout); f = [x for x in os.listdir(d) if x.endswith(".db")][0]
        self.assertNotEqual(self.run_cli("restore", "--file", os.path.join(d, f)).returncode, 0)                  # sem --force recusa
        ok = self.run_cli("doctor"); self.assertEqual(ok.returncode, 0, ok.stdout); self.assertIn("[OK] Saldos conferem", ok.stdout)
        prod = dict(GESTOK_ENV="production", GESTOK_SECRET_KEY="s" * 48)
        self.assertEqual(self.run_cli("doctor", **prod, GESTOK_FORCE_HTTPS=0).returncode, 1)
        r = self.run_cli("doctor", **prod); self.assertEqual(r.returncode, 0, r.stdout); self.assertIn("[WARN] GESTOK_TRUSTED_PROXIES=0", r.stdout)
        self.conn.execute("UPDATE stock_balances SET qty_milli=qty_milli+1"); r = self.run_cli("doctor"); self.assertEqual(r.returncode, 1); self.assertIn("[FAIL]", r.stdout)
    def test_doctor_without_database_fails_without_creating_one(self):
        os.remove(self.path); r = self.run_cli("doctor"); self.assertEqual(r.returncode, 1); self.assertFalse(os.path.exists(self.path))

class _Labels(HTMLParser):
    def __init__(self): super().__init__(); self.labels, self.fields, self.depth, self.problems, self.meta = set(), [], 0, [], {}
    def handle_starttag(self, tag, a):
        a = dict(a)
        if tag == "meta" and a.get("name") == "viewport": self.meta["viewport"] = a.get("content")
        if tag == "html": self.meta["lang"] = a.get("lang")
        if tag == "label": self.depth += 1; (self.labels.add(a["for"]) if "for" in a else None)
        if tag == "table" and "tbl" not in a.get("class", ""): self.problems.append("table sem classe tbl (rolagem própria)")
        if tag in ("input", "select", "textarea") and a.get("type") not in ("hidden", "submit"):
            self.fields.append((a.get("id"), "aria-label" in a, self.depth > 0))
    def handle_endtag(self, tag):
        if tag == "label": self.depth -= 1

class ResponsiveAndAccessibilityTests(Base):
    PAGES = ["/", "/materiais", "/cadastros", "/movimentacoes", "/relatorios", "/estoque-parado", "/usuarios", "/configuracoes", "/auditoria", "/perfil"]
    def test_every_page_is_responsive_ready_and_every_field_labelled(self):
        anon = self.app.test_client(); pages = [(u, self.admin.get(u).get_data(as_text=True)) for u in self.PAGES] + [(u, anon.get(u).get_data(as_text=True)) for u in ("/login", "/forgot")]
        for u, h in pages:
            p = _Labels(); p.feed(h)
            self.assertEqual(p.meta.get("viewport"), "width=device-width,initial-scale=1", u); self.assertEqual(p.meta.get("lang"), "pt-BR", u); self.assertEqual(p.problems, [], u)
            for fid, aria, wrapped in p.fields: self.assertTrue(aria or wrapped or (fid and fid in p.labels), (u, fid, "campo sem rótulo acessível"))
    def test_css_has_mobile_rules_and_scrollable_tables(self):
        css = (ROOT / "app/static/css/app.css").read_text()
        self.assertIn("@media(max-width:700px)", css); self.assertRegex(css, r"\.tbl\{[^}]*overflow-x:auto"); self.assertIn("flex-direction:column", css); self.assertIn("flex-wrap:wrap", css)
        self.assertEqual(self.app.test_client().get("/static/css/app.css").status_code, 200)

if __name__ == "__main__":
    unittest.main()
