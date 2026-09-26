"""What the web may do to a purchase order that already exists. DECISIONS 1.

The web **never creates or confirms** a purchase order: `api/routers.py`'s docstring
stands, and confirmation happens in Telegram with a named human, enforced by
`ck_po_confirmed_requires_human` (invariant 1, ARCHITECTURE 5). What DECISIONS 1 does
allow is everything after or beside that decision:

* **Cancel** -- from DRAFT, PENDING_CONFIRM or CONFIRMED. Refused from SENT (the supplier
  already has it; cancelling here would not reach them) and RECEIVED (the stock is on the
  shelf). Signed and timed: `cancelled_requires_human` is a CHECK.
* **Mark sent** -- CONFIRMED only, through `SqlPurchaseOrderRepository.mark_sent`, the
  same guard the bot's dispatch path uses. Records `sent_by`.
* **Receive** -- line by line through `receive_delivery.receive_po_line`, which already
  refuses anything a human has not confirmed and closes the order when complete.
* **Log a shop run** -- stock bought at a supermarket because a delivery would come too
  late. Not a purchase order at all (that would be the web creating one): a batch per
  line through `receive_adhoc`, and a `tesco_routing` row per line carrying what was
  actually paid and the premium over the usual supplier, which is the figure the Shop
  runs report sums (spec 4.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models import (
    Ingredient,
    POLine,
    POStatus,
    PurchaseOrder,
    Supplier,
    SupplierProduct,
    TescoRouting,
)
from cafeops.db.repositories.purchase_order import SqlPurchaseOrderRepository
from cafeops.domain.units import IncompatibleUnitsError, convert
from cafeops.services.receive_delivery import DeliveryReceipt, receive_adhoc, receive_po_line

__all__ = [
    "CANCELLABLE",
    "OrderActionRefused",
    "ReceiveLine",
    "ShopRunLine",
    "ShopRunOutcome",
    "cancel_order",
    "log_shop_run",
    "mark_order_sent",
    "receive_order",
]

#: Statuses a web Cancel may move from. SENT and RECEIVED are past the point where
#: cancelling here would mean anything to the supplier.
CANCELLABLE: tuple[POStatus, ...] = (
    POStatus.DRAFT,
    POStatus.PENDING_CONFIRM,
    POStatus.CONFIRMED,
)

_REASON_MAX = 400


class OrderActionRefused(ValueError):
    """Nothing changed on the order, and the message says why. Shown verbatim."""


def _signed(name: str, what: str) -> str:
    clean = name.strip()
    if not clean:
        raise OrderActionRefused(f"{what} is required: this is somebody's decision")
    return clean[:120]


def _order(session: Session, po_id: int) -> PurchaseOrder:
    po = session.get(PurchaseOrder, po_id)
    if po is None:
        raise LookupError(f"purchase order {po_id} not found")
    return po


def cancel_order(
    session: Session,
    *,
    po_id: int,
    cancelled_by: str,
    reason: str | None = None,
    at: datetime | None = None,
) -> PurchaseOrder:
    who = _signed(cancelled_by, "cancelled_by")
    po = _order(session, po_id)
    if po.status not in CANCELLABLE:
        if po.status is POStatus.SENT:
            raise OrderActionRefused(
                f"order {po_id} was already sent to the supplier: cancelling it here would "
                "not reach them. Cancel with the supplier; when the goods do or do not "
                "arrive, receive what came."
            )
        raise OrderActionRefused(f"order {po_id} is {po.status.value.lower()}; nothing to cancel")
    po.status = POStatus.CANCELLED
    po.cancelled_by = who
    po.cancelled_at = at or datetime.now(UTC)
    clean = (reason or "").strip()
    po.cancel_reason = clean[:_REASON_MAX] or None
    session.flush()
    return po


def mark_order_sent(
    session: Session,
    *,
    po_id: int,
    sent_by: str,
    at: datetime | None = None,
) -> PurchaseOrder:
    who = _signed(sent_by, "sent_by")
    po = _order(session, po_id)
    if po.status is not POStatus.CONFIRMED:
        raise OrderActionRefused(
            f"order {po_id} is {po.status.value.lower()}: only a confirmed order "
            "can be marked sent (invariant 1)"
        )
    SqlPurchaseOrderRepository(session).mark_sent(po_id, at=at or datetime.now(UTC))
    po.sent_by = who
    session.flush()
    return po


@dataclass(frozen=True, slots=True)
class ReceiveLine:
    po_line_id: int
    received_packs: int | None = None
    received_qty: Decimal | None = None
    expires_at: datetime | None = None


def receive_order(
    session: Session,
    *,
    po_id: int,
    received_by: str,
    lines: list[ReceiveLine],
    at: datetime | None = None,
) -> tuple[PurchaseOrder, list[DeliveryReceipt]]:
    """Receive some or all lines of one order. All in one transaction, or none."""
    who = _signed(received_by, "received_by")
    po = _order(session, po_id)
    if not lines:
        raise OrderActionRefused("nothing to receive: give at least one line")
    own = {line.id for line in session.scalars(select(POLine).where(POLine.po_id == po_id))}
    seen: set[int] = set()
    receipts: list[DeliveryReceipt] = []
    for line in lines:
        if line.po_line_id not in own:
            raise OrderActionRefused(
                f"line {line.po_line_id} is not on order {po_id}; nothing was received"
            )
        if line.po_line_id in seen:
            raise OrderActionRefused(f"line {line.po_line_id} is listed twice")
        seen.add(line.po_line_id)
        receipts.append(
            receive_po_line(
                session,
                po_line_id=line.po_line_id,
                received_packs=line.received_packs,
                received_qty=line.received_qty,
                expires_at=line.expires_at,
                received_at=at,
                received_by=who,
            )
        )
    session.refresh(po)
    return po, receipts


@dataclass(frozen=True, slots=True)
class ShopRunLine:
    ingredient_id: int
    qty: Decimal
    #: What was actually paid for this line, whole pence. None = not recorded, never 0.
    paid_pence: int | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ShopRunOutcome:
    receipts: tuple[DeliveryReceipt, ...]
    routing_ids: tuple[int, ...]
    #: Σ paid over the lines that recorded one, or None if any line did not.
    paid_pence: int | None
    #: Σ premium over the usual supplier, or None if any line could not price one.
    premium_pence: int | None


def _preferred_unit_pence(session: Session, ingredient: Ingredient) -> tuple[Decimal, int] | None:
    """The usual supplier's price per stocking unit, and who that supplier is."""
    rows = list(
        session.execute(
            select(SupplierProduct, Supplier)
            .join(Supplier, Supplier.id == SupplierProduct.supplier_id)
            .where(
                SupplierProduct.ingredient_id == ingredient.id,
                SupplierProduct.archived_at.is_(None),
                Supplier.archived_at.is_(None),
            )
        ).all()
    )
    rows.sort(key=lambda r: (not r[0].is_preferred, r[0].price_pence, r[0].id))
    for product, supplier in rows:
        try:
            size = convert(product.pack_size, product.pack_unit, ingredient.unit)
        except IncompatibleUnitsError:
            continue
        if size > 0:
            return Decimal(product.price_pence) / size, supplier.id
    return None


