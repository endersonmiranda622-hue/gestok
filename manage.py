#!/usr/bin/env python3
"""Uso: python manage.py migrate | status | check"""
import argparse, getpass, os, sys
from app import MIGRATIONS_DIR
from app.config import load_config
from app import db as dbm

def main():
    p = argparse.ArgumentParser(description="Administração do GESTOK")
    p.add_argument("command", choices=["migrate", "status", "check", "create-admin", "reset-link", "reconcile", "backup", "restore", "doctor"])
    p.add_argument("--company"); p.add_argument("--name"); p.add_argument("--email")
    p.add_argument("--dest", default="backups"); p.add_argument("--keep", type=int); p.add_argument("--file"); p.add_argument("--force", action="store_true")
    args = p.parse_args(); cmd = args.command
    cfg = load_config()
    from app import ops
    if cmd in ("backup", "restore", "doctor"):   # não abrem/criam o banco antes de verificar
        try:
            if cmd == "backup":
                t, h = ops.backup(cfg["DATABASE"], args.dest, args.keep); print(f"Backup criado e verificado: {t}\nSHA-256: {h}\nGuarde o arquivo em local seguro: ele contém e-mails, hashes de senha e IPs.")
            elif cmd == "restore":
                if not args.file: sys.exit("Informe --file caminho/do/backup.db")
                i = ops.restore(args.file, cfg["DATABASE"], args.force); print("Restaurado:", i, "\nRode 'python manage.py migrate' e 'python manage.py doctor' antes de subir a aplicação.")
            else:
                res = ops.doctor(cfg)
                for lvl, m in res: print(f"[{lvl}] {m}")
                sys.exit(1 if any(l == "FAIL" for l, _ in res) else 0)
        except ops.OpsError as e: sys.exit(f"Erro: {e}")
        return
    conn = dbm.connect(cfg["DATABASE"])
    if cmd == "migrate":
        done = dbm.migrate(conn, MIGRATIONS_DIR)
        print("Aplicadas:", ", ".join(done) if done else "nenhuma (banco já atualizado)")
    elif cmd == "status":
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)")
        for r in conn.execute("SELECT version, applied_at FROM schema_migrations ORDER BY version"):
            print(r["version"], r["applied_at"])
    elif cmd == "create-admin":
        from app import accounts
        if not (args.company and args.name and args.email):
            sys.exit("Informe --company, --name e --email.")
        pw = os.environ.get("GESTOK_ADMIN_PASSWORD")
        if not pw:
            pw = getpass.getpass("Senha do administrador: ")
            if pw != getpass.getpass("Repita a senha: "):
                sys.exit("As senhas não conferem.")
        try:
            cid, uid = accounts.create_company_with_admin(conn, args.company, args.name, args.email, pw)
        except accounts.ValidationError as e:
            sys.exit(f"Erro: {e}")
        print(f"Empresa {cid} e administrador {uid} criados.")
    elif cmd == "reconcile":
        from app import stock
        bad = stock.reconcile(conn)
        print("Saldos consistentes com o histórico." if not bad else f"{len(bad)} divergência(s): {bad}")
        sys.exit(1 if bad else 0)
    elif cmd == "reset-link":
        from app import accounts
        t = accounts.issue_reset_token(conn, args.email or "")
        print(f"{cfg['BASE_URL'].rstrip('/')}/reset/{t} (válido por {accounts.RESET_TTL_MIN} min, uso único)" if t else "Usuário inexistente, inativo ou limite de pedidos atingido.")
    else:
        rep = dbm.integrity_report(conn)
        print(rep)
        sys.exit(0 if rep["integrity_check"] == ["ok"] and not rep["foreign_key_violations"] else 1)

if __name__ == "__main__":
    main()
