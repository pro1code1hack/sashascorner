"""Read the database, hand back `viewmodels`. **Synchronous, and no Russian.**

Every function here takes a `Session` and runs on a worker thread
(`deps.run_sync`). Repositories stay sync -- SQLite has one writer and an async
repository would buy nothing at forty transactions a day (spec 3).

Two responsibilities, and they are the reason this module exists rather than the
handlers querying directly:

1. **Everything goes through the services layer** (spec 8). `record_count`,
   `receive_po_line`, `record_checklist_answer`, `build_split`, `sweep_expiry` and the
   purchase-order repository's `confirm` are the writers. Nothing here invents a
   `stock_movement` or advances a `purchase_order` on its own.

2. **English prose becomes an enum.** `po_line.cap_reason` and
   `purchase_order.confidence_notes` are sentences written for a back-office reader.
   A Russian bot cannot show them, and a formatter guessing at prose would be worse
   than a formatter given a code. `_classify_cap` and `_classify_confidence` do that
   translation-by-classification here, once, with the patterns documented next to the
   code that produces them. See `viewmodels` for why.

The parsers are the weak seam and they are deliberately visible. `po_line` has no
`low_confidence` column and no structured cap kind, so the facts have to be recovered
from text this codebase itself wrote. Both are integrator-owned changes worth making;
until then, a parse that fails degrades to `OTHER` and the owner still gets a true
sentence, never an English one and never silence.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.bot.viewmodels import (
    CapKind,
    CapNotice,
    ChecklistItemView,
    CountItemView,
    CountResultView,
    CountSessionKind,
    DeliveryLineView,
    DeliveryOrderView,
    DigestView,
    DispatchView,
    DriftAlertView,
    ExpiryLineView,
    IngredientRefView,
    LowConfidenceKind,
    LowConfidenceNotice,
    OrderLineView,
    OrderView,
    ReceiptIssue,
    ReceiptView,
    StockLineView,
    WriteOffView,
)
from cafeops.config import settings
from cafeops.db.models import (
    Ingredient,
    POLine,
    POStatus,
    PurchaseOrder,
    SupplierProduct,
)
from cafeops.db.repositories.batch import SqlBatchRepository
from cafeops.db.repositories.ingredient import SqlIngredientRepository
from cafeops.db.repositories.par import SqlParLevelRepository
from cafeops.db.repositories.purchase_order import SqlPurchaseOrderRepository
from cafeops.db.repositories.sourcing import SqlSourcingRepository
from cafeops.domain.drift import DriftCause
from cafeops.domain.tiers import GateAction
from cafeops.domain.types import (
    ChecklistStatus,
    OrderChannel,
    PriceSource,
    Storage,
    Tier,
    Unit,
)
from cafeops.domain.units import convert
from cafeops.integrations.suppliers import OrderItem, adapter_for
from cafeops.jobs.expiry_sweep import DEFAULT_SHORT_DATED_DAYS, sweep_expiry
from cafeops.services.read_stock import StockReading, read_on_hand
from cafeops.services.receive_delivery import (
    DeliveryReceipt,
    receive_adhoc,
    receive_po_line,
)
from cafeops.services.record_checklist import checklist_roster, record_checklist_answer
from cafeops.services.record_count import explain_drift_history, gate_status, record_count

__all__ = [
    "COUNT_OVERDUE_DAYS",
    "OrderNotAdjustable",
    "adjust_packs",
    "build_checklist",
    "build_count_session",
    "build_delivery_orders",
    "build_digest",
    "build_order_view",
    "confirm_order",
    "dispatch_order",
    "list_draft_orders",
    "lookup_ingredient",
    "receive_adhoc_delivery",
    "receive_line",
    "submit_checklist",
    "submit_count",
]

#: How far ahead the digest looks for deliveries. Today and tomorrow: the digest's job
#: here is "be ready to receive this", and a delivery six days out is not actionable at
#: 07:30 -- it would be noise in the one message that has to stay worth reading.
DIGEST_DELIVERY_HORIZON_DAYS = 1

#: A tier A ingredient uncounted for this long is due. The weekly full count and the
#: twice-weekly express count both aim at keeping this at zero.
COUNT_OVERDUE_DAYS = 7

#: Statuses a DRAFT can still be edited and confirmed from.
_CONFIRMABLE: tuple[POStatus, ...] = (POStatus.DRAFT, POStatus.PENDING_CONFIRM)

#: Statuses where stock is expected through the door.
_AWAITING_DELIVERY: tuple[POStatus, ...] = (POStatus.CONFIRMED, POStatus.SENT)


class OrderNotAdjustable(ValueError):
    """The order has moved past the point where +/- means anything.

    Raised rather than silently ignored: a confirmed order whose packs quietly changed
    under a stale keyboard is an order nobody approved (invariant 1).
    """


# ==========================================================================
# Prose -> enum. See the module docstring.
# ==========================================================================

#: `domain/ordering.OrderCandidate.cap_reason` and `_top_up` produce exactly these five
#: shapes. Matched here rather than translated, because the *kind* is what the sentence
#: needs and the names are read from the database instead of out of the string.
_CAP_CAPPED = re.compile(
    r"^capped at (\d+) days?(?: -- (?P<subject>.+?)(?P<tail> shelf life| ends))?"
)
_CAP_OUT_OF_SEASON = re.compile(r"^not ordered -- (?P<season>.+?) is out of season")
_CAP_TOP_UP_MINIMUM = "top-up to reach the minimum order"
_CAP_TOP_UP_FREE_DELIVERY = "top-up to reach the free-delivery threshold"


def _classify_cap(raw: str | None, *, is_top_up: bool) -> CapNotice | None:
    if raw is None:
        return CapNotice(kind=CapKind.TOP_UP_MINIMUM) if is_top_up else None
    text = raw.strip()
    if text.startswith(_CAP_TOP_UP_FREE_DELIVERY):
        return CapNotice(kind=CapKind.TOP_UP_FREE_DELIVERY)
    if text.startswith(_CAP_TOP_UP_MINIMUM):
        return CapNotice(kind=CapKind.TOP_UP_MINIMUM)
    out = _CAP_OUT_OF_SEASON.match(text)
    if out:
        return CapNotice(kind=CapKind.OUT_OF_SEASON, subject=out.group("season"))
    capped = _CAP_CAPPED.match(text)
    if capped:
        days = int(capped.group(1))
        subject = capped.group("subject")
        tail = capped.group("tail")
        if tail == " shelf life":
            return CapNotice(kind=CapKind.SHELF_LIFE, days=days, subject=subject)
        if tail == " ends":
            return CapNotice(kind=CapKind.SEASON_END, days=days, subject=subject)
        return CapNotice(kind=CapKind.OTHER, days=days)
    return CapNotice(kind=CapKind.OTHER)


#: `domain/forecast` sets `low_confidence=True` in exactly four places. Each writes a
#: sentence starting with one of these.
_LC_NO_HISTORY = re.compile(r"^no consumption recorded in the (\d+) day")
_LC_SHORT_HISTORY = re.compile(r"^only (\d+) day\(s\) of history to \S+, (\d+) needed")
_LC_FIRST_SEASON = "has no previous occurrence in the history"


def _classify_confidence(reason: str) -> LowConfidenceNotice:
    text = reason.strip()
    none_yet = _LC_NO_HISTORY.match(text)
    if none_yet:
        return LowConfidenceNotice(
            kind=LowConfidenceKind.NO_HISTORY, needed_days=int(none_yet.group(1))
        )
    short = _LC_SHORT_HISTORY.match(text)
    if short:
        return LowConfidenceNotice(
            kind=LowConfidenceKind.SHORT_HISTORY,
            history_days=int(short.group(1)),
            needed_days=int(short.group(2)),
        )
    if _LC_FIRST_SEASON in text:
        return LowConfidenceNotice(kind=LowConfidenceKind.FIRST_SEASON)
    return LowConfidenceNotice(kind=LowConfidenceKind.OTHER)


def _confidence_by_name(notes: str | None, names: Sequence[str]) -> dict[str, LowConfidenceNotice]:
    """Split `purchase_order.confidence_notes` back into per-ingredient notices.

    `SqlPurchaseOrderRepository._confidence_notes` writes a header line and then one
    `"{ingredient_name}: {reasons}"` line per low-confidence line. The names are matched
    against the order's own lines rather than split on the first colon, because
    ingredient names contain punctuation (`Coffee beans (house blend)`) and a blind
    split would invent an ingredient called `Invariant 7`.
    """
    if not notes:
        return {}
    found: dict[str, LowConfidenceNotice] = {}
    for row in notes.splitlines():
        for name in names:
            prefix = f"{name}: "
            if row.startswith(prefix):
                found[name] = _classify_confidence(row[len(prefix) :])
                break
    return found


# ==========================================================================
# Stock, and invariant 6
# ==========================================================================


def _stock_line(reading: StockReading, *, as_of: datetime) -> StockLineView:
    on_hand = reading.on_hand
    counted_at = on_hand.basis_counted_at
    return StockLineView(
        ingredient_id=reading.ingredient.id,
        name=reading.ingredient.name,
        unit=reading.ingredient.unit,
        tier=reading.ingredient.tier,
        qty=on_hand.qty,
        is_theoretical=on_hand.is_theoretical,
        has_count_basis=on_hand.has_count_basis,
        basis_count_qty=on_hand.basis_count_qty,
        basis_counted_at=counted_at,
        movement_count=on_hand.movement_count,
        days_since_count=None if counted_at is None else (as_of - counted_at).days,
        soonest_expiry_days=reading.soonest_expiry_days,
        batch_qty=reading.batch_qty,
        unbatched_qty=reading.batch_coverage_gap,
    )


def _readings(
    session: Session, *, as_of: datetime, tiers: tuple[Tier, ...] | None
) -> list[StockReading]:
    return read_on_hand(session, as_of=as_of, tiers=tiers)


# ==========================================================================
# Orders
# ==========================================================================


def _order_view(session: Session, order: PurchaseOrder) -> OrderView:
    repo = SqlPurchaseOrderRepository(session)
    terms = SqlSourcingRepository(session).terms(order.supplier_id)
    lines = repo.get_lines(order.id)

    products = {
        row.id: row
        for row in session.scalars(
            select(SupplierProduct).where(
                SupplierProduct.id.in_([line.supplier_product_id for line in lines] or [0])
            )
        )
    }
    ingredients = {
        row.id: row
        for row in session.scalars(
            select(Ingredient).where(
                Ingredient.id.in_([line.ingredient_id for line in lines] or [0])
            )
        )
    }
    names = [
        ingredients[line.ingredient_id].name for line in lines if line.ingredient_id in ingredients
    ]
    confidence = _confidence_by_name(order.confidence_notes, names)

    line_views: list[OrderLineView] = []
    for line in lines:
        ingredient = ingredients.get(line.ingredient_id)
        product = products.get(line.supplier_product_id)
        if ingredient is None or product is None:  # pragma: no cover - FK guarantees both
            continue
        line_views.append(
            OrderLineView(
                po_line_id=line.id,
                ingredient_id=line.ingredient_id,
                ingredient_name=ingredient.name,
                unit=ingredient.unit,
                suggested_packs=line.suggested_packs,
                packs=line.final_packs,
                pack_size=product.pack_size,
                pack_unit=product.pack_unit,
                unit_price_pence=line.unit_price_pence,
                need_qty=line.need_qty,
                is_top_up=line.is_top_up,
                cap=_classify_cap(line.cap_reason, is_top_up=line.is_top_up),
                low_confidence=confidence.get(ingredient.name),
            )
        )

    channel = terms.order_channel if terms else order.supplier.order_channel
    return OrderView(
        po_id=order.id,
        status=order.status,
        supplier_id=order.supplier_id,
        supplier_name=order.supplier.name,
        channel=channel,
        target_delivery_date=order.target_delivery_date,
        lines=tuple(line_views),
        delivery_fee_pence=order.delivery_fee_pence,
        min_order_pence=terms.min_order_pence if terms else 0,
        free_delivery_threshold_pence=terms.free_delivery_threshold_pence if terms else None,
        lead_time_days=terms.lead_time_days if terms else 0,
        delivery_weekdays=terms.delivery_weekdays if terms else (),
        terms_are_placeholders=bool(terms and terms.terms_are_placeholders),
        min_order_topped_up=order.min_order_topped_up,
        confirmed_by=order.confirmed_by,
        confirmed_at=order.confirmed_at,
        # Every registered adapter returns requires_human_completion=True
        # (`ARCHITECTURE.md` 4). A person presses the last button on all four channels,
        # so this is a statement about the design rather than a per-supplier lookup.
        requires_human_completion=True,
        routing_reason_present=bool(order.routing_reason),
    )


def build_order_view(session: Session, po_id: int) -> OrderView:
    order = session.get(PurchaseOrder, po_id)
    if order is None:
        raise LookupError(f"purchase order {po_id} not found")
    return _order_view(session, order)


def list_draft_orders(session: Session, *, supplier_id: int | None = None) -> list[OrderView]:
    """Every order still waiting for a human. Invariant 1's queue."""
    stmt = select(PurchaseOrder).where(PurchaseOrder.status.in_(list(_CONFIRMABLE)))
    if supplier_id is not None:
        stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
    orders = list(
        session.scalars(stmt.order_by(PurchaseOrder.target_delivery_date, PurchaseOrder.id))
    )
    return [_order_view(session, order) for order in orders]


