"""Hand-typed transactions: a cash sale at the counter, a Deliveroo or Just Eat order.

DECISIONS 28. The till is synced from Lightspeed and nothing here touches that path;
this module is for the sales the till never sees -- a coffee paid in cash and not rung
up, a delivery-app order the owner reads off the tablet -- so that `sale` is the whole
picture and not just the Lightspeed slice of it. Every row written here says so
(`source`, `recorded_by`) and is filterable apart from the till's rows.

Three rules, each enforced here and not in a caller:

* **`EPOS` is refused.** The till writes `EPOS`; a hand-typed till sale would count
  twice the moment the sync runs. `MANUAL_SALE_CHANNELS` is the whole list.
* **Stock is depleted the ordinary way.** A row lands with `expanded_at = NULL` and the
  nightly expansion resolves the recipe and writes the `SALE` movements, exactly as it
  does for a Lightspeed line. Nothing here writes `stock_movement` (invariant 12).
* **A mistake is voided, never deleted.** `void_sale` flips `voided` and leaves the row;
  if the sale was already expanded, `reverse_voided_expansions` puts the stock back with
  reversal movements on its next run. Deleting the row would orphan those movements.

Money is integer pence: `unit_price_pence * qty` is a `Decimal` product rounded half up
to a penny once, per line (invariant 11).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.clock import utcnow
from cafeops.config import settings
from cafeops.db.models import MenuCategory, MenuItem, Sale
from cafeops.domain.composition import SIZE_ORDER
from cafeops.domain.enums import MANUAL_SALE_CHANNELS, SaleChannel, SaleSource, SizeCode
from cafeops.services.actor import require_actor
from cafeops.services.ingest_sales import CLOCK_SKEW_TOLERANCE

__all__ = [
    "MANUAL_RECEIPT_PREFIX",
    "CheckedLine",
    "CheckedSale",
    "MenuPickItem",
    "RecordedLine",
    "RecordedSale",
    "SaleLineIn",
    "SaleRefused",
    "line_gross_pence",
    "list_menu_categories",
    "menu_items",
    "noon_of",
    "record_sale",
    "recorded_sale",
    "search_menu_items",
    "size_label",
    "validate_sale",
    "validate_sale_lines",
    "void_sale",
]

#: A hand-typed receipt is `manual:<uuid>`; its lines are `manual:<uuid>:1..n`. Same
#: two columns as a Lightspeed line so a receipt still groups, and unmistakably not a
#: Lightspeed id so a re-sync can never collide with one.
MANUAL_RECEIPT_PREFIX = "manual:"

#: How far back a hand-typed sale may be dated. A Deliveroo order entered the next
#: morning is ordinary; one dated last quarter is a typo, and it would land in a month
#: whose figures were already read.
MAX_BACKDATE = timedelta(days=62)


def size_label(size: SizeCode | None) -> str:
    """How a hand-typed sale shows a size: S, M, XL, and nothing for one-size items.

    The transactions CSV writes the same word, so a file the system exported reads back
    in (`transactions_csv`). One definition for both."""
    return "" if size is None or size is SizeCode.ONE else size.value


class SaleRefused(ValueError):
    """The request breaks a rule. Nothing was written."""


@dataclass(frozen=True, slots=True)
class SaleLineIn:
    menu_item_id: int
    qty: Decimal = Decimal(1)
    #: None: the menu price at the time of recording. Delivery apps sell at their own
    #: prices, so the owner can type what the app actually charged.
    unit_price_pence: int | None = None


@dataclass(frozen=True, slots=True)
class MenuPickItem:
    """One sellable item, as the bot's picker and the API's menu list show it."""

    menu_item_id: int
    name: str
    size: str
    category: str | None
    price_pence: int

    @property
    def label(self) -> str:
        return f"{self.name} {self.size}".strip()


@dataclass(frozen=True, slots=True)
class RecordedLine:
    sale_id: int
    menu_item_id: int
    name: str
    size: str
    qty: Decimal
    unit_price_pence: int
    gross_pence: int


@dataclass(frozen=True, slots=True)
class RecordedSale:
    receipt_id: str
    channel: SaleChannel
    source: SaleSource
    sold_at: datetime
    recorded_by: str | None
    note: str | None
    voided: bool
    lines: tuple[RecordedLine, ...]

    @property
    def total_pence(self) -> int:
        return sum(line.gross_pence for line in self.lines)


def line_gross_pence(unit_price_pence: int, qty: Decimal) -> int:
    """`price x qty`, rounded half up to a penny once. Never a float (invariant 11)."""
    return int((Decimal(unit_price_pence) * qty).quantize(Decimal(1), rounding=ROUND_HALF_UP))


# ------------------------------------------------------------------ menu ---


def list_menu_categories(session: Session) -> list[str]:
    """Categories that have at least one active item, in the rail's order.

    Uncategorised items are listed under `None` by `menu_items`; the picker shows them
    last so a missing category is visible rather than lost.
    """
    order = {row.name: (row.sort_order, row.name) for row in session.scalars(select(MenuCategory))}
    names = {
        (c or "").strip()
        for c in session.scalars(
            select(MenuItem.category).where(MenuItem.active.is_(True)).distinct()
        )
    }
    known = sorted((n for n in names if n), key=lambda n: order.get(n, (10_000, n)))
    return known


def _pick(row: MenuItem) -> MenuPickItem:
    return MenuPickItem(
        menu_item_id=row.id,
        name=row.name,
        size=size_label(row.size_code),
        category=(row.category or "").strip() or None,
        price_pence=row.price_pence,
    )


def menu_items(session: Session, *, category: str | None) -> list[MenuPickItem]:
    """Active items in one category (`None`: the uncategorised), name then size order."""
    stmt = select(MenuItem).where(MenuItem.active.is_(True))
    rows = [
        r
        for r in session.scalars(stmt)
        if ((r.category or "").strip() or None) == (category.strip() if category else None)
    ]
    return [_pick(r) for r in sorted(rows, key=_sort_key)]


def search_menu_items(session: Session, *, q: str, limit: int = 12) -> list[MenuPickItem]:
    """Active items whose name contains `q`. Prefix matches first, then the rest.

    A search is a shortlist to tap on, never an auto-pick: two items sharing a prefix
    (`Latte`, `Latte oat`) are both offered, and the person chooses.
    """
    wanted = q.strip().lower()
    if not wanted:
        return []
    rows = [
        r
        for r in session.scalars(select(MenuItem).where(MenuItem.active.is_(True)))
        if wanted in r.name.lower()
    ]
    rows.sort(key=lambda r: (not r.name.lower().startswith(wanted), *_sort_key(r)))
    return [_pick(r) for r in rows[:limit]]


_SIZE_RANK = {size: rank for rank, size in enumerate(SIZE_ORDER)}


def _sort_key(row: MenuItem) -> tuple[str, int]:
    return (row.name.lower(), _SIZE_RANK.get(row.size_code, 3) if row.size_code else 3)


# ------------------------------------------------------------- validate ---


@dataclass(frozen=True, slots=True)
class CheckedLine:
    """One line that passed every rule, priced: what `record_sale` will write."""

    menu_item_id: int
    qty: Decimal
    unit_price_pence: int
    gross_pence: int


@dataclass(frozen=True, slots=True)
class CheckedSale:
    recorded_by: str
    sold_at: datetime
    lines: tuple[CheckedLine, ...]

    @property
    def total_pence(self) -> int:
        return sum(line.gross_pence for line in self.lines)


def validate_sale_lines(session: Session, lines: Sequence[SaleLineIn]) -> tuple[CheckedLine, ...]:
    """Check and price every line, writing nothing. Raises `SaleRefused` on the first bad
    one. `record_sale` calls this before its first `add`, and the CSV import's dry run
    calls it too, so the preview refuses exactly what the real run would."""
    if not lines:
        raise SaleRefused("a sale needs at least one line")
    out: list[CheckedLine] = []
    for line in lines:
        item = session.get(MenuItem, line.menu_item_id)
        if item is None:
            raise SaleRefused(f"menu item {line.menu_item_id} does not exist")
        if not item.active:
            raise SaleRefused(f"{item.name} is not on the menu any more")
        qty = Decimal(line.qty)
        if not qty.is_finite() or qty <= 0:
            raise SaleRefused(f"{item.name}: quantity must be more than zero")
        price = item.price_pence if line.unit_price_pence is None else line.unit_price_pence
        if price < 0:
            raise SaleRefused(f"{item.name}: a price cannot be negative")
        out.append(
            CheckedLine(
                menu_item_id=item.id,
                qty=qty,
                unit_price_pence=price,
                gross_pence=line_gross_pence(price, qty),
            )
        )
    return tuple(out)


def validate_sale(
    session: Session,
    *,
    channel: SaleChannel,
    lines: Sequence[SaleLineIn],
    recorded_by: str,
    sold_at: datetime | None = None,
    source: SaleSource = SaleSource.MANUAL,
    now: datetime | None = None,
    max_backdate: timedelta | None = MAX_BACKDATE,
) -> CheckedSale:
    """Every rule `record_sale` enforces, writing nothing (see `record_sale` for them)."""
    now = now or utcnow()
    if channel not in MANUAL_SALE_CHANNELS:
        raise SaleRefused(
            f"channel {channel.value} cannot be typed by hand: the till is synced from "
            "Lightspeed and a typed till sale would be counted twice. Choose "
            + ", ".join(c.value for c in MANUAL_SALE_CHANNELS)
        )
    if source is SaleSource.POS_API:
        raise SaleRefused("source POS_API is reserved for the Lightspeed sync")
    if not lines:
        raise SaleRefused("a sale needs at least one line")
    who = require_actor(recorded_by, error=SaleRefused)
    when = sold_at or now
    if when.tzinfo is None:
        raise SaleRefused("sold_at must be timezone-aware")
    if when > now + CLOCK_SKEW_TOLERANCE:
        raise SaleRefused(
            f"sold_at {when.isoformat()} is in the future; a sale is recorded after it happens"
        )
    if max_backdate is not None and when < now - max_backdate:
        raise SaleRefused(
            f"sold_at {when.date().isoformat()} is more than {max_backdate.days} days ago; "
            "that month's figures were already read, so a backdated line needs the back office"
        )
    return CheckedSale(recorded_by=who, sold_at=when, lines=validate_sale_lines(session, lines))


# ----------------------------------------------------------------- write ---


def record_sale(
    session: Session,
    *,
    channel: SaleChannel,
    lines: Sequence[SaleLineIn],
    recorded_by: str,
    sold_at: datetime | None = None,
    note: str | None = None,
    source: SaleSource = SaleSource.MANUAL,
    now: datetime | None = None,
    receipt_id: str | None = None,
    max_backdate: timedelta | None = MAX_BACKDATE,
) -> RecordedSale:
    """Write one hand-typed receipt: one `sale` row per line, none expanded yet.

    `sold_at` defaults to now. A date-only entry (a delivery order typed the next
    morning) arrives as local noon of that day from the callers; the guard here is
    only that it is neither in the future past the till's clock tolerance nor
    absurdly old (`max_backdate=None` lifts the second guard: a CSV of last quarter's
    delivery orders is a deliberate backfill, not a typo).

    `receipt_id` lets an importer choose a deterministic id so that the same file
    imported twice writes nothing the second time; a typed sale gets a fresh one.
    """
    checked = validate_sale(
        session,
        channel=channel,
        lines=lines,
        recorded_by=recorded_by,
        sold_at=sold_at,
        source=source,
        now=now,
        max_backdate=max_backdate,
    )
    if receipt_id is None:
        receipt_id = f"{MANUAL_RECEIPT_PREFIX}{uuid.uuid4().hex[:12]}"
    elif session.scalar(select(Sale.id).where(Sale.lightspeed_receipt_id == receipt_id)):
        raise SaleRefused(f"receipt {receipt_id} is already recorded")
    clean_note = (note or "").strip()[:400] or None
    # Every check has passed before the first `add`: a refusal part-way through the
    # lines used to leave the earlier ones pending in the caller's transaction, and the
    # CSV importer's commit then wrote half of a receipt it reported as rejected.
    for index, line in enumerate(checked.lines, start=1):
        session.add(
            Sale(
                lightspeed_receipt_id=receipt_id,
                lightspeed_line_id=f"{receipt_id}:{index}",
                menu_item_id=line.menu_item_id,
                qty=line.qty,
                gross_pence=line.gross_pence,
                sold_at=checked.sold_at,
                channel=channel,
                source=source,
                recorded_by=checked.recorded_by,
                note=clean_note,
                applied_modifiers=[],
                voided=False,
                is_refund=False,
            )
        )
    session.flush()
    return recorded_sale(session, receipt_id)


def void_sale(session: Session, *, receipt_id: str, voided_by: str) -> RecordedSale:
    """Void a hand-typed receipt. A Lightspeed receipt is refused: correct it on the till.

    The rows stay. If expansion already depleted stock for them, the nightly
    `reverse_voided_expansions` writes the reversal (invariant 12: append-only), which
    is why a delete here would be wrong even though it looks tidier.
    """
    rows = list(session.scalars(select(Sale).where(Sale.lightspeed_receipt_id == receipt_id)))
    if not rows:
        raise LookupError(f"no receipt {receipt_id}")
    if any(r.source is SaleSource.POS_API for r in rows):
        raise SaleRefused(
            f"{receipt_id} came from the till. Void it in Lightspeed; the next sync brings "
            "the correction across and adjusts stock."
        )
    if any(r.source is SaleSource.LOYALTY for r in rows):
        raise SaleRefused(f"{receipt_id} is a loyalty redemption; undo it on the card instead")
    who = require_actor(voided_by, error=SaleRefused)
    for r in rows:
        if not r.voided:
            r.voided = True
            stamp = f"voided by {who[:80]} {utcnow():%Y-%m-%d %H:%M}"
            r.note = f"{r.note}; {stamp}" if r.note else stamp
    session.flush()
    return recorded_sale(session, receipt_id)


def recorded_sale(session: Session, receipt_id: str) -> RecordedSale:
    stmt = (
        select(Sale, MenuItem)
        .join(MenuItem, MenuItem.id == Sale.menu_item_id)
        .where(Sale.lightspeed_receipt_id == receipt_id)
        .order_by(Sale.id)
    )
    pairs = list(session.execute(stmt))
    if not pairs:
        raise LookupError(f"no receipt {receipt_id}")
    first = pairs[0][0]
    lines = tuple(
        RecordedLine(
            sale_id=sale.id,
            menu_item_id=item.id,
            name=item.name,
            size=size_label(item.size_code),
            qty=sale.qty,
            unit_price_pence=_unit_price(sale),
            gross_pence=sale.gross_pence,
        )
        for sale, item in pairs
    )
    return RecordedSale(
        receipt_id=receipt_id,
        channel=first.channel,
        source=first.source,
        sold_at=first.sold_at,
        recorded_by=first.recorded_by,
        note=first.note,
        voided=all(sale.voided for sale, _ in pairs),
        lines=lines,
    )


def _unit_price(sale: Sale) -> int:
    if sale.qty == 0:
        return 0
    return int((Decimal(sale.gross_pence) / sale.qty).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def noon_of(day: date) -> datetime:
    """Local noon of a calendar day, as an aware UTC instant.

    For a sale typed with a date but no time -- yesterday's Deliveroo orders entered
    this morning. Noon rather than midnight so the line sits inside the trading day it
    belongs to under any clock change, and inside the day's opening hours on the
    weekday-by-hour chart rather than at 00:00.
    """
    return datetime.combine(day, time(12, 0), tzinfo=settings.tz).astimezone(UTC)
