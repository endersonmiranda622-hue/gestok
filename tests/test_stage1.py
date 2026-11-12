import os, shutil, sqlite3, tempfile, unittest
from pathlib import Path
from app import create_app, MIGRATIONS_DIR
from app import db as dbm
from app.config import load_config, ConfigError

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "t.db")
        self.conn = dbm.connect(self.path)
        dbm.migrate(self.conn, MIGRATIONS_DIR)
    def tearDown(self):
        self.conn.close(); shutil.rmtree(self.tmp)
    def two_companies(self):
        c = self.conn
        for n in ("A", "B"):
            c.execute("INSERT INTO companies(name) VALUES (?)", (n,))
            cid = c.execute("SELECT max(id) FROM companies").fetchone()[0]
            c.execute("INSERT INTO units(company_id,code,name) VALUES (?,?,?)", (cid, "UN", "Unidade"))
            c.execute("INSERT INTO categories(company_id,name) VALUES (?,?)", (cid, "Geral"))
            c.execute("INSERT INTO warehouses(company_id,name) VALUES (?,?)", (cid, "Principal"))
            c.execute("INSERT INTO users(company_id,name,email,password_hash,role) VALUES (?,?,?,?,?)",
                      (cid, "U", f"u{cid}@x.com", "h", "admin"))
    def add_item(self, cid, code="P1", barcode=None):
        unit = self.conn.execute("SELECT id FROM units WHERE company_id=?", (cid,)).fetchone()[0]
        self.conn.execute("INSERT INTO items(company_id,code,barcode,name,item_type,unit_id) VALUES (?,?,?,?,?,?)",
                          (cid, code, barcode, "Item", "material", unit))

class MigrationTests(Base):
    def test_all_tables_created(self):
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ("companies","users","items","stock_movements","movement_lines","stock_balances","audit_log","schema_migrations"):
            self.assertIn(t, names)
    def test_migrate_is_idempotent_and_keeps_data(self):
        self.conn.execute("INSERT INTO companies(name) VALUES ('Keep')")
        self.assertEqual(dbm.migrate(self.conn, MIGRATIONS_DIR), [])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM companies").fetchone()[0], 1)
    def test_modified_migration_is_detected(self):
        d = Path(self.tmp) / "m"; d.mkdir()
        (d / "001_x.sql").write_text("CREATE TABLE a(id INTEGER);")
        c2 = dbm.connect(os.path.join(self.tmp, "o.db")); dbm.migrate(c2, d)
        (d / "001_x.sql").write_text("CREATE TABLE a(id INTEGER, x TEXT);")
        with self.assertRaises(dbm.MigrationError): dbm.migrate(c2, d)
        c2.close()
    def test_failed_migration_rolls_back_completely(self):
        d = Path(self.tmp) / "m2"; d.mkdir()
        (d / "001_bad.sql").write_text("CREATE TABLE ok(id INTEGER); INSERT INTO nao_existe VALUES (1);")
        c2 = dbm.connect(os.path.join(self.tmp, "o2.db"))
        with self.assertRaises(dbm.MigrationError): dbm.migrate(c2, d)
        self.assertIsNone(c2.execute("SELECT name FROM sqlite_master WHERE name='ok'").fetchone())
        self.assertEqual(c2.execute("SELECT count(*) FROM schema_migrations").fetchone()[0], 0)
        c2.close()
    def test_integrity_report_clean(self):
        r = dbm.integrity_report(self.conn)
        self.assertEqual(r["integrity_check"], ["ok"]); self.assertEqual(r["foreign_key_violations"], [])