def adjust_packs(session: Session, *, po_line_id: int, delta: int) -> OrderView:
    """The inline +/- button. Moves `final_packs`, never `suggested_packs`.

    Both are kept because the difference is the record of what the human decided
    against what the system proposed. Overwriting the suggestion would erase the only
    evidence that a cap or a top-up was overridden.

    Persisted immediately rather than held in FSM state: a bot restart between the tap
    and the confirm must not silently revert a decision, and `po_line.final_packs` is
    already the column the confirmation reads.
    """
    line = session.get(POLine, po_line_id)
    if line is None:
        raise LookupError(f"po_line {po_line_id} not found")
    order = session.get(PurchaseOrder, line.po_id)
    if order is None:  # pragma: no cover - FK guarantees it
        raise LookupError(f"purchase order {line.po_id} not found")
    if order.status not in _CONFIRMABLE:
        raise OrderNotAdjustable(
            f"purchase order {order.id} is {order.status.value}: packs can only be "
            "adjusted while it is still waiting for a human"
        )
    line.final_packs = max(0, line.final_packs + delta)
    order.total_pence = sum(row.final_packs * row.unit_price_pence for row in order.lines)
    session.flush()
    return _order_view(session, order)


def confirm_order(session: Session, *, po_id: int, confirmed_by: str) -> OrderView:
    """INVARIANT 1. `confirmed_by` is recorded or nothing moves.

    The repository refuses an empty name and `ck_po_confirmed_requires_human` refuses
    the row underneath it. Neither is worked around here: if the name is missing, the
    write is supposed to fail.
    """
    repo = SqlPurchaseOrderRepository(session)
    lines = repo.get_lines(po_id)
    repo.confirm(
        po_id,
        confirmed_by=confirmed_by,
        at=datetime.now(UTC),
        final_packs={line.id: line.final_packs for line in lines},
    )
    session.flush()
    return build_order_view(session, po_id)


