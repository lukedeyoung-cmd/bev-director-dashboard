"""Jinja template filters for dashboard number formatting.

Aggregates arrive as Decimal from Postgres but float from sqlite; every
filter normalizes through `Decimal(str(...))` first. All filters render
"-" for None so absent data (e.g. cost metrics before R365 ingestion)
renders cleanly instead of crashing the page.
"""

from decimal import Decimal

DASH = "-"


def _dec(value: object) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def money(value: object) -> str:
    """Whole dollars: $12,345"""
    d = _dec(value)
    if d is None:
        return DASH
    return f"${d.quantize(Decimal('1')):,.0f}"


def money_cents(value: object) -> str:
    """Dollars and cents: $1,234.56"""
    d = _dec(value)
    if d is None:
        return DASH
    return f"${d:,.2f}"


def money_signed(value: object) -> str:
    """Signed whole dollars for deltas: +$1,234 / -$987"""
    d = _dec(value)
    if d is None:
        return DASH
    sign = "+" if d >= 0 else "\u2212"
    return f"{sign}${abs(d):,.0f}"


def money_signed_cents(value: object) -> str:
    """Signed dollars-and-cents for variance: +$123.45 / -$54.32"""
    d = _dec(value)
    if d is None:
        return DASH
    sign = "+" if d >= 0 else "\u2212"
    return f"{sign}${abs(d):,.2f}"


def pct(value: object, digits: int = 1) -> str:
    """Percentage with sign for deltas: +4.2% / -1.3%"""
    if value is None:
        return DASH
    return f"{float(value):+.{digits}f}%"


def pct_plain(value: object, digits: int = 1) -> str:
    """Unsigned percentage: 27.4%"""
    if value is None:
        return DASH
    return f"{float(value):.{digits}f}%"


def qty(value: object) -> str:
    """Trimmed quantity: 128 or 128.5 (never scientific notation)."""
    d = _dec(value)
    if d is None:
        return DASH
    if d == d.to_integral_value():
        return f"{d:,.0f}"
    return f"{d:,}".rstrip("0").rstrip(".")


def register_filters(app) -> None:
    for name, fn in {
        "money": money,
        "money_cents": money_cents,
        "money_signed": money_signed,
        "money_signed_cents": money_signed_cents,
        "pct": pct,
        "pct_plain": pct_plain,
        "qty": qty,
    }.items():
        app.add_template_filter(fn, name)
