"""Shelf-life defaults. Spec 4.1, and spec 15 question 3.

Shelf life is NOT in the legacy workbook, and spec 4.1 says ordering cannot run
without it: it caps order size (spec 5.4, invariant 4) and its absence is what turns
a clever ordering system into a waste generator.

The owner chose to seed defaults rather than block, so every value here is written
with `shelf_life_source = ESTIMATE` and surfaced on the data-quality screen -- the
same treatment prices get, for the same reason (invariant 8). They are industry
norms for a UK café, not measurements of Sasha's actual stock.

The one that actually bites is milk: 7 days less a 2-day transit buffer gives a
5-day usable window, which caps a 9-day cover window down to 5 and means milk is
ordered more often and in smaller quantities than the forecast alone would ask for.
That is the point.
"""

from __future__ import annotations

from dataclasses import dataclass

from cafeops.db.models import Storage

__all__ = ["DEFAULTS", "ShelfLifeDefault", "default_for"]


@dataclass(frozen=True, slots=True)
class ShelfLifeDefault:
    storage: Storage
    #: Unopened life in days. None means "does not expire" -- cups, lids, napkins.
    shelf_life_days: int | None
    #: Life after opening. None means opening does not shorten it meaningfully.
    open_life_days: int | None = None
    #: Days deducted from shelf life when sizing an order, for transit and for the
    #: fact that a delivery does not arrive fresh off the line.
    transit_buffer_days: int = 0
    note: str = ""


#: Matched by exact ingredient name first, then by category. Names win because the
#: workbook's categories are coarse: "Dairy" holds both milk (7 days) and whipping
#: cream (14), and a category-only rule would get one of them badly wrong.
BY_NAME: dict[str, ShelfLifeDefault] = {
    "Whole milk": ShelfLifeDefault(Storage.CHILLED, 7, 3, 2, "fresh dairy"),
    "Semi-skimmed milk": ShelfLifeDefault(Storage.CHILLED, 7, 3, 2, "fresh dairy"),
    "Whipping cream": ShelfLifeDefault(Storage.CHILLED, 14, 3, 2, "fresh dairy"),
    "Oat milk (barista)": ShelfLifeDefault(Storage.AMBIENT, 270, 5, 3, "UHT, chill once open"),
    "Almond milk (barista)": ShelfLifeDefault(Storage.AMBIENT, 270, 5, 3, "UHT"),
    "Soy milk (barista)": ShelfLifeDefault(Storage.AMBIENT, 270, 5, 3, "UHT"),
    "Coconut milk (barista)": ShelfLifeDefault(Storage.AMBIENT, 270, 5, 3, "UHT"),
    "Coffee beans (house blend)": ShelfLifeDefault(
        Storage.AMBIENT, 365, 21, 7, "roast date matters more than the best-before"
    ),
}

#: Category fallbacks, applied when no name matches.
BY_CATEGORY: dict[str, ShelfLifeDefault] = {
    "Dairy": ShelfLifeDefault(Storage.CHILLED, 7, 3, 2),
    "Dairy alt": ShelfLifeDefault(Storage.AMBIENT, 270, 5, 3),
    "Coffee": ShelfLifeDefault(Storage.AMBIENT, 365, 21, 7),
    "Syrup": ShelfLifeDefault(Storage.AMBIENT, 730, 90, 14),
    "Chocolate": ShelfLifeDefault(Storage.AMBIENT, 540, 180, 14),
    "Specialty": ShelfLifeDefault(Storage.AMBIENT, 540, 180, 14),
    "Tea": ShelfLifeDefault(Storage.AMBIENT, 730, None, 14),
    "Cake": ShelfLifeDefault(Storage.CHILLED, 4, None, 1, "wholesale cake, short dated"),
    "Cake (CakeSmiths)": ShelfLifeDefault(Storage.CHILLED, 4, None, 1),
    "Food": ShelfLifeDefault(Storage.CHILLED, 4, None, 1, "savoury, short dated"),
    "Bottled": ShelfLifeDefault(Storage.AMBIENT, 270, None, 14),
    # Packaging and sundries genuinely do not expire. NULL here is a statement, not
    # a gap: putting a date on a napkin would drag it into the expiry sweep forever.
    "Packaging": ShelfLifeDefault(Storage.AMBIENT, None, None, 0),
    "Sundries": ShelfLifeDefault(Storage.AMBIENT, None, None, 0),
}

#: Fallback when neither name nor category is known. Deliberately non-perishable:
#: inventing a shelf life for an unknown thing would cap its orders on a guess,
#: whereas leaving it unconstrained is visible on the data-quality screen.
UNKNOWN = ShelfLifeDefault(Storage.AMBIENT, None, None, 0, "no default known -- VERIFY")

DEFAULTS = {"by_name": BY_NAME, "by_category": BY_CATEGORY, "unknown": UNKNOWN}


def default_for(name: str, category: str | None) -> tuple[ShelfLifeDefault, bool]:
    """Return (default, is_known). `is_known=False` means it needs a human."""
    if name in BY_NAME:
        return BY_NAME[name], True
    cat = (category or "").strip()
    if cat in BY_CATEGORY:
        return BY_CATEGORY[cat], True
    return UNKNOWN, False