def dispatch_order(session: Session, *, po_id: int) -> DispatchView:
    """Hand a CONFIRMED order to its channel. `adapter_for`, never a supplier `if`.

    Nothing here decides whether a person still has to act -- every adapter answers
    that itself, and all four say yes (`ARCHITECTURE.md` 4). The formatter reads
    `requires_human_completion` and says "basket ready", never "ordered".
    """
    order = session.get(PurchaseOrder, po_id)
    if order is None:
        raise LookupError(f"purchase order {po_id} not found")
    if order.status is not POStatus.CONFIRMED:
        raise ValueError(
            f"purchase order {po_id} is {order.status.value}; only a CONFIRMED order can "
            "be dispatched (invariant 1)"
        )

    repo = SqlPurchaseOrderRepository(session)
    items: list[OrderItem] = []
    for line in repo.get_lines(po_id):
        if line.final_packs <= 0:
            continue
        product = session.get(SupplierProduct, line.supplier_product_id)
        ingredient = session.get(Ingredient, line.ingredient_id)
        if product is None or ingredient is None:  # pragma: no cover
            continue
        items.append(
            OrderItem(
                ingredient_name=ingredient.name,
                sku=product.sku,
                packs=line.final_packs,
                pack_size=product.pack_size,
                unit_price_pence=line.unit_price_pence,
                product_url=product.product_url,
            )
        )

    adapter = adapter_for(order.supplier.order_channel)
    prepared = adapter.prepare(po_id, order.supplier, tuple(items))
    result = adapter.dispatch(prepared)
    if result.succeeded and order.status is POStatus.CONFIRMED:
        repo.mark_sent(po_id, at=result.dispatched_at)
        session.flush()
    return DispatchView(
        po_id=po_id,
        supplier_name=order.supplier.name,
        channel=result.channel,
        succeeded=result.succeeded,
        requires_human_completion=result.requires_human_completion,
        target_url=prepared.target_url,
        instruction_steps=sum(1 for row in prepared.instructions.splitlines() if row.strip()),
        items=len(items),
        total_pence=prepared.total_pence,
    )


