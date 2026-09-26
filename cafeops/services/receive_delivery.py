"""Receiving a delivery -- the real path by which stock enters the batch system.

`services/rebuild_batches.py` reconstructs batches by replaying a finished ledger.
That is a backfill tool (its own header says so). This is what happens at the door:
a delivery arrives, somebody reads the date off the carton, and one lot of stock
starts existing with its own expiry and its own unit cost.

Five decisions, each of which changes what the system can tell the truth about.

1. **The expiry is entered by a human, and an assumed one is flagged.** Shelf lives
   are seeded ESTIMATE defaults (`ARCHITECTURE.md` 8F.1), so a date derived from them
   is a guess about the single number that decides whether stock gets written off.
   When no date is given the guess is used anyway -- refusing would lose a real
   delivery, which is worse -- but the batch is stamped `EXPIRY ASSUMED` in its note
   and the receipt reports it. Invariant 8's treatment of an estimate, applied to a
   date instead of a price.

2. **Receiving requires a confirmed order.** Invariant 1 says nothing is ordered
   without a human. A `DRAFT` order that stock arrives against was never placed by
   anybody, so receiving one would launder an unauthorised purchase into the ledger.
   The walk-in case is real and has its own door: `receive_adhoc`.

3. **`received_qty` accumulates, `received_expires_at` keeps the SOONEST date.** Two
   pallets of the same line arriving on different days are two batches with two
   dates; the line can only hold one, and the short one is the one that caps how the
   stock may be used (spec 5.4).

4. **The unit cost comes from the order line, not from the ingredient cache.** The
   cache is today's price; the batch holds what this lot actually cost, which is what
   makes an expiry write-off a defensible money figure rather than a re-priced guess.

5. **The `DELIVERY` movement carries the batch id.** The ledger and the batch table
   have to agree about where stock came from, or the "unbatched stock" gap in
   `cafeops stock` starts reporting phantom deliveries.

## How `opened_at` gets set

Not here. A delivery arrives sealed, so `opened_at` stays NULL and the effective
expiry is the unopened one. It is set in two places:

- **Automatically, by the first draw.** `SqlBatchRepository.apply_allocations` stamps
  `opened_at` the first time FIFO takes stock from a lot of something that has an
  `open_life_days`. Drawing from a carton IS opening it. This is the only mechanism
  that will actually happen every time: it needs nobody to remember anything at
  06:30, and it is accurate for the overwhelmingly common case where the pack FIFO
  chose is the pack the barista reached for.
- **Explicitly, when reality differed.** `open_batch` below, reached from
  `cafeops open-batch`, for a carton opened for prep rather than a sale, or one
  opened out of FIFO order. It never moves an existing `opened_at`: the first opening
  starts the clock, and letting a later entry push it forward would extend the
  effective expiry of stock that is already ageing.

The alternative -- asking at the daily count -- was rejected. A count happens weekly
at best, and an open-life window is three to five days, so the date would arrive
after the stock it was meant to protect had already expired.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models import (
    ExpirySource,
    Ingredient,
    MovementType,
    POLine,
    POStatus,
    PurchaseOrder,
    SupplierProduct,
)
from cafeops.db.repositories.batch import SqlBatchRepository
from cafeops.domain.stock import batch_expiry_for
from cafeops.domain.types import MovementSpec, ReceiptWarning, ReceiptWarningKind, Unit
from cafeops.domain.units import convert

__all__ = [
    "ASSUMED_EXPIRY_NOTE",
    "DeliveryReceipt",
    "ReceiveRefused",
    "open_batch",
    "receive_adhoc",
    "receive_po_line",
]

#: `stock_movement.ref_type` for a delivery received against an order line.
PO_LINE_REF = "po_line"
#: ...and for one that arrived without an order at all (a Tesco run).
ADHOC_REF = "adhoc_delivery"

#: Prefix on `stock_batch.note` when the expiry was derived from an ESTIMATE shelf
#: life rather than read off the carton. A string because `stock_batch` has no
#: `expiry_source` column -- see the summary; that column is the better home and it
#: needs a model change this agent does not own.
ASSUMED_EXPIRY_NOTE = "EXPIRY ASSUMED"

#: Statuses a line may be received against. Invariant 1: a human confirmed all three.
#: `RECEIVED` is included so a second, partial delivery against a closed order still
#: lands in the ledger instead of being lost.
_RECEIVABLE: tuple[POStatus, ...] = (POStatus.CONFIRMED, POStatus.SENT, POStatus.RECEIVED)


class ReceiveRefused(ValueError):
    """The delivery was not recorded, and the message says what to fix.

    A refusal rather than a silent correction: stock that arrived is a physical fact,
    and the right answer to "this does not match any order anybody placed" is a person
    looking at it, not a batch invented to make the numbers balance.
    """


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    """What one received line did. Everything the bot needs to say it back."""

    po_line_id: int | None
    ingredient_id: int
    ingredient_name: str
    unit: Unit
    batch_id: int
    qty: Decimal
    received_at: datetime
    expires_at: datetime | None
    #: True when `expires_at` was derived from the ESTIMATE shelf life rather than
    #: read off the carton. The figure is still used; it is simply not trusted.
    expiry_was_assumed: bool
    unit_cost_pence: Decimal
    movements_written: int
    #: Set when this receipt completed every line on the order.
    order_completed: bool = False
    #: Every warning, each with the code for the condition that produced it. The bot used
    #: to recover these by SUBSTRING against the sentences below -- `"no expiry entered"
    #: in warning` -- so rewording one silently turned the Russian receipt into "N more
    #: notes were written to the log". An assumed expiry decides a write-off; it is not
    #: allowed to arrive as a count.
    coded_warnings: tuple[ReceiptWarning, ...] = ()

    @property
    def warnings(self) -> tuple[str, ...]:
        """The sentences, for the CLI and the log. Never matched -- read the codes."""
        return tuple(warning.text for warning in self.coded_warnings)

    @property
    def warning_kinds(self) -> tuple[ReceiptWarningKind, ...]:
        """The codes, deduplicated, in the order they were raised."""
        return tuple(dict.fromkeys(warning.kind for warning in self.coded_warnings))

    @property
    def value_pence(self) -> Decimal:
        return self.qty * self.unit_cost_pence

    @property
    def shelf_life_days_left(self) -> int | None:
        if self.expires_at is None:
            return None
        return (self.expires_at - self.received_at).days

    def summary(self) -> str:
        parts = [
            f"batch {self.batch_id}: {self.qty} {self.unit.value} of {self.ingredient_name}",
            f"GBP {self.value_pence / 100:.2f}",
        ]
        if self.expires_at is None:
            parts.append("no expiry (does not spoil)")
        else:
            parts.append(
                f"expires {self.expires_at:%Y-%m-%d}"
                f" ({self.shelf_life_days_left}d)" + (" ASSUMED" if self.expiry_was_assumed else "")
            )
        if self.order_completed:
            parts.append("order now RECEIVED")
        return "; ".join(parts)


def receive_po_line(
    session: Session,
    *,
    po_line_id: int,
    received_packs: int | None = None,
    received_qty: Decimal | None = None,
    expires_at: datetime | None = None,
    received_at: datetime | None = None,
    received_by: str,
    note: str | None = None,
) -> DeliveryReceipt:
    """Receive one order line: create the batch, write the movement, update the line.

    Give either `received_packs` (what the person at the door counts) or
    `received_qty` in the ingredient's stocking unit. Packs are converted through
    `domain.units.convert`, which RAISES across dimensions rather than assuming 1:1 --
    a 1 kg bag booked as 1 litre is exactly the class of error that conversion exists
    to make impossible.
    """
    if not received_by.strip():
        raise ReceiveRefused("received_by is required: a delivery is somebody's signature")

    line = session.get(POLine, po_line_id)
    if line is None:
        raise LookupError(f"po_line {po_line_id} not found")
    order = session.get(PurchaseOrder, line.po_id)
    if order is None:  # pragma: no cover - FK guarantees it
        raise LookupError(f"purchase_order {line.po_id} not found")

    if order.status not in _RECEIVABLE:
        raise ReceiveRefused(
            f"purchase order {order.id} is {order.status.value}; stock can only be received "
            "against an order a human confirmed (invariant 1). If this arrived without an "
            "order, record it with receive_adhoc so the ledger says so."
        )

    ingredient = session.get(Ingredient, line.ingredient_id)
    if ingredient is None:  # pragma: no cover - FK guarantees it
        raise LookupError(f"ingredient {line.ingredient_id} not found")
    product = session.get(SupplierProduct, line.supplier_product_id)
    if product is None:  # pragma: no cover - FK guarantees it
        raise LookupError(f"supplier_product {line.supplier_product_id} not found")

    received_at = _require_aware(received_at or datetime.now(UTC), "received_at")
    warnings: list[ReceiptWarning] = []

    qty = _resolve_qty(
        received_packs=received_packs,
        received_qty=received_qty,
        pack_size=product.pack_size,
        pack_unit=product.pack_unit,
        target_unit=ingredient.unit,
    )

    expected = _expected_qty(line, product, ingredient.unit)
    if expected is not None and qty > expected:
        warnings.append(
            ReceiptWarning(
                kind=ReceiptWarningKind.OVER_DELIVERY,
                text=(
                    f"received {qty} {ingredient.unit.value} against {expected} ordered: "
                    "over-delivery recorded as received, because the stock is on the shelf "
                    "either way"
                ),
            )
        )

    unit_cost = _unit_cost_pence(line, product, ingredient)

    expires_at, assumed, expiry_warnings = _resolve_expiry(
        session,
        ingredient_id=ingredient.id,
        received_at=received_at,
        expires_at=expires_at,
    )
    warnings.extend(expiry_warnings)

    receipt = _create_batch_and_movement(
        session,
        ingredient=ingredient,
        qty=qty,
        received_at=received_at,
        expires_at=expires_at,
        expiry_was_assumed=assumed,
        unit_cost_pence=unit_cost,
        po_line_id=line.id,
        ref_type=PO_LINE_REF,
        ref_id=line.id,
        note=_batch_note(assumed, note or f"received by {received_by}"),
        warnings=tuple(warnings),
        received_by=received_by,
    )

    line.received_qty = (line.received_qty or Decimal("0")) + qty
    # The soonest date wins. A line can hold one date and the short one is the one
    # that caps how this stock may be used.
    if expires_at is not None and (
        line.received_expires_at is None or expires_at < line.received_expires_at
    ):
        line.received_expires_at = expires_at

    completed = _close_order_if_complete(session, order)
    session.flush()
    return replace(receipt, order_completed=completed)


def receive_adhoc(
    session: Session,
    *,
    ingredient_id: int,
    qty: Decimal,
    expires_at: datetime | None = None,
    received_at: datetime | None = None,
    received_by: str,
    unit_cost_pence: Decimal | None = None,
    note: str | None = None,
) -> DeliveryReceipt:
    """Stock that arrived with no order behind it -- the walk to Tesco.

    `stock_batch.po_line_id` is nullable precisely for this. Without it, a panic buy
    would have to be entered as an `ADJUSTMENT`, which no batch would own, so it could
    never expire and never be counted as waste -- the exact hole the batch system
    exists to close.

    The unit cost falls back to the ingredient's cached cost when none is given, and
    the receipt warns, because a retail price paid in a hurry is usually higher than
    the cache and a write-off costed from the cache understates the loss.
    """
    if not received_by.strip():
        raise ReceiveRefused("received_by is required: a delivery is somebody's signature")
    ingredient = session.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise LookupError(f"ingredient {ingredient_id} not found")
    if isinstance(qty, float):
        raise TypeError("qty must be Decimal, not float (invariant 11)")
    if qty <= 0:
        raise ReceiveRefused(f"a delivery must be a positive quantity, got {qty}")

    received_at = _require_aware(received_at or datetime.now(UTC), "received_at")
    warnings: list[ReceiptWarning] = []

    if unit_cost_pence is None:
        unit_cost_pence = ingredient.current_cost_pence_per_unit or Decimal("0")
        warnings.append(
            ReceiptWarning(
                kind=ReceiptWarningKind.PRICE_FROM_CACHE,
                text=(
                    "no price given: costed from the ingredient's cached unit cost. A retail "
                    "emergency buy usually cost more than that, so any write-off against "
                    "this batch understates the loss."
                ),
            )
        )

    expires_at, assumed, expiry_warnings = _resolve_expiry(
        session, ingredient_id=ingredient.id, received_at=received_at, expires_at=expires_at
    )
    warnings.extend(expiry_warnings)

    return _create_batch_and_movement(
        session,
        ingredient=ingredient,
        qty=qty,
        received_at=received_at,
        expires_at=expires_at,
        expiry_was_assumed=assumed,
        unit_cost_pence=unit_cost_pence,
        po_line_id=None,
        ref_type=ADHOC_REF,
        ref_id=None,
        note=_batch_note(assumed, note or f"ad-hoc, received by {received_by}"),
        warnings=tuple(warnings),
        received_by=received_by,
    )


def open_batch(session: Session, *, batch_id: int, at: datetime | None = None) -> tuple[bool, str]:
    """Record that a pack was opened out of band. Returns (changed, sentence).

    See the module docstring for why the first FIFO draw normally does this instead.
    """
    at = _require_aware(at or datetime.now(UTC), "at")
    repo = SqlBatchRepository(session)
    before = repo.get(batch_id)
    if before is None:
        raise LookupError(f"stock_batch {batch_id} not found")

    open_life = repo.open_life_days(before.ingredient_id)
    if open_life is None:
        return (
            False,
            f"batch {batch_id}: opening does not shorten this ingredient's life "
            "(no open_life_days), so nothing is recorded",
        )
    if before.opened_at is not None:
        return (
            False,
            f"batch {batch_id} was already opened at {before.opened_at:%Y-%m-%d %H:%M}; "
            "the first opening starts the clock and is never moved forward",
        )
    repo.mark_opened(batch_id, at=at)

    # The effective expiry is recomputed from the spec rather than re-read, so the
    # sentence the user sees is produced by the same code FIFO and the sweep use.
    opened = replace(before, opened_at=at)
    expiry = opened.effective_expiry(open_life)
    if expiry is None:  # pragma: no cover - open_life is not None, so neither is this
        return True, f"batch {batch_id} opened at {at:%Y-%m-%d %H:%M}"

    was = before.expires_at
    moved = (
        ""
        if was is None or expiry >= was
        else f", pulled in from {was:%Y-%m-%d} -- {(was - expiry).days} days earlier"
    )
    return (
        True,
        f"batch {batch_id} opened at {at:%Y-%m-%d %H:%M}: {open_life}-day open life, "
        f"effective expiry now {expiry:%Y-%m-%d}{moved}",
    )


# ------------------------------------------------------------------ internals


def _create_batch_and_movement(
    session: Session,
    *,
    ingredient: Ingredient,
    qty: Decimal,
    received_at: datetime,
    expires_at: datetime | None,
    expiry_was_assumed: bool,
    unit_cost_pence: Decimal,
    po_line_id: int | None,
    ref_type: str,
    ref_id: int | None,
    note: str,
    warnings: tuple[ReceiptWarning, ...],
    received_by: str | None = None,
) -> DeliveryReceipt:
    repo = SqlBatchRepository(session)
    batch_id = repo.create_batch(
        ingredient.id,
        qty=qty,
        received_at=received_at,
        expires_at=expires_at,
        unit_cost_pence=unit_cost_pence,
        po_line_id=po_line_id,
        note=note,
        expiry_source=ExpirySource.ASSUMED if expiry_was_assumed else ExpirySource.ENTERED,
        received_by=received_by,
    )
    signed = received_by.strip()[:120] if received_by and received_by.strip() else None
    written = repo.append_linked_movements(
        [
            (
                MovementSpec(
                    ingredient_id=ingredient.id,
                    type=MovementType.DELIVERY,
                    qty=qty,
                    occurred_at=received_at,
                    ref_type=ref_type,
                    ref_id=ref_id,
                    note=note,
                    recorded_by=signed,
                ),
                batch_id,
            )
        ]
    )
    session.flush()
    return DeliveryReceipt(
        po_line_id=po_line_id,
        ingredient_id=ingredient.id,
        ingredient_name=ingredient.name,
        unit=ingredient.unit,
        batch_id=batch_id,
        qty=qty,
        received_at=received_at,
        expires_at=expires_at,
        expiry_was_assumed=expiry_was_assumed,
        unit_cost_pence=unit_cost_pence,
        movements_written=written,
        coded_warnings=warnings,
    )


def _resolve_expiry(
    session: Session,
    *,
    ingredient_id: int,
    received_at: datetime,
    expires_at: datetime | None,
) -> tuple[datetime | None, bool, list[ReceiptWarning]]:
    """The date on the carton, or an honest guess clearly labelled as one."""
    repo = SqlBatchRepository(session)
    shelf_life = repo.shelf_life(ingredient_id)
    warnings: list[ReceiptWarning] = []

    if expires_at is not None:
        expires_at = _require_aware(expires_at, "expires_at")
        if expires_at <= received_at:
            warnings.append(
                ReceiptWarning(
                    kind=ReceiptWarningKind.EXPIRY_NOT_AFTER_RECEIPT,
                    text=(
                        f"the date given ({expires_at:%Y-%m-%d}) is not after the delivery "
                        f"({received_at:%Y-%m-%d}): this stock arrived already expired and "
                        "the next sweep will write it off. Recorded as entered -- check the "
                        "carton."
                    ),
                )
            )
        if shelf_life is not None and shelf_life.shelf_life_days is None:
            warnings.append(
                ReceiptWarning(
                    kind=ReceiptWarningKind.NON_PERISHABLE_WITH_DATE,
                    text=(
                        "this ingredient is configured as non-perishable (shelf_life_days is "
                        "NULL) but a date was entered. The date is used -- a human reading a "
                        "carton beats a seeded default -- and the ingredient's shelf life is "
                        "worth correcting."
                    ),
                )
            )
        return expires_at, False, warnings

    derived = batch_expiry_for(received_at=received_at, shelf_life=shelf_life)
    if derived is None:
        return None, False, warnings

    days = shelf_life.shelf_life_days if shelf_life else None
    source = shelf_life.source.value if shelf_life and shelf_life.source else "unknown"
    warnings.append(
        ReceiptWarning(
            kind=ReceiptWarningKind.EXPIRY_ASSUMED,
            text=(
                f"no expiry entered: assumed {derived:%Y-%m-%d} from a {days}-day shelf life "
                f"flagged {source}. This date decides whether the stock is written off, so "
                "it is a guess worth replacing with what the carton says."
            ),
        )
    )
    return derived, True, warnings


def _batch_note(assumed: bool, note: str) -> str:
    return f"{ASSUMED_EXPIRY_NOTE}: {note}" if assumed else note


def _resolve_qty(
    *,
    received_packs: int | None,
    received_qty: Decimal | None,
    pack_size: Decimal,
    pack_unit: Unit,
    target_unit: Unit,
) -> Decimal:
    if (received_packs is None) == (received_qty is None):
        raise ReceiveRefused(
            "give exactly one of received_packs (what is counted at the door) or "
            "received_qty (in the ingredient's stocking unit)"
        )
    if received_qty is not None:
        if isinstance(received_qty, float):
            raise TypeError("received_qty must be Decimal, not float (invariant 11)")
        if received_qty <= 0:
            raise ReceiveRefused(f"a delivery must be a positive quantity, got {received_qty}")
        return received_qty

    assert received_packs is not None
    if received_packs <= 0:
        raise ReceiveRefused(f"a delivery must be at least one pack, got {received_packs}")
    return convert(Decimal(received_packs) * pack_size, pack_unit, target_unit)


def _expected_qty(line: POLine, product: SupplierProduct, target_unit: Unit) -> Decimal | None:
    if line.final_packs <= 0:
        return None
    return convert(Decimal(line.final_packs) * product.pack_size, product.pack_unit, target_unit)


def _unit_cost_pence(line: POLine, product: SupplierProduct, ingredient: Ingredient) -> Decimal:
    """What this lot cost per stocking unit, from the order line's own price.

    Falls back to the ingredient cache only when the line carries no price, which
    should not happen; a zero here would make every write-off against the batch look
    free, and invariant 8 forbids "unknown" wearing a zero.
    """
    per_pack = Decimal(line.unit_price_pence)
    size_in_stocking_unit = convert(product.pack_size, product.pack_unit, ingredient.unit)
    if per_pack > 0 and size_in_stocking_unit > 0:
        return per_pack / size_in_stocking_unit
    return ingredient.current_cost_pence_per_unit or Decimal("0")


def _close_order_if_complete(session: Session, order: PurchaseOrder) -> bool:
    """Move the order to RECEIVED once every line has its stock.

    The `ck_po_confirmed_requires_human` CHECK makes this unreachable without a
    recorded human (`ARCHITECTURE.md` 5), which is the point: the guard at the top of
    `receive_po_line` and the constraint say the same thing in two places.
    """
    if order.status is POStatus.RECEIVED:
        return False
    lines = list(session.scalars(select(POLine).where(POLine.po_id == order.id)))
    for line in lines:
        product = session.get(SupplierProduct, line.supplier_product_id)
        ingredient = session.get(Ingredient, line.ingredient_id)
        if product is None or ingredient is None:  # pragma: no cover
            return False
        expected = _expected_qty(line, product, ingredient.unit)
        if expected is None:
            continue
        if (line.received_qty or Decimal("0")) < expected:
            return False
    order.status = POStatus.RECEIVED
    return True


def _require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None:
        raise ReceiveRefused(f"{name} must be timezone-aware (spec 4: all timestamps UTC)")
    return value
