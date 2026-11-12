"""Exportação CSV/XLSX/PDF dos relatórios. Reaproveita run_report (mesmos filtros, permissões e isolamento por empresa).
Proteção contra injeção de fórmulas: texto que começa com = + - @ TAB ou CR ganha um apóstrofo inicial (CSV e XLSX)."""
import csv, io
from datetime import date, datetime, timezone
from decimal import Decimal
from xml.sax.saxutils import escape
from flask import Blueprint, Response, g, jsonify, request
from . import accounts as acc, analytics as an, get_db
from .authz import company_id, require_perm
from .dashboard import run_report
from .numbers import fmt_money, fmt_qty
from .stock import StockError

bp = Blueprint("exports", __name__)
MAX_ROWS = 50000
_DANGER = ("=", "+", "-", "@", "\t", "\r")
MIME = {"csv": "text/csv; charset=utf-8", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "pdf": "application/pdf"}

@bp.errorhandler(StockError)
def _err(e):
    return jsonify(error=e.msg, field=e.field), e.code

def safe(v):
    s = str(v)
    return "'" + s if s and s[0] in _DANGER else s

def _local(iso, tz):
    return datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).astimezone(tz)

def _comma(d, places, trim=False):
    s = f"{d:.{places}f}"
    if trim: s = s.rstrip("0").rstrip(".")
    return s.replace(".", ",")

def _plain(t, v, tz):   # CSV: sem separador de milhar; vírgula decimal (abre corretamente no Excel pt-BR)
    if v is None: return ""
    if t == "qty": return _comma(Decimal(v) / 1000, 3, True)
    if t == "cents": return _comma(Decimal(v) / 100, 2)
    if t == "e4":
        s = f"{Decimal(v) / 10000:.4f}"; return (s[:-2] if s.endswith("00") else s).replace(".", ",")
    if t == "int": return str(v)
    if t == "date": return date.fromisoformat(v).strftime("%d/%m/%Y")
    if t == "datetime": return _local(v, tz).strftime("%d/%m/%Y %H:%M")
    return safe(v)

def _show(t, v, tz):    # PDF: formatação de leitura pt-BR
    if v is None: return ""
    if t == "qty": return fmt_qty(v)
    if t == "cents": return fmt_money(v * 100)
    if t == "e4": return fmt_money(v)
    return _plain(t, v, tz) if t in ("date", "datetime", "int") else str(v)

def _typed(t, v, tz):   # XLSX: valores nativos + formato numérico
    if v is None: return None, None
    if t == "qty": return float(Decimal(v) / 1000), "#,##0.###"
    if t == "cents": return float(Decimal(v) / 100), '"R$" #,##0.00'
    if t == "e4": return float(Decimal(v) / 10000), '"R$" #,##0.00##'
    if t == "int": return int(v), "0"
    if t == "date": return date.fromisoformat(v), "dd/mm/yyyy"
    if t == "datetime": return _local(v, tz).replace(tzinfo=None), "dd/mm/yyyy hh:mm"
    return safe(v), None

def _total_row(r):
    if r["totals"].get("value_cents") is None: return None
    cols = r["columns"]; row = [None] * len(cols); row[0] = "TOTAL"
    for i, c in enumerate(cols):
        if c["key"] == "value_cents": row[i] = r["totals"]["value_cents"]
    return row

def to_csv(r, tz):
    out = io.StringIO(); w = csv.writer(out, delimiter=";", lineterminator="\r\n")
    w.writerow([safe(c["label"]) for c in r["columns"]])
    for row in r["rows"]: w.writerow([_plain(c["type"], row.get(c["key"]), tz) for c in r["columns"]])
    t = _total_row(r)
    if t: w.writerow([("TOTAL" if i == 0 else _plain("cents", v, tz)) if (i == 0 or v is not None) else "" for i, v in enumerate(t)])
    return ("\ufeff" + out.getvalue()).encode("utf-8")