# ==========================================================================
# Counting
# ==========================================================================


def build_count_session(
    session: Session,
    *,
    kind: CountSessionKind,
    as_of: datetime | None = None,
    only_due: bool = False,
    overdue_days: int = COUNT_OVERDUE_DAYS,
) -> list[CountItemView]:
    """The ingredients to walk through, in the order somebody would walk the shelves.

    `EXPRESS_A` is tier A only -- twice a week, twelve items, the ones the ordering path
    leans on. `FULL` is every tracked ingredient, weekly. Tier C is not here: it is a
    checklist, not a count (spec 4.7).
    """
    as_of = as_of or datetime.now(UTC)
    tiers = (Tier.A,) if kind is CountSessionKind.EXPRESS_A else (Tier.A, Tier.B)
    readings = _readings(session, as_of=as_of, tiers=tiers)
    items: list[CountItemView] = []
    for reading in sorted(readings, key=lambda r: (r.ingredient.tier.value, r.ingredient.name)):
        stock = _stock_line(reading, as_of=as_of)
        if (
            only_due
            and stock.days_since_count is not None
            and stock.days_since_count < overdue_days
        ):
            continue
        items.append(
            CountItemView(
                ingredient_id=reading.ingredient.id,
                name=reading.ingredient.name,
                unit=reading.ingredient.unit,
                tier=reading.ingredient.tier,
                stock=stock,
            )
        )
    return items