def log_shop_run(
    session: Session,
    *,
    bought_by: str,
    where: str,
    lines: list[ShopRunLine],
    reason: str | None = None,
    at: datetime | None = None,
) -> ShopRunOutcome:
    """Stock bought at a shop because a delivery would come too late. Not an order."""
    who = _signed(bought_by, "bought_by")
    place = where.strip()
    if not place:
        raise OrderActionRefused("say where it was bought: the shop is what the report groups by")
    if not lines:
        raise OrderActionRefused("nothing bought: give at least one line")
    at = at or datetime.now(UTC)
    receipts: list[DeliveryReceipt] = []
    routing_ids: list[int] = []
    paid_total: int | None = 0
    premium_total: int | None = 0
    for line in lines:
        if line.paid_pence is not None and line.paid_pence <= 0:
            raise OrderActionRefused(
                "a price paid must be more than £0. Leave it blank if nobody kept the receipt."
            )
        ingredient = session.get(Ingredient, line.ingredient_id)
        if ingredient is None:
            raise LookupError(f"ingredient {line.ingredient_id} not found")
        if line.qty <= 0:
            raise OrderActionRefused(f"{ingredient.name}: a quantity bought must be positive")
        unit_cost = None if line.paid_pence is None else Decimal(line.paid_pence) / line.qty
        receipt = receive_adhoc(
            session,
            ingredient_id=line.ingredient_id,
            qty=line.qty,
            expires_at=line.expires_at,
            received_at=at,
            received_by=who,
            unit_cost_pence=unit_cost,
            note=f"shop run at {place}, bought by {who}",
        )
        receipts.append(receipt)

        usual = _preferred_unit_pence(session, ingredient)
        premium: int | None = None
        retail_unit: int | None = None
        if line.paid_pence is not None:
            retail_unit = _whole(Decimal(line.paid_pence) / line.qty)
            if usual is not None:
                raw = Decimal(line.paid_pence) - usual[0] * line.qty
                # Floored at zero, as `emergency_premium_pence` does: a shop that was
                # cheaper is a sourcing finding, not a saving to net off the others.
                premium = max(_whole(raw), 0)
        row = TescoRouting(
            ingredient_id=line.ingredient_id,
            occurred_at=at,
            reason=(reason or f"bought at {place}: could not wait for a delivery")[:400],
            retail_unit_price_pence=retail_unit,
            preferred_unit_price_pence=None if usual is None else _whole(usual[0]),
            premium_pence=premium,
            would_be_supplier_id=None if usual is None else usual[1],
            retailer=place[:80],
            bought_by=who,
            paid_pence=line.paid_pence,
        )
        session.add(row)
        session.flush()
        routing_ids.append(row.id)
        if paid_total is not None:
            paid_total = None if line.paid_pence is None else paid_total + line.paid_pence
        if premium_total is not None:
            premium_total = None if premium is None else premium_total + premium
    return ShopRunOutcome(
        receipts=tuple(receipts),
        routing_ids=tuple(routing_ids),
        paid_pence=paid_total,
        premium_pence=premium_total,
    )


def _whole(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))
