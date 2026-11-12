"""Conversão segura entre decimais (entrada do usuário) e inteiros do banco; formatação pt-BR.
Quantidade: milésimos (3 casas). Dinheiro/custo: 1/10000 de R$ (4 casas)."""
from decimal import Decimal, InvalidOperation

class NumError(ValueError):
    pass

LIMIT = Decimal("1000000000")

def _dec(v):
    if v is None or isinstance(v, bool) or str(v).strip() == "":
        raise NumError("Informe um número.")
    s = str(v).strip().replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")  # 1.234,50 (pt-BR)
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise NumError("Número inválido.") from None
    if not d.is_finite():
        raise NumError("Número inválido.")
    if d < 0:
        raise NumError("O valor não pode ser negativo.")
    if d > LIMIT:
        raise NumError("Valor acima do limite permitido.")
    return d

def parse_qty(v, allow_fraction=True):
    d = _dec(v)
    if not allow_fraction and d != d.to_integral_value():
        raise NumError("Esta unidade de medida não aceita quantidades fracionadas.")
    m = d * 1000
    if m != m.to_integral_value():
        raise NumError("Use no máximo 3 casas decimais.")
    return int(m)

def parse_money(v):
    d = _dec(v)
    e4 = d * 10000
    if e4 != e4.to_integral_value():
        raise NumError("Use no máximo 4 casas decimais.")
    return int(e4)

def _br(d, places):
    s = f"{d:,.{places}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")

def fmt_qty(milli):
    d = Decimal(milli) / 1000
    s = f"{d:.3f}".rstrip("0").rstrip(".")
    return _br(Decimal(s), len(s.split(".")[1]) if "." in s else 0)

def fmt_money(e4):
    d = Decimal(e4) / 10000
    return "R$ " + _br(d, 2 if e4 % 100 == 0 else 4)