def submit_count(
    session: Session,
    *,
    ingredient_id: int,
    counted_qty: Decimal,
    counted_by: str,
    counted_at: datetime | None = None,
) -> CountResultView:
    """One physical count, through the service that owns drift and the gate."""
    outcome = record_count(
        session,
        ingredient_id=ingredient_id,
        counted_qty=counted_qty,
        counted_by=counted_by,
        counted_at=counted_at,
    )
    cause: DriftCause | None = None
    if outcome.explanation is not None:
        cause = outcome.explanation.cause
    return CountResultView(
        ingredient_id=ingredient_id,
        name=outcome.ingredient.name,
        unit=outcome.ingredient.unit,
        counted_qty=outcome.counted_qty,
        theoretical_qty=outcome.on_hand_before.qty,
        drift_pct=None if outcome.drift is None else outcome.drift.drift_pct,
        verdict=outcome.verdict,
        gate_action=outcome.decision.action,
        auto_order_enabled=outcome.decision.auto_order_enabled,
        alert=outcome.alert,
        clean_streak=outcome.decision.clean_streak,
        required_streak=outcome.decision.required_streak,
        suggested_waste_factor=(
            None if outcome.drift is None else outcome.drift.suggested_waste_factor
        ),
        current_waste_factor=outcome.ingredient.waste_factor,
        cause=cause,
        back_dated=any("back-dated" in note for note in outcome.notes),
    )


# ==========================================================================
# Tier C checklist
# ==========================================================================


def build_checklist(
    session: Session, *, at: datetime | None = None, only_stale: bool = True
) -> list[ChecklistItemView]:
    at = at or datetime.now(UTC)
    roster = checklist_roster(session, at=at)
    items: list[ChecklistItemView] = []
    for ingredient_id, name in roster.items:
        if only_stale and not roster.is_stale(ingredient_id):
            continue
        seen = roster.latest.get(ingredient_id)
        items.append(
            ChecklistItemView(
                ingredient_id=ingredient_id,
                name=name,
                unit=Unit.EACH,
                last_was_low=None if seen is None else seen[0] is ChecklistStatus.LOW,
                last_answered_at=None if seen is None else seen[1],
            )
        )
    return items


def submit_checklist(
    session: Session, *, ingredient_id: int, is_low: bool, responded_by: str
) -> ChecklistItemView:
    answer = record_checklist_answer(
        session,
        ingredient_id=ingredient_id,
        status=ChecklistStatus.LOW if is_low else ChecklistStatus.OK,
        responded_by=responded_by,
    )
    return ChecklistItemView(
        ingredient_id=answer.ingredient_id,
        name=answer.ingredient_name,
        unit=Unit.EACH,
        last_was_low=answer.status is ChecklistStatus.LOW,
        last_answered_at=answer.responded_at,
    )


