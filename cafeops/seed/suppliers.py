"""The real supplier set. Spec 4.4.

Eight suppliers, and only two of them have terms I actually know.

**Everything marked `terms_are_placeholders=True` is invented.** Lead time, delivery
weekdays, cutoff, minimum order and free-delivery threshold are guesses that look
plausible for a UK foodservice wholesaler. The owner chose to proceed on placeholders
rather than block, so the obligation is to make them impossible to mistake for facts:
every import warns, every supplier row carries the flag, and every order built from
one says so on its face. A cover window is only as good as the lead time behind it,
and a confident quantity derived from an invented delivery schedule is worse than no
quantity at all.

Tesco and Amazon are the honest two: walk-in and next-day-ish, no minimum, no
schedule worth modelling.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from cafeops.db.models import OrderChannel

__all__ = [
    "ALTERNATE_SOURCES",
    "CATEGORY_TO_SUPPLIER",
    "DEFAULT_SUPPLIER",
    "SUPPLIERS",
    "SupplierSeed",
]


@dataclass(frozen=True, slots=True)
class SupplierSeed:
    name: str
    lead_time_days: int
    #: ISO weekdays, Mon=1..Sun=7. EMPTY means any day -- walk-in retail.
    delivery_weekdays: tuple[int, ...]
    min_order_pence: int
    order_channel: OrderChannel
    contact: str
    cutoff_time: time | None = None
    delivery_fee_pence: int = 0
    free_delivery_threshold_pence: int | None = None
    terms_are_placeholders: bool = True
    supplies: str = ""
    order_url: str | None = None
    notes: str | None = None


SUPPLIERS: tuple[SupplierSeed, ...] = (
    SupplierSeed(
        name="Cakesmiths",
        lead_time_days=2,
        delivery_weekdays=(2, 5),
        min_order_pence=5000,
        order_channel=OrderChannel.PORTAL,
        contact="https://cakesmiths.example/account",
        cutoff_time=time(14, 0),
        delivery_fee_pence=0,
        free_delivery_threshold_pence=7500,
        supplies="Cakes, traybakes",
        order_url="https://cakesmiths.example/order",
    ),
    SupplierSeed(
        name="Brakes",
        lead_time_days=1,
        delivery_weekdays=(1, 3, 5),
        min_order_pence=7500,
        order_channel=OrderChannel.PORTAL,
        contact="https://brakes.example/account",
        cutoff_time=time(16, 0),
        delivery_fee_pence=0,
        free_delivery_threshold_pence=10000,
        supplies="Foodservice, chilled",
        notes="Spec 4.4 lists PORTAL / EDI. EDI needs a real integration; PORTAL first.",
    ),
    SupplierSeed(
        name="Booker",
        lead_time_days=2,
        delivery_weekdays=(2, 4),
        min_order_pence=5000,
        order_channel=OrderChannel.PORTAL,
        contact="https://booker.example/account",
        cutoff_time=time(12, 0),
        delivery_fee_pence=0,
        free_delivery_threshold_pence=6000,
        supplies="Wholesale general",
    ),
    SupplierSeed(
        name="Cups Direct",
        lead_time_days=3,
        delivery_weekdays=(1, 2, 3, 4, 5),
        min_order_pence=3000,
        order_channel=OrderChannel.PORTAL,
        contact="https://cupsdirect.example",
        cutoff_time=time(15, 0),
        delivery_fee_pence=595,
        free_delivery_threshold_pence=7500,
        supplies="Packaging",
        order_url="https://cupsdirect.example/basket",
    ),
    SupplierSeed(
        name="Monolith",
        lead_time_days=3,
        delivery_weekdays=(3,),
        min_order_pence=10000,
        order_channel=OrderChannel.EMAIL,
        contact="orders@monolith.example",
        cutoff_time=time(11, 0),
        supplies="Eastern European lines",
    ),
    # --- the two whose terms are genuinely known -----------------------------
    SupplierSeed(
        name="Tesco",
        lead_time_days=0,
        delivery_weekdays=(),  # walk-in: any day
        min_order_pence=0,
        order_channel=OrderChannel.MANUAL,
        contact="Walk-in, Dundee",
        terms_are_placeholders=False,
        supplies="Gap-fill, emergencies",
        notes=(
            "Every routing here is a retail premium paid. The accumulated log is the "
            "argument for fixing the ordering cadence (spec 4.4)."
        ),
    ),
    SupplierSeed(
        name="Amazon",
        lead_time_days=2,
        delivery_weekdays=(1, 2, 3, 4, 5, 6),
        min_order_pence=0,
        order_channel=OrderChannel.MANUAL,
        contact="https://amazon.co.uk",
        terms_are_placeholders=False,
        supplies="Sundries",
    ),
    SupplierSeed(
        name="Nataly (custom)",
        lead_time_days=5,
        delivery_weekdays=(),
        min_order_pence=0,
        order_channel=OrderChannel.MANUAL,
        contact="message",
        supplies="Bespoke items",
        notes=(
            "Spec 15 question 5 is UNANSWERED: what this supplier covers and through "
            "what channel. Modelled as MANUAL message-based with a 5-day lead so it "
            "never silently wins a sourcing decision."
        ),
    ),
)

#: Which supplier a workbook category is bought from. Still an inference -- the
#: workbook's notes column records where a PRICE came from, not who sells it.
CATEGORY_TO_SUPPLIER: dict[str, str] = {
    "Packaging": "Cups Direct",
    "Sundries": "Cups Direct",
    "Cake": "Cakesmiths",
    "Cake (CakeSmiths)": "Cakesmiths",
    "Food": "Brakes",
    "Dairy": "Brakes",
    "Dairy alt": "Booker",
    "Bottled": "Booker",
    "Coffee": "Booker",
    "Tea": "Booker",
    "Syrup": "Booker",
    "Chocolate": "Booker",
    "Specialty": "Monolith",
}
DEFAULT_SUPPLIER = "Booker"

#: Second sources, so multi-sourcing (spec 4.4) has something real to choose between.
#: Each is (ingredient name, supplier, pack size in the ingredient's unit, pack pence).
#: Deliberately DIFFERENT pack sizes from the primary: spec 16 asks for two suppliers
#: with different pack sizes, and the interesting sourcing decision is a cheaper unit
#: price in a pack too big to use before it spoils.
ALTERNATE_SOURCES: tuple[tuple[str, str, str, int], ...] = (
    # Milk: Brakes sells the 3.4 L catering bottle; Tesco a 2 L at a worse unit price
    # but available today. The emergency path needs a real retail price to compare.
    ("Whole milk", "Tesco", "2", 165),
    ("Semi-skimmed milk", "Tesco", "2", 165),
    # Oat milk: Booker by the case, Tesco singly and dearer.
    ("Oat milk (barista)", "Tesco", "1", 190),
    # Beans: Monolith in a 3 kg bag, cheaper per kg than the 5 kg from Booker is not
    # plausible, so this one is dearer per kg and exists to be rejected -- the
    # sourcing code needs a case where the alternate LOSES.
    ("Coffee beans (house blend)", "Monolith", "3", 6300),
    # Cups: Amazon in a 100-pack, far dearer per unit than Cups Direct's 500.
    ("12oz paper cup", "Amazon", "100", 1499),
    ("12oz cup lid", "Amazon", "100", 999),
)
