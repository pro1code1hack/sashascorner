"""Purchase order reads and writes.

INVARIANT 1, twice over: `create_draft` writes `DRAFT` and nothing else, and
`confirm` refuses without a named human. The schema backs both up -- moving a row to
`CONFIRMED`, `SENT` or `RECEIVED` without `confirmed_by` and `confirmed_at` violates
`ck_po_confirmed_requires_human` and SQLite rejects the write (`ARCHITECTURE.md` 5).
The guards here exist to fail with a sentence a human can read instead of an
`IntegrityError`, not to be the only thing standing in the way.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models import Ingredient, POLine, POStatus, PurchaseOrder, SupplierProduct
from cafeops.domain.types import OrderNoteKind, OrderSuggestion
from cafeops.domain.units import convert

#: "Open" per the repository protocol: anything not yet received and not cancelled.
#: DRAFT is included, so a draft built this morning is not double-ordered this
#: afternoon -- and so running `simulate --commit` twice does not order twice.
OPEN_PO_STATUSES: tuple[POStatus, ...] = (
    POStatus.DRAFT,
    POStatus.PENDING_CONFIRM,
    POStatus.CONFIRMED,
    POStatus.SENT,
)

#: Statuses a draft can still have a line added to. Anything past confirmation is an
#: order a human approved as it stood, and adding to it would be ordering without
#: confirmation (invariant 1).
_OPEN_TO_EDITS: tuple[POStatus, ...] = (POStatus.DRAFT, POStatus.PENDING_CONFIRM)

_CLOSED_TO_CONFIRMATION: tuple[POStatus, ...] = (
    POStatus.CONFIRMED,
    POStatus.SENT,
    POStatus.RECEIVED,
    POStatus.CANCELLED,
)


class SqlPurchaseOrderRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def open_qty_for(self, ingredient_id: int) -> Decimal:
        """Quantity on POs that are neither RECEIVED nor CANCELLED.

        Returned in the **ingredient's** stocking unit: packs are counted in pack
        units, and `units.convert` raises rather than adding litres to kilograms.
        """
        ingredient = self.session.get(Ingredient, ingredient_id)
        if ingredient is None:
            raise LookupError(f"ingredient {ingredient_id} not found")

        rows = self.session.execute(
            select(POLine.final_packs, SupplierProduct.pack_size, SupplierProduct.pack_unit)
            .join(PurchaseOrder, PurchaseOrder.id == POLine.po_id)
            .join(SupplierProduct, SupplierProduct.id == POLine.supplier_product_id)
            .where(
                POLine.ingredient_id == ingredient_id,
                PurchaseOrder.status.in_(list(OPEN_PO_STATUSES)),
            )
        ).all()

        total = Decimal("0")
        for packs, pack_size, pack_unit in rows:
            total += Decimal(packs) * convert(pack_size, pack_unit, ingredient.unit)
        return total

    def create_draft(
        self,
        suggestion: object,
        *,
        routing_reason: str | None = None,
        delivery_fee_pence: int = 0,
    ) -> int:
        """Write an `OrderSuggestion` as a DRAFT purchase order. Never further.

        `suggested_packs` and `final_packs` start equal; the human moves `final_packs`
        with the +/- buttons in Telegram, which is why the suggestion survives
        alongside the decision instead of being overwritten by it.

        `routing_reason` and `delivery_fee_pence` are keyword arguments rather than fields
        on `OrderSuggestion`, which is integrator-owned and has neither. They are what
        spec 4.4 asks a Tesco order to carry, and passing them here keeps the protocol
        signature satisfied while the contract catches up.

        **`cap_reason` travels to the line** (spec 5.4). Without it the database holds a
        quantity smaller than the forecast with no record of why, and the next person to
        look at it -- or the bot rendering it -- puts it back up.
        """
        if not isinstance(suggestion, OrderSuggestion):
            raise TypeError(f"expected an OrderSuggestion, got {type(suggestion)!r}")
        if not suggestion.lines:
            raise ValueError(
                "refusing to create a purchase order with no lines; "
                "'nothing is needed' is not an order"
            )

        po = PurchaseOrder(
            supplier_id=suggestion.supplier.id,
            status=POStatus.DRAFT,
            target_delivery_date=suggestion.target_delivery_date,
            total_pence=suggestion.total_pence,
            min_order_topped_up=suggestion.min_order_topped_up,
            notes="\n".join(suggestion.notes) or None,
            note_codes=_note_codes(suggestion),
            confidence_notes=_confidence_notes(suggestion),
            routing_reason=routing_reason,
            delivery_fee_pence=delivery_fee_pence,
        )
        self.session.add(po)
        self.session.flush()

        for line in suggestion.lines:
            self.session.add(
                POLine(
                    po_id=po.id,
                    ingredient_id=line.ingredient_id,
                    supplier_product_id=line.pack.supplier_product_id,
                    suggested_packs=line.packs,
                    final_packs=line.packs,
                    unit_price_pence=line.pack.price_pence,
                    need_qty=line.need_qty,
                    is_top_up=line.is_top_up,
                    cap_reason=_capped(line.cap_reason),
                    # The structured companions. The sentence is for a human reading a
                    # log; these are what a non-English surface branches on, so a
                    # reword upstream cannot silently change behaviour (invariants 4, 9).
                    cap_kind=line.cap_kind,
                    cover_days=line.cover_days,
                    low_confidence=line.low_confidence,
                    low_confidence_kind=line.low_confidence_kind,
                    confidence_reason=(
                        " | ".join(line.confidence_reasons) or None
                        if line.confidence_reasons
                        else None
                    ),
                    # The two figures the invariant 9 sentence is made of. Read off the
                    # note that BLOCKS the number, not off any note, so they always
                    # belong to the reason actually shown.
                    forecast_history_days=(
                        None if line.blocking_note is None else line.blocking_note.history_days
                    ),
                    forecast_needed_days=(
                        None if line.blocking_note is None else line.blocking_note.needed_days
                    ),
                    checklist_requested_by=line.checklist_requested_by,
                )
            )
        self.session.flush()
        return po.id

    def add_checklist_line(
        self,
        *,
        ingredient_id: int,
        supplier_id: int,
        supplier_product_id: int,
        packs: int,
        unit_price_pence: int,
        requested_by: str,
        target_delivery_date: date,
    ) -> tuple[int, int, bool, date]:
        """Put a tier C checklist request on a DRAFT order.

        Returns `(po_id, line_id, order_was_created, the order's delivery date)`. The date
        is returned rather than assumed by the caller: when the line joins an existing
        draft it arrives on THAT order's date, and telling her the freshly computed one
        would be a promise the order does not make.

        Spec 4.7 says tier C is never calculated, which is why this exists as its own
        path: there is no forecast, no par level and no cover window to size against, so
        the quantity is `packs` -- a number a person typed, recorded with their name on
        `po_line.checklist_requested_by` so no surface can show it as a calculation.

        It JOINS an existing draft for the supplier when there is one, rather than opening
        a second order she then has to confirm twice. Otherwise a DRAFT is created --
        `POStatus.DRAFT` and nothing further, like every other path here (invariant 1).

        A repeat request for the same ingredient on the same draft ADDS packs to the
        existing checklist line rather than creating a duplicate. Answering "running low"
        twice in a week is not two separate purchases, and two lines for one ingredient is
        how an order quietly doubles.
        """
        if packs <= 0:
            raise ValueError(
                f"a checklist request must be at least one pack, got {packs}; "
                "'do not order' is expressed by not asking, not by a zero line"
            )
        if not requested_by.strip():
            raise ValueError(
                "requested_by is required: tier C carries no forecast, so the quantity on "
                "this line is somebody's decision and the line has to say whose"
            )

        # A draft whose delivery date has already passed is not going to carry a new
        # request -- joining it would put the item on an order that was never sent and
        # report a date in the past. `target_delivery_date` is the earliest slot the
        # supplier can actually make, so it is also the earliest draft worth joining.
        po = self.session.scalars(
            select(PurchaseOrder)
            .where(
                PurchaseOrder.supplier_id == supplier_id,
                PurchaseOrder.status.in_(list(_OPEN_TO_EDITS)),
                PurchaseOrder.target_delivery_date >= target_delivery_date,
            )
            .order_by(PurchaseOrder.target_delivery_date, PurchaseOrder.id)
        ).first()
        created = po is None
        if po is None:
            po = PurchaseOrder(
                supplier_id=supplier_id,
                status=POStatus.DRAFT,
                target_delivery_date=target_delivery_date,
                total_pence=0,
                notes=None,
                note_codes=OrderNoteKind.CHECKLIST_REQUEST.value,
            )
            self.session.add(po)
            self.session.flush()
        else:
            codes = [c for c in (po.note_codes or "").split(",") if c]
            if OrderNoteKind.CHECKLIST_REQUEST.value not in codes:
                codes.append(OrderNoteKind.CHECKLIST_REQUEST.value)
                po.note_codes = ",".join(codes)

        existing = self.session.scalars(
            select(POLine).where(
                POLine.po_id == po.id,
                POLine.ingredient_id == ingredient_id,
                POLine.checklist_requested_by.is_not(None),
            )
        ).first()
        if existing is not None:
            existing.suggested_packs += packs
            existing.final_packs += packs
            existing.checklist_requested_by = requested_by
            line = existing
        else:
            line = POLine(
                po_id=po.id,
                ingredient_id=ingredient_id,
                supplier_product_id=supplier_product_id,
                suggested_packs=packs,
                final_packs=packs,
                unit_price_pence=unit_price_pence,
                # NULL, not zero. There is no computed need behind a checklist answer, and
                # a zero here would read as "the forecast asked for nothing" (invariant 8's
                # rule about unknowns wearing a zero, applied to a quantity).
                need_qty=None,
                is_top_up=False,
                checklist_requested_by=requested_by,
            )
            self.session.add(line)
        self.session.flush()
        po.total_pence = sum(row.final_packs * row.unit_price_pence for row in po.lines)
        self.session.flush()
        return po.id, line.id, created, po.target_delivery_date

    def get_lines(self, po_id: int) -> Sequence[POLine]:
        return list(
            self.session.scalars(select(POLine).where(POLine.po_id == po_id).order_by(POLine.id))
        )

    def confirm(
        self, po_id: int, *, confirmed_by: str, at: datetime, final_packs: Mapping[int, int]
    ) -> None:
        """Record a human's decision. INVARIANT 1: without a name, nothing moves.

        `final_packs` maps `po_line.id` to the count the human settled on. A line left
        out keeps its suggestion; a line set to zero stays on the order at zero rather
        than disappearing, so the order still shows what was proposed and declined.
        """
        if not confirmed_by.strip():
            raise ValueError(
                "INVARIANT 1: a purchase order cannot be confirmed without a named "
                "human. Nothing is ordered without human confirmation, ever, in v1."
            )
        po = self.session.get(PurchaseOrder, po_id)
        if po is None:
            raise LookupError(f"purchase order {po_id} not found")
        if po.status in _CLOSED_TO_CONFIRMATION:
            raise ValueError(f"purchase order {po_id} is already {po.status.value}")

        lines = self.get_lines(po_id)
        known = {line.id for line in lines}
        unknown = sorted(set(final_packs) - known)
        if unknown:
            raise LookupError(f"po_line id(s) {unknown} do not belong to purchase order {po_id}")
        for line in lines:
            packs = final_packs.get(line.id, line.final_packs)
            if packs < 0:
                raise ValueError(f"po_line {line.id}: final_packs {packs} is negative")
            line.final_packs = packs

        po.total_pence = sum(line.final_packs * line.unit_price_pence for line in lines)
        po.confirmed_by = confirmed_by
        po.confirmed_at = at
        po.status = POStatus.CONFIRMED

    def mark_sent(self, po_id: int, *, at: datetime) -> None:
        """Only a CONFIRMED order can be sent, and only with a `sent_at`."""
        po = self.session.get(PurchaseOrder, po_id)
        if po is None:
            raise LookupError(f"purchase order {po_id} not found")
        if po.status is not POStatus.CONFIRMED:
            raise ValueError(
                f"purchase order {po_id} is {po.status.value}; only a CONFIRMED order "
                "can be sent (invariant 1)"
            )
        po.sent_at = at
        po.status = POStatus.SENT

    def list_for_supplier(
        self, supplier_id: int, *, statuses: Sequence[POStatus] | None = None
    ) -> list[PurchaseOrder]:
        stmt = select(PurchaseOrder).where(PurchaseOrder.supplier_id == supplier_id)
        if statuses:
            stmt = stmt.where(PurchaseOrder.status.in_(list(statuses)))
        return list(self.session.scalars(stmt.order_by(PurchaseOrder.id)))


#: `po_line.cap_reason` is String(80). The phrase is written to fit, so a longer one is
#: a bug in the phrase rather than in the column -- but it is truncated visibly rather
#: than raising, because losing an order over the length of an explanation would be the
#: worse failure.
_CAP_REASON_MAX = 80


def _capped(reason: str | None) -> str | None:
    if reason is None:
        return None
    if len(reason) <= _CAP_REASON_MAX:
        return reason
    return reason[: _CAP_REASON_MAX - 4].rstrip() + " ..."


#: `purchase_order.note_codes` is String(600). Enum names are short and the list is
#: deduplicated, so overflowing it means a genuinely unusual order rather than a long
#: sentence -- but it is truncated rather than raised, for the same reason `cap_reason` is.
_NOTE_CODES_MAX = 600


def _note_codes(suggestion: OrderSuggestion) -> str | None:
    """The order's decisions as a comma-separated list of `OrderNoteKind` names.

    Deduplicated and in first-seen order (`OrderSuggestion.note_kinds`), because the bot
    renders one sentence per KIND: three capped lines produce one "deliberately smaller"
    paragraph naming all three, not three paragraphs.
    """
    codes = [kind.value for kind in suggestion.note_kinds]
    if not codes:
        return None
    joined = ",".join(codes)
    while len(joined) > _NOTE_CODES_MAX and codes:
        codes.pop()
        joined = ",".join(codes)
    return joined or None


def _confidence_notes(suggestion: OrderSuggestion) -> str | None:
    """INVARIANT 7 carried as far as the Telegram message.

    A low-confidence line's *reason* is stored so the bot can print it instead of the
    forecast figure. Without this the flag would arrive without the sentence, and a
    flag on its own is not "saying so".
    """
    lines = [line for line in suggestion.lines if line.low_confidence]
    if not lines:
        return None
    parts = [
        f"{line.ingredient_name}: {'; '.join(line.confidence_reasons) or 'low confidence'}"
        for line in lines
    ]
    header = (
        f"{len(lines)} of {len(suggestion.lines)} line(s) rest on a low-confidence "
        "forecast. Invariant 7: show these reasons in place of the forecast figure, "
        "not beside it."
    )
    return "\n".join([header, *parts])