# ==========================================================================
# Deliveries -- the expiry date, which is what makes FIFO possible at all
# ==========================================================================


def _expected_qty(line: POLine, product: SupplierProduct, target: Unit) -> Decimal | None:
    packs = line.final_packs or line.suggested_packs
    if packs <= 0:
        return None
    try:
        return Decimal(packs) * convert(product.pack_size, product.pack_unit, target)
    except ValueError:
        # `convert` raises across dimensions rather than assuming 1:1. A pack unit that
        # cannot reach the stocking unit is a data error, and reporting "expected
        # unknown" is honest where inventing a number would not be.
        return None


def build_delivery_orders(
    session: Session, *, up_to: date | None = None, po_id: int | None = None
) -> list[DeliveryOrderView]:
    """Orders stock is expected against: CONFIRMED or SENT, by delivery date.

    DRAFT is excluded on purpose. `receive_po_line` refuses a DRAFT (invariant 1: an
    order nobody confirmed was never placed), so offering one here would build a flow
    whose only outcome is a refusal.
    """
    stmt = select(PurchaseOrder).where(PurchaseOrder.status.in_(list(_AWAITING_DELIVERY)))
    if po_id is not None:
        # Narrowed, not replaced: a DRAFT named by id is still not receivable, and
        # offering it would build a flow whose only outcome is `receive_po_line`
        # refusing (invariant 1 -- an order nobody confirmed was never placed).
        stmt = stmt.where(PurchaseOrder.id == po_id)
    elif up_to is not None:
        stmt = stmt.where(PurchaseOrder.target_delivery_date <= up_to)
    orders = list(
        session.scalars(stmt.order_by(PurchaseOrder.target_delivery_date, PurchaseOrder.id))
    )

    batches = SqlBatchRepository(session)
    out: list[DeliveryOrderView] = []
    for order in orders:
        lines: list[DeliveryLineView] = []
        for line in SqlPurchaseOrderRepository(session).get_lines(order.id):
            ingredient = session.get(Ingredient, line.ingredient_id)
            product = session.get(SupplierProduct, line.supplier_product_id)
            if ingredient is None or product is None:  # pragma: no cover
                continue
            shelf_life = batches.shelf_life(line.ingredient_id)
            lines.append(
                DeliveryLineView(
                    po_line_id=line.id,
                    ingredient_id=line.ingredient_id,
                    ingredient_name=ingredient.name,
                    unit=ingredient.unit,
                    ordered_packs=line.final_packs,
                    pack_size=product.pack_size,
                    pack_unit=product.pack_unit,
                    expected_qty=_expected_qty(line, product, ingredient.unit),
                    already_received_qty=line.received_qty or Decimal("0"),
                    storage=shelf_life.storage if shelf_life else Storage.AMBIENT,
                    default_shelf_life_days=shelf_life.shelf_life_days if shelf_life else None,
                    shelf_life_is_estimate=bool(
                        shelf_life and shelf_life.source is PriceSource.ESTIMATE
                    ),
                    open_life_days=shelf_life.open_life_days if shelf_life else None,
                )
            )
        out.append(
            DeliveryOrderView(
                po_id=order.id,
                supplier_name=order.supplier.name,
                status=order.status,
                target_delivery_date=order.target_delivery_date,
                lines=tuple(lines),
            )
        )
    return out


#: `DeliveryReceipt.warnings` are English sentences, so they are recognised by their
#: opening rather than shown. See the module docstring; a `code` on the service's
#: warning would remove the need for this.
_RECEIPT_ISSUE_MARKERS: tuple[tuple[str, ReceiptIssue], ...] = (
    ("no expiry entered", ReceiptIssue.EXPIRY_ASSUMED),
    ("is not after the delivery", ReceiptIssue.EXPIRY_BEFORE_RECEIPT),
    ("over-delivery recorded", ReceiptIssue.OVER_DELIVERY),
    ("no price given", ReceiptIssue.PRICE_FROM_CACHE),
    ("configured as non-perishable", ReceiptIssue.NON_PERISHABLE_WITH_DATE),
)


