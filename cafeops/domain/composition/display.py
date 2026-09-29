"""`cafeops.domain.composition`, the display section.

Split from one 2,270-line module on 2026-09-29 (ARCHITECTURE 8Y). Import the public
names from the package; this module is an implementation detail of it.
"""

from __future__ import annotations

from decimal import Decimal

from cafeops.domain.types import (
    SizeCode,
    Unit,
)

# ==========================================================================
# Any edit, previewed the same way (back-office redesign, recipes spec A2-A4)
# ==========================================================================
#
# `ItemImpact` above has ONE price, which is right for a quantity edit and wrong for
# everything the redesign adds: a base-price edit moves the price, a new flavour has
# no "before" at all, and taking a flavour off the menu has no "after" worth pricing.
# `ItemChange` carries both sides of every figure, and `summarise_changes` is the one
# definition of "affected", "excluded for an unknown cost" and "thinnest margin" that
# every redesign preview uses -- recipe changesets, manual lines, sell prices and
# ingredient prices alike. One definition, so two previews cannot disagree about
# what the same edit does.

#: The order sizes are shown in everywhere, and the order the design adds them in.
SIZE_ORDER: tuple[SizeCode, ...] = (SizeCode.S, SizeCode.M, SizeCode.XL, SizeCode.ONE)


_UNIT_LABEL: dict[Unit, str] = {
    Unit.L: "L",
    Unit.ML: "ml",
    Unit.KG: "kg",
    Unit.G: "g",
    Unit.EACH: "unit",
}


def unit_label(unit: Unit | None) -> str:
    """The design's unit words: L, ml, kg, g, unit."""
    return "" if unit is None else _UNIT_LABEL[unit]


def gbp(pence: int | Decimal) -> str:
    """`£3.90`, or U+2212 then `£0.60`. The design's `gbp`, for server-built sentences."""
    value = Decimal(pence)
    sign = "\u2212" if value < 0 else ""
    return f"{sign}£{abs(value) / 100:.2f}"


def unit_gbp(pence: Decimal) -> str:
    """Pence per unit the design's way: £1.23, £0.734, £0.0450 (2/3/4 dp by size)."""
    pounds = abs(pence) / 100
    places = 2 if pounds >= 1 else 3 if pounds >= Decimal("0.1") else 4
    sign = "\u2212" if pence < 0 else ""
    return f"{sign}£{pounds:.{places}f}"


def qty_text(value: Decimal) -> str:
    """A quantity without trailing zeros or an exponent: 0.180 -> 0.18, 1E+2 -> 100."""
    if value == 0:
        return "0"
    text: str = f"{value.normalize():f}"
    return text


def _size_word(size: SizeCode | None) -> str:
    if size is None or size is SizeCode.ONE:
        return "One"
    return str(size.value)
