"""Operação: backup consistente, restauração verificada e diagnóstico (doctor)."""
import hashlib, os, re, shutil, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from . import db as dbm, MIGRATIONS_DIR

class OpsError(RuntimeError):
    pass

_NAME = re.compile(r"^gestok-\d{8}-\d{6}-\d{6}\.db$")

def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
    return h.hexdigest()

def _verify_db(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        ic = [r[0] for r in c.execute("PRAGMA integrity_check")]
        fk = list(c.execute("PRAGMA foreign_key_check"))
        has = c.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
    except sqlite3.DatabaseError as e:
        raise OpsError(f"Arquivo de banco inválido: {e}") from None
    finally:
        c.close()
    if ic != ["ok"] or fk or not has:
        raise OpsError("O banco falhou na verificação de integridade (integrity_check/foreign_key_check/schema).")

def backup(db_path, dest_dir, keep=None):
    """Cópia consistente (API de backup do SQLite, segura mesmo com o sistema em uso), verificada e com SHA-256."""
    if not os.path.exists(db_path): raise OpsError("Banco de dados não encontrado.")
    os.makedirs(dest_dir, exist_ok=True)
    target = os.path.join(dest_dir, f"gestok-{datetime.now(timezone.utc):%Y%m%d-%H%M%S-%f}.db")
    src, dst = sqlite3.connect(db_path, timeout=30), sqlite3.connect(target)
    try: src.backup(dst)
    finally: dst.close(); src.close()
    try: _verify_db(target)
    except OpsError:
        os.remove(target); raise
    os.chmod(target, 0o600)
    digest = _sha(target); Path(target + ".sha256").write_text(f"{digest}  {os.path.basename(target)}\n")
    if keep:
        olds = sorted(f for f in os.listdir(dest_dir) if _NAME.match(f))[:-keep]
        for f in olds:
            for x in (f, f + ".sha256"):
                p = os.path.join(dest_dir, x)
                if os.path.exists(p): os.remove(p)
    return target, digest

def restore(backup_path, db_path, force=False):
    """Restaura com verificação prévia. O sistema deve estar PARADO. Nunca sobrescreve sem --force e sempre guarda cópia de segurança."""
    sidecar = backup_path + ".sha256"
    if not os.path.exists(backup_path): raise OpsError("Arquivo de backup não encontrado.")
    if not os.path.exists(sidecar): raise OpsError("Arquivo .sha256 do backup ausente: não é possível verificar a integridade.")
    if Path(sidecar).read_text().split()[0] != _sha(backup_path): raise OpsError("SHA-256 não confere: o backup foi alterado ou está corrompido.")
    _verify_db(backup_path)
    safety = None
    if os.path.exists(db_path):
        if not force: raise OpsError("Já existe um banco em uso neste caminho. Use --force para substituí-lo (uma cópia de segurança é feita antes).")
        probe = sqlite3.connect(db_path, timeout=1, isolation_level=None)
        try: probe.execute("BEGIN EXCLUSIVE"); probe.execute("ROLLBACK")
        except sqlite3.OperationalError: raise OpsError("O banco está em uso. Pare a aplicação antes de restaurar.") from None
        finally: probe.close()
        safety = f"{db_path}.pre-restore-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
        try:
            s, d = sqlite3.connect(db_path), sqlite3.connect(safety); s.backup(d); d.close(); s.close()
        except sqlite3.DatabaseError: shutil.copy2(db_path, safety)   # banco atual possivelmente corrompido: copia os bytes
    tmp = db_path + ".restoring"
    if os.path.exists(tmp): os.remove(tmp)
    s, d = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True), sqlite3.connect(tmp); s.backup(d); d.close(); s.close()
    for ext in ("-wal", "-shm"):   # WAL antigo não pode sobreviver à troca do arquivo
        if os.path.exists(db_path + ext): os.remove(db_path + ext)
    os.replace(tmp, db_path); os.chmod(db_path, 0o600)
    c = dbm.connect(db_path)
    try:
        info = {"schema": dbm.schema_version(c), "safety_copy": safety,
                "users": c.execute("SELECT count(*) FROM users").fetchone()[0], "items": c.execute("SELECT count(*) FROM items").fetchone()[0],
                "movements": c.execute("SELECT count(*) FROM stock_movements").fetchone()[0]}
        from . import stock
        info["reconcile_issues"] = len(stock.reconcile(c)) if info["movements"] or True else 0
    finally: c.close()
    return info

def doctor(cfg, env=None):
    """Lista de (nível, mensagem). Nível FAIL => não pronto para produção."""
    out = []; add = lambda lvl, m: out.append((lvl, m)); prod = cfg["ENV_MODE"] == "production"
    add("OK", f"Modo: {cfg['ENV_MODE']}")
    if prod:
        add("OK" if cfg["FORCE_HTTPS"] else "FAIL", "HTTPS obrigatório (GESTOK_FORCE_HTTPS)" if cfg["FORCE_HTTPS"] else "GESTOK_FORCE_HTTPS=0 em produção: credenciais podem trafegar sem criptografia")
        add("OK" if cfg["TRUSTED_PROXIES"] else "WARN", f"Proxies confiáveis: {cfg['TRUSTED_PROXIES']}" if cfg["TRUSTED_PROXIES"] else "GESTOK_TRUSTED_PROXIES=0: atrás de proxy, o limite de login por IP verá só o IP do proxy")
    else: add("WARN", "Modo de desenvolvimento: não use dados reais")
    path = cfg["DATABASE"]
    if not os.path.exists(path): add("FAIL", f"Banco não encontrado em {path} (rode: python manage.py migrate)"); return out
    mode = oct(os.stat(path).st_mode & 0o777)
    add("OK" if not (os.stat(path).st_mode & 0o077) else "WARN", f"Permissões do arquivo do banco: {mode}" + ("" if not (os.stat(path).st_mode & 0o077) else " (outros usuários do sistema podem ler; use chmod 600)"))
    try:
        c = dbm.connect(path); rep = dbm.integrity_report(c)
        add("OK" if rep["integrity_check"] == ["ok"] and not rep["foreign_key_violations"] else "FAIL", "Integridade do banco (integrity_check + foreign_key_check)")
        latest = sorted(f.stem for f in Path(MIGRATIONS_DIR).glob("[0-9][0-9][0-9]_*.sql"))[-1]; cur = dbm.schema_version(c)
        add("OK" if cur == latest else "FAIL", f"Esquema {cur}" + ("" if cur == latest else f" (pendente: {latest}; rode migrate)"))
        if cur == latest:
            from . import stock
            bad = stock.reconcile(c); add("OK" if not bad else "FAIL", "Saldos conferem com o histórico" if not bad else f"{len(bad)} divergência(s) entre saldos e histórico")
            n = c.execute("SELECT count(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
            add("OK" if n else "WARN", f"{n} administrador(es) ativo(s)" if n else "Nenhum administrador: rode create-admin")
        add("OK" if c.execute("PRAGMA journal_mode").fetchone()[0] == "wal" else "WARN", "Modo WAL ativo")
        c.close()
    except Exception as e: add("FAIL", f"Não foi possível verificar o banco: {e}")
    return out
