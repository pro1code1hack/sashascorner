"""How values cross the wire. One place, so no payload invents its own rule.

Three rules, and every one of them exists because of an invariant rather than a
preference:

1. **Money is integer pence where the database stores an integer** (`price_pence`,
   `min_order_pence`, a pack price, an order total) and an **exact decimal string of
   pence** where the value is derived and genuinely fractional (an ingredient cost of
   42.375p, a labour cost of 8.0555p). Never a float, in either case -- invariant 11.
   Rounding a derived cost to a whole penny at the edge would make the margin screen
   disagree with the recipe by a penny per item and nobody would be able to say why;
   emitting it as a float would be the bug invariant 11 names.

2. **Quantities are strings**, always. `qty_by_size` is `{"S": "0.12"}` on the way in
   and on the way out (`ARCHITECTURE.md` 7.4). JSON has only doubles, and a recipe
   editor that turns 0.1 + 0.2 into 0.30000000000000004 is not acceptable.

3. **`None` survives.** A missing cost is `null`, never `0` (invariant 8). These
   helpers all return `None` for `None` rather than a zero default, and no caller may
   substitute one -- that substitution is the exact failure invariant 8 exists to
   prevent.

Timestamps are tz-aware UTC ISO-8601; Pydantic serialises `datetime` that way on its
own, so there is nothing to do here beyond never handing it a naive one.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

__all__ = ["QTY_SCALE", "as_pence", "as_qty", "iso", "pct"]

#: `db/types.Qty` stores six decimal places and rounds half-up on the way in, so six is
#: the declared scale and a real contract, not a display choice (ARCHITECTURE 8E). A
#: derived quantity -- a forecast, a need, a run-out -- comes out of the arithmetic with
#: 28 significant digits, and emitting all of them would claim a precision the storage
#: does not have while making every payload unreadable. Rounded the same way the column
#: rounds, so the wire value and the stored value agree.
QTY_SCALE = Decimal("0.000001")
QTY_EXPONENT = -6


def as_qty(value: Decimal | int | None) -> str | None:
    """A quantity as a decimal string at the declared scale. `None` stays `None`."""
    return _scaled(value)


def as_pence(value: Decimal | int | None) -> str | None:
    """A derived money figure as an exact decimal string of pence. `None` stays `None`.

    Used only for money the system computes (costs, labour, premiums). Money the
    database stores as an integer stays an `int` in the schema -- see the module
    docstring. Rounded to the same six places as a quantity: 1e-6 of a penny is not a
    precision anybody has, and 28 digits of it in a payload is noise, not rigour.
    """
    return _scaled(value)


def _scaled(value: Decimal | int | None) -> str | None:
    """Round to the declared scale and format without an exponent.

    `format(..., "f")` rather than `str()` so a Decimal carrying an exponent ("1E+2")
    does not reach the frontend in scientific notation -- exact, but not what a recipe
    editor should have to parse.
    """
    if value is None:
        return None
    number = Decimal(value)
    exponent = number.as_tuple().exponent
    if isinstance(exponent, int) and exponent < QTY_EXPONENT:
        number = number.quantize(QTY_SCALE, rounding=ROUND_HALF_UP)
    return format(number, "f")


def pct(value: float | None) -> float | None:
    """A percentage. A float is acceptable here precisely because it is NOT money.

    Rounded to three places so the payload does not carry a binary artefact
    ("70.83333333333334") that a reader would mistake for precision.
    """
    if value is None:
        return None
    return round(value, 3)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