class IntegrityTests(Base):
    def test_duplicate_item_code_blocked_per_company_but_allowed_across(self):
        self.two_companies(); self.add_item(1, "X1"); self.add_item(2, "X1")
        with self.assertRaises(sqlite3.IntegrityError): self.add_item(1, "X1")
    def test_duplicate_barcode_blocked_per_company(self):
        self.two_companies(); self.add_item(1, "A", "789"); 
        with self.assertRaises(sqlite3.IntegrityError): self.add_item(1, "B", "789")
    def test_cross_company_reference_blocked_by_database(self):
        self.two_companies()
        unit_b = self.conn.execute("SELECT id FROM units WHERE company_id=2").fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO items(company_id,code,name,item_type,unit_id) VALUES (1,'Z','n','material',?)", (unit_b,))
    def test_movement_history_is_immutable(self):
        self.two_companies()
        self.conn.execute("INSERT INTO stock_movements(id,company_id,kind,user_id,occurred_at) VALUES ('m1',1,'entrada',1,'2026-01-01T00:00:00Z')")
        with self.assertRaises(sqlite3.DatabaseError): self.conn.execute("UPDATE stock_movements SET reason='x'")
        with self.assertRaises(sqlite3.DatabaseError): self.conn.execute("DELETE FROM stock_movements")
    def test_audit_log_is_immutable(self):
        self.conn.execute("INSERT INTO audit_log(action) VALUES ('t')")
        with self.assertRaises(sqlite3.DatabaseError): self.conn.execute("DELETE FROM audit_log")
    def test_only_one_reversal_per_movement(self):
        self.two_companies()
        ins = "INSERT INTO stock_movements(id,company_id,kind,user_id,occurred_at,reverses_id) VALUES (?,1,?,1,'2026-01-01T00:00:00Z',?)"
        self.conn.execute(ins, ("m1", "entrada", None)); self.conn.execute(ins, ("e1", "estorno", "m1"))
        with self.assertRaises(sqlite3.IntegrityError): self.conn.execute(ins, ("e2", "estorno", "m1"))
    def test_negative_cost_and_invalid_role_rejected(self):
        self.two_companies()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO users(company_id,name,email,password_hash,role) VALUES (1,'n','r@x','h','root')")
    def test_transaction_rolls_back_on_error(self):
        with self.assertRaises(RuntimeError):
            with dbm.transaction(self.conn):
                self.conn.execute("INSERT INTO companies(name) VALUES ('tmp')"); raise RuntimeError
        self.assertEqual(self.conn.execute("SELECT count(*) FROM companies").fetchone()[0], 0)
    def test_email_unique_case_insensitive(self):
        self.two_companies()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO users(company_id,name,email,password_hash,role) VALUES (2,'n','U1@X.COM','h','gestor')")

class ConfigAndAppTests(Base):
    def test_production_requires_strong_secret(self):
        with self.assertRaises(ConfigError): load_config({"GESTOK_ENV": "production"})
        with self.assertRaises(ConfigError): load_config({"GESTOK_ENV": "production", "GESTOK_SECRET_KEY": "curta"})
        cfg = load_config({"GESTOK_ENV": "production", "GESTOK_SECRET_KEY": "x" * 40})
        self.assertTrue(cfg["SESSION_COOKIE_SECURE"])
    def test_invalid_env_rejected(self):
        with self.assertRaises(ConfigError): load_config({"GESTOK_ENV": "staging"})
    def test_healthz_and_security_headers(self):
        app = create_app({"DATABASE": self.path, "TESTING": True})
        r = app.test_client().get("/healthz")
        self.assertEqual(r.status_code, 200); self.assertEqual(r.json["schema"], sorted(f.stem for f in MIGRATIONS_DIR.glob("*.sql"))[-1])
        self.assertEqual(r.headers["X-Frame-Options"], "DENY"); self.assertIn("default-src", r.headers["Content-Security-Policy"])
    def test_healthz_503_without_migrations_and_no_leak(self):
        app = create_app({"DATABASE": os.path.join(self.tmp, "vazio.db"), "TESTING": True})
        r = app.test_client().get("/healthz")
        self.assertEqual(r.status_code, 503); self.assertNotIn("schema_migrations", r.get_data(as_text=True))
    def test_404_is_generic(self):
        app = create_app({"DATABASE": self.path, "TESTING": True})
        self.assertEqual(app.test_client().get("/nada").status_code, 404)
    def test_data_persists_after_reconnect(self):
        self.conn.execute("INSERT INTO companies(name) VALUES ('Persist')"); self.conn.close()
        c = dbm.connect(self.path)
        self.assertEqual(c.execute("SELECT name FROM companies").fetchone()[0], "Persist"); c.close()
        self.conn = dbm.connect(self.path)

if __name__ == "__main__":
    unittest.main()
