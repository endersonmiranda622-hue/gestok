"""Gera docs/MODELO_DE_DADOS.md a partir das migrations (fonte da verdade). Uso: python tools/gen_schema_doc.py"""
import os, sqlite3, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import MIGRATIONS_DIR, db as dbm

def main():
    path = os.path.join(tempfile.mkdtemp(), "doc.db"); c = dbm.connect(path); applied = dbm.migrate(c, MIGRATIONS_DIR)
    out = ["# Modelo de dados do GESTOK", "", "Gerado automaticamente das migrations por `tools/gen_schema_doc.py` (não edite à mão).", "",
           f"Migrations: {', '.join(applied)}.", "",
           "Convenções: quantidades em milésimos (INTEGER); custos/preços em 1/10000 de R$ (INTEGER); valores financeiros agregados em centavos; datas em UTC ISO-8601.",
           "FKs compostas `(id, company_id)` impedem, no próprio banco, ligar registros de empresas diferentes. Tabelas `stock_movements`, `movement_lines` e `audit_log` são imutáveis (triggers).", ""]
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for t in tables:
        out += [f"## `{t}`", "", "| Coluna | Tipo | Obrigatória | Padrão | Chave |", "|---|---|---|---|---|"]
        for cid, name, typ, nn, dflt, pk in c.execute(f"PRAGMA table_info({t})"):
            out.append(f"| {name} | {typ or '-'} | {'sim' if nn or pk else 'não'} | {'' if dflt is None else '`' + str(dflt).replace('|', '/') + '`'} | {'PK' if pk else ''} |")
        fks = list(c.execute(f"PRAGMA foreign_key_list({t})"))
        if fks:
            grp = {}
            for r in fks: grp.setdefault(r[0], []).append(r)
            out += ["", "Chaves estrangeiras: " + "; ".join(f"({', '.join(x[3] for x in g)}) → {g[0][2]}({', '.join(x[4] for x in g)})" for g in grp.values())]
        idx = [(r[1], r[2]) for r in c.execute(f"PRAGMA index_list({t})") if not r[1].startswith("sqlite_autoindex")]
        uq = [", ".join(x[2] for x in c.execute(f"PRAGMA index_info({r[1]})")) for r in c.execute(f"PRAGMA index_list({t})") if r[2] and r[1].startswith("sqlite_autoindex")]
        if uq: out += ["", "Unicidade: " + "; ".join(f"({u})" for u in uq)]
        if idx: out += ["", "Índices: " + ", ".join(f"`{n}`" + (" (único)" if u else "") for n, u in idx)]
        trg = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (t,))]
        if trg: out += ["", "Triggers (imutabilidade): " + ", ".join(f"`{n}`" for n in trg)]
        out.append("")
    open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "MODELO_DE_DADOS.md"), "w", encoding="utf-8").write("\n".join(out))
    print(f"{len(tables)} tabelas documentadas.")
main()
