code = '''"""Acesso ao SQLite: conexão segura, transação e migrations versionadas."""
import hashlib, os, sqlite3, tempfile
from contextlib import contextmanager
from pathlib import Path

class MigrationError(RuntimeError):
    pass

def connect(path):
    # FIX VERCEL: na Vercel só pode escrever em /tmp
    if path != ":memory:" and "/tmp" not in str(path):
        if os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"):
            filename = Path(path).name if path else "gestok.db"
            path = os.path.join(tempfile.gettempdir(), filename)
            os.makedirs(os.path.dirname(path), exist_ok=True)
    
    is_new = path != ":memory:" and not os.path.exists(path)
    conn = sqlite3.connect(path, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=NORMAL")
    if path != ":memory:":
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except:
            pass
    if is_new:
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(path + ext):
                try:
                    os.chmod(path + ext, 0o600)
                except:
                    pass
    return conn

@contextmanager
def transaction(conn):
    """BEGIN IMMEDIATE serializa escritores: base para saldo consistente sob concorrência."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")

def migrate(conn, migrations_dir):
    """Aplica migrations pendentes, cada uma atomicamente. Nunca recria nem apaga dados."""
    conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations(
        version TEXT PRIMARY KEY, checksum TEXT NOT NULL,
        applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')))""")
    applied = {r["version"]: r["checksum"] for r in conn.execute("SELECT version, checksum FROM schema_migrations")}
    done = []
    for f in sorted(Path(migrations_dir).glob("[0-9][0-9][0-9]_*.sql")):
        sql = f.read_text("utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        if f.stem in applied:
            if applied[f.stem] != checksum:
                raise MigrationError(f"Migration {f.stem} foi alterada após ser aplicada. Crie uma nova migration.")
            continue
        try:
            conn.execute("BEGIN IMMEDIATE;\\n" + sql + "\\n;")
            conn.execute("INSERT INTO schema_migrations(version, checksum) VALUES (?,?)", (f.stem, checksum))
            conn.execute("COMMIT")
        except Exception as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise MigrationError(f"Falha em {f.stem}: {exc}") from exc
        done.append(f.stem)
    return done

def schema_version(conn):
    row = conn.execute("SELECT max(version) AS v FROM schema_migrations").fetchone()
    return row["v"]

def integrity_report(conn):
    return {
        "integrity_check": [r[0] for r in conn.execute("PRAGMA integrity_check")],
        "foreign_key_violations": [tuple(r) for r in conn.execute("PRAGMA foreign_key_check")],
    }
'''

import pathlib
pathlib.Path('app/db.py').write_text(code, encoding='utf-8')
print('DB.PY CORRIGIDO!')