def _receipt_view(session: Session, receipt: DeliveryReceipt) -> ReceiptView:
    issues: list[ReceiptIssue] = []
    unclassified = 0
    for warning in receipt.warnings:
        for marker, issue in _RECEIPT_ISSUE_MARKERS:
            if marker in warning:
                issues.append(issue)
                break
        else:
            unclassified += 1
    if receipt.expiry_was_assumed and ReceiptIssue.EXPIRY_ASSUMED not in issues:
        issues.append(ReceiptIssue.EXPIRY_ASSUMED)
    return ReceiptView(
        batch_id=receipt.batch_id,
        ingredient_name=receipt.ingredient_name,
        unit=receipt.unit,
        qty=receipt.qty,
        received_at=receipt.received_at,
        expires_at=receipt.expires_at,
        expiry_was_assumed=receipt.expiry_was_assumed,
        days_left=receipt.shelf_life_days_left,
        value_pence=receipt.value_pence,
        order_completed=receipt.order_completed,
        open_life_days=SqlBatchRepository(session).open_life_days(receipt.ingredient_id),
        issues=tuple(dict.fromkeys(issues)),
        unclassified_issues=unclassified,
    )


def receive_line(
    session: Session,
    *,
    po_line_id: int,
    packs: int | None = None,
    qty: Decimal | None = None,
    expires_at: datetime | None = None,
    received_by: str,
) -> ReceiptView:
    """Receive one ordered line, with the expiry the person read off the carton.

    `expires_at=None` is allowed and is the case the message has to be loud about: the
    service falls back to the ESTIMATE shelf life and stamps the batch `EXPIRY ASSUMED`
    (`ARCHITECTURE.md` 8F.1). Refusing would lose a real delivery, which is worse -- but
    an assumed date is the single number that decides a write-off, so the receipt says
    it was assumed.
    """
    receipt = receive_po_line(
        session,
        po_line_id=po_line_id,
        received_packs=packs,
        received_qty=qty,
        expires_at=expires_at,
        received_by=received_by,
    )
    return _receipt_view(session, receipt)


def receive_adhoc_delivery(
    session: Session,
    *,
    ingredient_id: int,
    qty: Decimal,
    expires_at: datetime | None = None,
    received_by: str,
    unit_cost_pence: Decimal | None = None,
) -> ReceiptView:
    """The walk to Tesco. No order behind it, so it gets its own door (spec 4.4)."""
    receipt = receive_adhoc(
        session,
        ingredient_id=ingredient_id,
        qty=qty,
        expires_at=expires_at,
        received_by=received_by,
        unit_cost_pence=unit_cost_pence,
    )
    return _receipt_view(session, receipt)


# ==========================================================================
# The morning digest
# ==========================================================================