def to_xlsx(r, tz, meta):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook(); ws = wb.active; ws.title = r["title"][:31]
    ws.append([r["title"]]); ws["A1"].font = Font(bold=True, size=14)
    ws.append([meta["company"] + " - gerado em " + meta["generated"]]); ws.append(["Filtros: " + (meta["filters"] or "nenhum")]); ws.append([])
    ws.append([safe(c["label"]) for c in r["columns"]])
    for cell in ws[5]: cell.font = Font(bold=True, color="FFFFFF"); cell.fill = PatternFill("solid", fgColor="0F2A43")
    for row in r["rows"]:
        ws.append([None] * len(r["columns"]))
        for i, c in enumerate(r["columns"]):
            v, fmt = _typed(c["type"], row.get(c["key"]), tz); cell = ws.cell(ws.max_row, i + 1)
            cell.value = v
            if isinstance(v, str): cell.data_type = "s"   # nunca interpretar como fórmula
            if fmt: cell.number_format = fmt
    t = _total_row(r)
    if t:
        ws.append([None] * len(t)); ws.cell(ws.max_row, 1).value = "TOTAL"
        for i, c in enumerate(r["columns"]):
            if t[i] is not None and i:
                cell = ws.cell(ws.max_row, i + 1); cell.value, cell.number_format = _typed("cents", t[i], tz)
        for cell in ws[ws.max_row]: cell.font = Font(bold=True)
    ws.freeze_panes = "A6"
    if r["rows"]: ws.auto_filter.ref = f"A5:{ws.cell(5, len(r['columns'])).column_letter}{5 + len(r['rows'])}"
    for i, c in enumerate(r["columns"]): ws.column_dimensions[ws.cell(5, i + 1).column_letter].width = min(max(len(c["label"]) + 4, 12), 40)
    b = io.BytesIO(); wb.save(b); return b.getvalue()

def to_pdf(r, tz, meta):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    st = getSampleStyleSheet(); small = ParagraphStyle("s", parent=st["Normal"], fontSize=7, leading=8.5); right = ParagraphStyle("r", parent=small, alignment=2)
    head = ParagraphStyle("h", parent=small, textColor=colors.white, fontName="Helvetica-Bold")
    b = io.BytesIO(); doc = SimpleDocTemplate(b, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=28, bottomMargin=28, title=r["title"])
    num = {"qty", "cents", "e4", "int"}; P = lambda s, x: Paragraph(escape(str(s)), x)
    data = [[P(c["label"], head) for c in r["columns"]]]
    for row in r["rows"]: data.append([P(_show(c["type"], row.get(c["key"]), tz), right if c["type"] in num else small) for c in r["columns"]])
    t = _total_row(r)
    if t: data.append([P("TOTAL" if i == 0 else (fmt_money(v * 100) if v is not None else ""), right if i else small) for i, v in enumerate(t)])
    tbl = Table(data, repeatRows=1)
    tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F2A43")), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c5ced8")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                             ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f5f8")])]))
    def foot(c, d):
        c.setFont("Helvetica", 7); c.drawString(24, 14, f"GESTOK - {meta['company']} - {meta['generated']}"); c.drawRightString(landscape(A4)[0] - 24, 14, f"Pagina {d.page}")
    doc.build([P(r["title"], st["Title"]), P(f"{meta['company']} - gerado em {meta['generated']}", small), P("Filtros: " + (meta["filters"] or "nenhum"), small), Spacer(1, 8), tbl], onFirstPage=foot, onLaterPages=foot)
    return b.getvalue()

@bp.get("/api/reports/<name>/export")
@require_perm("reports.view")
def export(name):
    fmt = request.args.get("format", "csv")
    if fmt not in MIME: raise StockError("Formato inválido (use csv, xlsx ou pdf).", field="format")
    db, cid = get_db(), company_id()
    n = run_report(db, cid, name, request.args, 1, 1)["total_rows"]    # valida filtros/relatório e conta antes de carregar tudo
    if n > MAX_ROWS: raise StockError(f"O relatório tem {n} linhas (máximo {MAX_ROWS}). Refine os filtros.", 413)
    r = run_report(db, cid, name, request.args, 1, None)
    tz = an._tz(db, cid); now = datetime.now(timezone.utc).astimezone(tz)
    meta = {"company": g.user["company_name"], "generated": now.strftime("%d/%m/%Y %H:%M"), "filters": ", ".join(f"{k}={v}" for k, v in request.args.items() if k != "format" and v)}
    body = to_csv(r, tz) if fmt == "csv" else to_xlsx(r, tz, meta) if fmt == "xlsx" else to_pdf(r, tz, meta)
    acc.audit(db, cid, g.user["id"], "report.exported", "report", name, {"format": fmt, "rows": r["total_rows"], "filters": meta["filters"]}, request.remote_addr)
    resp = Response(body, mimetype=MIME[fmt].split(";")[0]); resp.headers["Content-Type"] = MIME[fmt]
    resp.headers["Content-Disposition"] = f'attachment; filename="gestok_{name}_{now.strftime("%Y%m%d")}.{fmt}"'
    return resp
