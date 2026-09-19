"""Units and exact conversion between compatible ones.

Spec 4.1 keeps ML and G as first-class stocking units rather than normalising
everything to L/KG/EACH. The legacy workbook genuinely works that way -- syrups
are bought by the litre but costed and recipe'd per ml -- and forcing a single
canonical unit would mean rewriting every recipe quantity at import and losing the
unit the owner actually thinks in.

The cost of that choice is this module: whenever two quantities of the same
ingredient meet (a recipe line in ml against a pack in L), somebody has to
convert, exactly. All ratios are Decimal. No floats anywhere.
"""

from __future__ import annotations

import enum
from decimal import Decimal

from cafeops.domain.types import Unit

__all__ = [
    "Dimension",
    "IncompatibleUnitsError",
    "UnknownUnitError",
    "convert",
    "dimension_of",
    "format_qty",
    "parse_unit",
    "same_dimension",
]


class Dimension(enum.StrEnum):
    VOLUME = "VOLUME"
    MASS = "MASS"
    COUNT = "COUNT"


class UnknownUnitError(ValueError):
    """Raised rather than guessing. A wrong guess corrupts the ledger silently."""


class IncompatibleUnitsError(ValueError):
    """Litres into kilograms. Needs a density, which we do not model."""


#: Canonical unit per dimension, and each unit's factor to it.
_DIMENSION: dict[Unit, Dimension] = {
    Unit.L: Dimension.VOLUME,
    Unit.ML: Dimension.VOLUME,
    Unit.KG: Dimension.MASS,
    Unit.G: Dimension.MASS,
    Unit.EACH: Dimension.COUNT,
}

#: Factor to the dimension's base unit (L for volume, KG for mass, EACH for count).
_TO_BASE: dict[Unit, Decimal] = {
    Unit.L: Decimal("1"),
    Unit.ML: Decimal("0.001"),
    Unit.KG: Decimal("1"),
    Unit.G: Decimal("0.001"),
    Unit.EACH: Decimal("1"),
}

#: Workbook and POS unit strings -> our Unit. Deliberately explicit: a typo in a
#: spreadsheet must fail loudly, not resolve to something plausible.
_ALIASES: dict[str, Unit] = {
    "l": Unit.L,
    "litre": Unit.L,
    "litres": Unit.L,
    "liter": Unit.L,
    "liters": Unit.L,
    "ml": Unit.ML,
    "millilitre": Unit.ML,
    "cl": Unit.ML,  # handled by the multiplier table below
    "kg": Unit.KG,
    "kilo": Unit.KG,
    "kilos": Unit.KG,
    "kilogram": Unit.KG,
    "kilograms": Unit.KG,
    "g": Unit.G,
    "gr": Unit.G,
    "gram": Unit.G,
    "grams": Unit.G,
    "unit": Unit.EACH,
    "units": Unit.EACH,
    "each": Unit.EACH,
    "ea": Unit.EACH,
    "item": Unit.EACH,
    "items": Unit.EACH,
    "pc": Unit.EACH,
    "pcs": Unit.EACH,
    "piece": Unit.EACH,
    "pieces": Unit.EACH,
    "bag": Unit.EACH,
    "loaf": Unit.EACH,
    "bottle": Unit.EACH,
    "box": Unit.EACH,
    "pack": Unit.EACH,
    "sachet": Unit.EACH,
    "tub": Unit.EACH,
    "slice": Unit.EACH,
    "portion": Unit.EACH,
    "can": Unit.EACH,
    "tin": Unit.EACH,
    "jar": Unit.EACH,
    "tray": Unit.EACH,
}

#: Aliases that are not 1:1 with the Unit they map to.
_ALIAS_MULTIPLIER: dict[str, Decimal] = {"cl": Decimal("10")}  # 1 cl = 10 ml


def dimension_of(unit: Unit) -> Dimension:
    return _DIMENSION[unit]


def same_dimension(a: Unit, b: Unit) -> bool:
    return _DIMENSION[a] is _DIMENSION[b]


def parse_unit(raw: str | None) -> tuple[Unit, Decimal]:
    """Map a source unit string to (Unit, multiplier into that Unit).

    Raises UnknownUnitError for anything unrecognised. A few cells in the legacy
    workbook's Unit column contain prose rather than a unit; callers are expected
    to catch this and report the row instead of inventing a unit.
    """
    if raw is None:
        raise UnknownUnitError("missing unit")
    key = str(raw).strip().lower().rstrip(".")
    if key in _ALIASES:
        return _ALIASES[key], _ALIAS_MULTIPLIER.get(key, Decimal("1"))
    # Tolerate a unit at the end of a prose cell ("... (May 2026). L"), but only
    # when the final token is itself unambiguous.
    tokens = key.replace(".", " ").replace(",", " ").split()
    if tokens and tokens[-1] in _ALIASES:
        tail = tokens[-1]
        return _ALIASES[tail], _ALIAS_MULTIPLIER.get(tail, Decimal("1"))
    raise UnknownUnitError(f"unrecognised unit {raw!r}")


def convert(qty: Decimal, from_unit: Unit, to_unit: Decimal | Unit) -> Decimal:
    """Convert exactly between units of the same dimension.

    Raises IncompatibleUnitsError across dimensions -- converting litres to
    kilograms needs a density, which this system does not model. Failing here is
    correct: a silent 1:1 would put "0.18 kg of milk" in a ledger that means
    litres.
    """
    if not isinstance(to_unit, Unit):  # defensive: catch argument-order slips
        raise TypeError("to_unit must be a Unit")
    if from_unit is to_unit:
        return qty
    if not same_dimension(from_unit, to_unit):
        raise IncompatibleUnitsError(
            f"cannot convert {from_unit.value} to {to_unit.value}: "
            f"{_DIMENSION[from_unit].value} vs {_DIMENSION[to_unit].value}"
        )
    return qty * _TO_BASE[from_unit] / _TO_BASE[to_unit]


#: Back-office labels. The dashboard is English (confirmed); the Telegram bot is
#: Russian and renders its own strings in bot/formatters.py, so domain/ stays
#: locale-free.
UNIT_LABELS: dict[Unit, str] = {
    Unit.L: "L",
    Unit.ML: "ml",
    Unit.KG: "kg",
    Unit.G: "g",
    Unit.EACH: "pcs",
}


def format_qty(qty: Decimal, unit: Unit) -> str:
    """Render a quantity for back-office output. Counts whole, measures 2-3 places."""
    if unit is Unit.EACH:
        return f"{qty.quantize(Decimal('1'))} {UNIT_LABELS[unit]}"
    if unit in (Unit.ML, Unit.G):
        return f"{qty.quantize(Decimal('0.1'))} {UNIT_LABELS[unit]}"
    return f"{qty.quantize(Decimal('0.001'))} {UNIT_LABELS[unit]}"