def build_digest(
    session: Session,
    *,
    as_of: datetime | None = None,
    tz: ZoneInfo | None = None,
    short_dated_days: int = DEFAULT_SHORT_DATED_DAYS,
    overdue_days: int = COUNT_OVERDUE_DAYS,
    headline_limit: int = 12,
) -> DigestView:
    """Everything worth saying at 07:00, read-only.

    **Nothing here writes.** The expiry sweep is run `dry_run=True`: the digest reports
    what a sweep would find, and the sweep job books it. A digest that wrote off stock
    would make reading the morning message a financial event, and re-reading it twice as
    expensive.
    """
    as_of = as_of or datetime.now(UTC)
    tz = tz or settings.tz
    local_date = as_of.astimezone(tz).date()

    readings = _readings(session, as_of=as_of, tiers=(Tier.A, Tier.B))
    lines = [_stock_line(r, as_of=as_of) for r in readings]
    tier_a = [line for line in lines if line.tier is Tier.A]

    sweep = sweep_expiry(session, at=as_of, short_dated_days=short_dated_days, dry_run=True)

    drafts = list_draft_orders(session)
    deliveries = build_delivery_orders(
        session, up_to=local_date + timedelta(days=DIGEST_DELIVERY_HORIZON_DAYS)
    )

    counts_due = [
        line
        for line in lines
        if line.days_since_count is None or line.days_since_count >= overdue_days
    ]

    par_repo = SqlParLevelRepository(session)
    ingredient_repo = SqlIngredientRepository(session)
    alerts: list[DriftAlertView] = []
    for snapshot in ingredient_repo.list_tracked(tiers=(Tier.A, Tier.B)):
        decision = gate_status(session, ingredient=snapshot)
        par = par_repo.get(snapshot.id)
        if not decision.alert and decision.action is not GateAction.REVOKE:
            continue
        history = explain_drift_history(session, ingredient_id=snapshot.id, limit=1)
        latest = history[0] if history else None
        alerts.append(
            DriftAlertView(
                ingredient_id=snapshot.id,
                name=snapshot.name,
                unit=snapshot.unit,
                drift_pct=(decision.considered_pcts[0] if decision.considered_pcts else None),
                verdict=decision.verdict,
                gate_action=decision.action,
                auto_order_enabled=bool(par and par.auto_order_enabled),
                alert=decision.alert,
                clean_streak=decision.clean_streak,
                required_streak=decision.required_streak,
                cause=None if latest is None else latest.cause,
                expiry_share=None if latest is None else latest.explanation.expiry_share,
            )
        )

    return DigestView(
        as_of=as_of,
        local_date=local_date,
        tracked_count=len(lines),
        headline=tuple(sorted(tier_a, key=lambda line: line.name)[:headline_limit]),
        unanchored=tuple(line for line in lines if not line.has_count_basis),
        negative=tuple(line for line in lines if line.qty < 0),
        unbatched=tuple(
            line
            for line in lines
            if line.batch_qty > 0 and abs(line.unbatched_qty) > Decimal("0.001")
        ),
        short_dated=tuple(
            ExpiryLineView(
                ingredient_name=row.ingredient_name,
                unit=row.unit,
                qty=row.qty,
                expires_at=row.expires_at,
                days_left=row.days_left,
                value_pence=row.value_pence,
            )
            for row in sweep.short_dated
        ),
        pending_write_offs=tuple(
            WriteOffView(
                ingredient_name=row.ingredient_name,
                unit=row.unit,
                qty=row.qty,
                expired_at=row.expired_at,
                days_overdue=row.days_overdue,
                loss_pence=row.loss_pence,
                expired_after_opening=row.expired_after_opening,
                expiry_was_assumed=row.expiry_was_assumed,
            )
            for row in sweep.write_offs
        ),
        already_written_off=sweep.already_written_off,
        drafts=tuple(drafts),
        deliveries_expected=tuple(deliveries),
        counts_due=tuple(sorted(counts_due, key=lambda line: (line.tier.value, line.name))),
        count_overdue_days=overdue_days,
        drift_alerts=tuple(alerts),
        checklist_due=tuple(build_checklist(session, at=as_of)),
        checklist_low=tuple(
            item
            for item in build_checklist(session, at=as_of, only_stale=False)
            if item.last_was_low
        ),
        telegram_configured=bool(settings.telegram_bot_token),
    )


# ==========================================================================
# Shared helpers the jobs also use
# ==========================================================================


def local_midnight(day: date, tz: ZoneInfo | None = None) -> datetime:
    """Local midnight as a UTC instant. The day boundary the whole system uses."""
    tz = tz or settings.tz
    return datetime.combine(day, time(0, 0), tzinfo=tz).astimezone(UTC)


def order_channel_of(session: Session, supplier_id: int) -> OrderChannel:
    terms = SqlSourcingRepository(session).terms(supplier_id)
    if terms is None:
        raise LookupError(f"supplier {supplier_id} has no terms")
    return terms.order_channel


def lookup_ingredient(session: Session, *, name: str) -> IngredientRefView | None:
    """Find one ingredient by name for the ad-hoc receipt. Exact first, then prefix.

    Never a fuzzy match. Receiving stock against the wrong ingredient puts a delivery in
    the wrong ledger and the error is invisible afterwards, so an ambiguous name gets no
    answer rather than a guess.
    """
    wanted = name.strip().lower()
    if not wanted:
        return None
    rows = list(session.scalars(select(Ingredient).order_by(Ingredient.name)))
    exact = [row for row in rows if row.name.lower() == wanted]
    matches = exact or [row for row in rows if row.name.lower().startswith(wanted)]
    if len(matches) != 1:
        return None
    row = matches[0]
    return IngredientRefView(
        ingredient_id=row.id,
        name=row.name,
        unit=row.unit,
        tier=row.tier,
        storage=row.storage,
        shelf_life_days=row.shelf_life_days,
        shelf_life_is_estimate=row.shelf_life_source is PriceSource.ESTIMATE,
    )
