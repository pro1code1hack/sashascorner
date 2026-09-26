"""Sync assembly for the Stock, Orders and Suppliers writes (and two reads).

Same two rules as `api/views/__init__.py`: nothing here is async, and nothing returns an
ORM object -- every function runs inside `asyncio.to_thread` and returns a finished
Pydantic model before the session closes.

Every write goes through a service in `cafeops/services/` (CLAUDE.md 8). The refusals are
the services' own `ValueError` subclasses, which the app turns into a 422 whose `detail` is
the service's sentence, shown verbatim by the screen. Nothing here decides a rule.

**No route in this area creates, confirms or sends a purchase order** (DECISIONS 1,
invariant 1). Orders can be cancelled, marked sent once confirmed in Telegram, and
received; a shop run records batches and a routing, never a purchase order.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.api.areas.stock_schemas import (
    ChecklistIn,
    ChecklistOut,
    CountIn,
    CountOut,
    DeliveryIn,
    DeliveryOut,
    DrawnBatchOut,
    IngredientOptionOut,
    OrderAction,
    OrderCounts,
    OrdersListResponse,
    ParChangeOut,
    ParIn,
    ProductActorIn,
    ProductArchiveIn,
    ProductLinkIn,
    ProductPatchIn,
    ProductWriteOut,
    PurchaseOrderOut,
    ReceiveIn,
    ReceiveOut,
    ShopRunIn,
    ShopRunMonthOut,
    ShopRunOut,
    ShopRunRowOut,
    ShopRunsResponse,
    SupplierArchiveIn,
    SupplierCreateIn,
    SupplierPatchIn,
    SupplierProductOut,
    SupplierProductsResponse,
    SupplierWriteOut,
    TierIn,
    TierOut,
    VsOtherOut,
    WriteOffIn,
    WriteOffOut,
)
from cafeops.api.encoding import as_pence, as_qty, pct
from cafeops.api.schemas import Cost, OnHand, SupplierOut
from cafeops.api.views.orders import _supplier_out, persisted_order_out
from cafeops.api.views.stock import _attribution, _drift, trust_label
from cafeops.config import settings
from cafeops.db.models import (
    ChecklistStatus,
    Expense,
    Ingredient,
    IngredientPrice,
    OrderChannel,
    POStatus,
    PriceSource,
    PurchaseOrder,
    Supplier,
    SupplierProduct,
    TescoRouting,
    Tier,
    Unit,
    WriteOffReason,
)
from cafeops.db.repositories.sourcing import _terms
from cafeops.services import order_actions, suppliers
from cafeops.services.receive_delivery import DeliveryReceipt, receive_adhoc
from cafeops.services.record_checklist import record_checklist_answer
from cafeops.services.record_count import record_count
from cafeops.services.record_write_off import record_write_off
from cafeops.services.stock_settings import change_tier, set_par_floor

__all__ = [
    "cancel_order_view",
    "checklist_view",
    "count_view",
    "delivery_view",
    "mark_sent_view",
    "orders_view",
    "par_view",
    "product_archive_view",
    "product_link_view",
    "product_patch_view",
    "product_prefer_view",
    "receive_view",
    "shop_run_view",
    "shop_runs_view",
    "supplier_archive_view",
    "supplier_create_view",
    "supplier_patch_view",
    "supplier_products_view",
    "tier_view",
    "write_off_view",
]


# --------------------------------------------------------------------------
# parsing at the edge
# --------------------------------------------------------------------------

_DECIMAL = re.compile(r"^[+-]?\d+(\.\d+)?$")


def parse_qty(raw: str, what: str = "quantity") -> Decimal:
    """A decimal string -> Decimal. Plain digits only: no exponent, no NaN, no float."""
    text = raw.strip()
    if not _DECIMAL.match(text):
        raise ValueError(f"{what} must be a plain number like 2 or 1.5, got {raw!r}")
    try:
        return Decimal(text)
    except InvalidOperation:  # pragma: no cover - the regex already refused it
        raise ValueError(f"{what} must be a plain number, got {raw!r}") from None


def use_by(day: date | None) -> datetime | None:
    """A carton's use-by DATE -> the instant it stops being usable: the end of that
    local day. Stock dated the 3rd is good all of the 3rd."""
    if day is None:
        return None
    return datetime.combine(day, time(23, 59, 59), tzinfo=settings.tz).astimezone(UTC)


def _delivery_out(receipt: DeliveryReceipt) -> DeliveryOut:
    return DeliveryOut(
        batch_id=receipt.batch_id,
        ingredient_id=receipt.ingredient_id,
        ingredient_name=receipt.ingredient_name,
        qty=as_qty(receipt.qty) or "0",
        unit=receipt.unit.value,
        received_at=receipt.received_at,
        expires_at=receipt.expires_at,
        expiry_assumed=receipt.expiry_was_assumed,
        warnings=receipt.warnings,
        order_completed=receipt.order_completed,
    )


# --------------------------------------------------------------------------
# stock writes
# --------------------------------------------------------------------------


def count_view(session: Session, *, ingredient_id: int, body: CountIn) -> CountOut:
    outcome = record_count(
        session,
        ingredient_id=ingredient_id,
        counted_qty=parse_qty(body.counted_qty, "counted_qty"),
        counted_at=body.counted_at,
        counted_by=body.counted_by,
        note=body.note,
    )
    session.flush()
    drift = _drift(session, outcome.ingredient)
    before = outcome.on_hand_before
    recon = outcome.reconciliation
    recon_note: str | None = None
    if recon is not None and not recon.is_noop:
        if recon.surplus_qty > 0:
            recon_note = (
                f"The count found {as_qty(recon.surplus_qty)} more than the batch records "
                "held; it was booked as a new batch so it can expire and be written off."
            )
        else:
            recon_note = (
                f"The batch records held {as_qty(-recon.delta)} more than the count; they "
                "were brought down to match, soonest-expiring first."
            )
    return CountOut(
        ingredient_id=outcome.ingredient.id,
        ingredient_name=outcome.ingredient.name,
        unit=outcome.ingredient.unit.value,
        stock_count_id=outcome.stock_count_id,
        counted_qty=as_qty(outcome.counted_qty) or "0",
        counted_at=outcome.counted_at,
        counted_by=body.counted_by.strip(),
        theoretical_before=OnHand(
            qty=as_qty(before.qty) or "0",
            unit=outcome.ingredient.unit.value,
            as_of=before.as_of,
            is_theoretical=True,
            has_count_basis=before.has_count_basis,
            basis_count_qty=as_qty(before.basis_count_qty),
            basis_counted_at=before.basis_counted_at,
            movement_sum=as_qty(before.movement_sum) or "0",
            movement_count=before.movement_count,
            basis_label=(
                "counted + ledger" if before.has_count_basis else "ledger only - NO COUNT"
            ),
            is_negative=before.qty < 0,
        ),
        drift_pct=None if outcome.drift is None else pct(outcome.drift.drift_pct),
        verdict=None if outcome.verdict is None else outcome.verdict.value,
        trust_label=trust_label(has_count_basis=True, drift=drift),
        auto_order_enabled=outcome.decision.auto_order_enabled,
        gate_reason=outcome.decision.reason,
        alert_level=outcome.decision.alert_level.value,
        attribution=None if outcome.explanation is None else _attribution(outcome.explanation),
        reconciliation_note=recon_note,
        notes=outcome.notes,
    )


def delivery_view(session: Session, *, ingredient_id: int, body: DeliveryIn) -> DeliveryOut:
    cost = None if body.unit_cost_pence is None else parse_qty(body.unit_cost_pence, "unit cost")
    if cost is not None and cost <= 0:
        raise ValueError("a unit cost must be more than 0p; leave it out if nobody knows it")
    qty = parse_qty(body.qty, "qty")
    ingredient = session.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise LookupError(f"ingredient {ingredient_id} not found")
    if ingredient.unit is Unit.EACH and qty != qty.to_integral_value():
        raise ValueError(
            f"{ingredient.name} is counted in whole units: {qty} is not a whole number"
        )
    receipt = receive_adhoc(
        session,
        ingredient_id=ingredient_id,
        qty=qty,
        expires_at=use_by(body.expires_on),
        received_by=body.received_by,
        unit_cost_pence=cost,
        note=body.note,
    )
    return _delivery_out(receipt)


def write_off_view(session: Session, *, ingredient_id: int, body: WriteOffIn) -> WriteOffOut:
    outcome = record_write_off(
        session,
        ingredient_id=ingredient_id,
        qty=parse_qty(body.qty, "qty"),
        reason=WriteOffReason[body.reason],
        recorded_by=body.recorded_by,
        note=body.note,
    )
    if outcome.value_pence is None:
        value = Cost(
            pence=None,
            is_missing=True,
            excluded_from_aggregates=True,
            note="part of this stock has no price, so the value of the loss is unknown",
        )
    else:
        value = Cost(
            pence=as_pence(outcome.value_pence),
            source=PriceSource.ESTIMATE.value if outcome.value_is_estimate else None,
            is_estimate=outcome.value_is_estimate,
        )
    return WriteOffOut(
        ingredient_id=outcome.ingredient_id,
        ingredient_name=outcome.ingredient_name,
        unit=outcome.unit.value,
        qty=as_qty(outcome.qty) or "0",
        reason=outcome.reason.value,
        movement_type=outcome.movement_type.value,
        movement_ids=outcome.movement_ids,
        batches_drawn=tuple(
            DrawnBatchOut(batch_id=b.batch_id, qty=as_qty(b.qty) or "0")
            for b in outcome.batches_drawn
        ),
        shortfall_qty=as_qty(outcome.shortfall_qty) or "0",
        value=value,
    )


def checklist_view(session: Session, *, ingredient_id: int, body: ChecklistIn) -> ChecklistOut:
    ingredient = session.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise LookupError(f"ingredient {ingredient_id} not found")
    if ingredient.tier is not Tier.C:
        raise ValueError(
            f"{ingredient.name} is tier {ingredient.tier.value}: it is counted, not "
            "checked off. The checklist is for tier C."
        )
    answer = record_checklist_answer(
        session,
        ingredient_id=ingredient_id,
        status=ChecklistStatus[body.status],
        responded_by=body.responded_by.strip(),
    )
    return ChecklistOut(
        ingredient_id=answer.ingredient_id,
        ingredient_name=answer.ingredient_name,
        status=answer.status.value,
        responded_at=answer.responded_at,
        responded_by=answer.responded_by,
    )


def par_view(session: Session, *, ingredient_id: int, body: ParIn) -> ParChangeOut:
    change = set_par_floor(
        session,
        ingredient_id=ingredient_id,
        min_qty=parse_qty(body.min_qty, "min_qty"),
        changed_by=body.changed_by,
    )
    return ParChangeOut(
        ingredient_id=change.ingredient_id,
        ingredient_name=change.ingredient_name,
        min_qty_before=as_qty(change.min_qty_before) or "0",
        min_qty_after=as_qty(change.min_qty_after) or "0",
        max_qty=as_qty(change.max_qty) or "0",
        auto_order_enabled=change.auto_order_enabled,
        set_by=change.set_by,
        set_at=change.set_at,
    )


def tier_view(session: Session, *, ingredient_id: int, body: TierIn) -> TierOut:
    change = change_tier(
        session,
        ingredient_id=ingredient_id,
        tier=Tier(body.tier),
        changed_by=body.changed_by,
        reason=body.reason,
    )
    return TierOut(
        ingredient_id=change.ingredient_id,
        ingredient_name=change.ingredient_name,
        tier_before=change.tier_before.value,
        tier_after=change.tier_after.value,
        would_clear_gate=change.would_clear_gate,
        clean_streak=change.clean_streak,
        required_streak=change.required_streak,
        auto_order_enabled=change.auto_order_enabled,
        tracking_enabled=change.tracking_enabled,
        note=change.note,
    )


# --------------------------------------------------------------------------
# orders
# --------------------------------------------------------------------------

_WAITING = (POStatus.DRAFT, POStatus.PENDING_CONFIRM)
_OPEN = (POStatus.DRAFT, POStatus.PENDING_CONFIRM, POStatus.CONFIRMED, POStatus.SENT)


def _actions(status: POStatus) -> tuple[OrderAction, ...]:
    """What the web may do next. The server decides, so the screen cannot drift from the
    service rules. There is never a 'confirm': that is Telegram's (invariant 1)."""
    if status in (POStatus.DRAFT, POStatus.PENDING_CONFIRM):
        return ("cancel",)
    if status is POStatus.CONFIRMED:
        return ("mark_sent", "receive", "cancel")
    if status is POStatus.SENT:
        return ("receive",)
    return ()


def _order_out(session: Session, po: PurchaseOrder) -> PurchaseOrderOut:
    base = persisted_order_out(session, po)
    supplier = session.get(Supplier, po.supplier_id)
    return PurchaseOrderOut(
        **base.model_dump(),
        supplier_id=po.supplier_id,
        supplier_name=supplier.name if supplier is not None else f"supplier {po.supplier_id}",
        supplier_archived=supplier is not None and supplier.archived_at is not None,
        terms_are_placeholders=bool(supplier and supplier.terms_are_placeholders),
        cancelled_at=po.cancelled_at,
        cancelled_by=po.cancelled_by,
        cancel_reason=po.cancel_reason,
        routing_reason=po.routing_reason,
        actions=_actions(po.status),
    )


def orders_view(
    session: Session,
    *,
    statuses: tuple[POStatus, ...] | None = None,
    supplier_id: int | None = None,
    limit: int = 200,
) -> OrdersListResponse:
    stmt = select(PurchaseOrder).order_by(PurchaseOrder.created_at.desc(), PurchaseOrder.id.desc())
    if statuses:
        stmt = stmt.where(PurchaseOrder.status.in_(list(statuses)))
    if supplier_id is not None:
        stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
    orders = tuple(_order_out(session, po) for po in session.scalars(stmt.limit(limit)))
    every = list(session.scalars(select(PurchaseOrder.status)))
    return OrdersListResponse(
        orders=orders,
        counts=OrderCounts(
            open=sum(1 for s in every if s in _OPEN),
            waiting=sum(1 for s in every if s in _WAITING),
        ),
    )


def cancel_order_view(
    session: Session, *, po_id: int, cancelled_by: str, reason: str | None
) -> PurchaseOrderOut:
    po = order_actions.cancel_order(session, po_id=po_id, cancelled_by=cancelled_by, reason=reason)
    return _order_out(session, po)


def mark_sent_view(session: Session, *, po_id: int, sent_by: str) -> PurchaseOrderOut:
    po = order_actions.mark_order_sent(session, po_id=po_id, sent_by=sent_by)
    return _order_out(session, po)


def receive_view(session: Session, *, po_id: int, body: ReceiveIn) -> ReceiveOut:
    lines = [
        order_actions.ReceiveLine(
            po_line_id=line.po_line_id,
            received_packs=line.received_packs,
            received_qty=(
                None if line.received_qty is None else parse_qty(line.received_qty, "received_qty")
            ),
            expires_at=use_by(line.expires_on),
        )
        for line in body.lines
    ]
    po, receipts = order_actions.receive_order(
        session, po_id=po_id, received_by=body.received_by, lines=lines
    )
    return ReceiveOut(
        order=_order_out(session, po),
        receipts=tuple(_delivery_out(r) for r in receipts),
    )


def shop_run_view(session: Session, *, body: ShopRunIn) -> ShopRunOut:
    outcome = order_actions.log_shop_run(
        session,
        bought_by=body.bought_by,
        where=body.where,
        reason=body.reason,
        lines=[
            order_actions.ShopRunLine(
                ingredient_id=line.ingredient_id,
                qty=parse_qty(line.qty, "qty"),
                paid_pence=line.paid_pence,
                expires_at=use_by(line.expires_on),
            )
            for line in body.lines
        ],
    )
    return ShopRunOut(
        routing_ids=outcome.routing_ids,
        receipts=tuple(_delivery_out(r) for r in outcome.receipts),
        paid_pence=outcome.paid_pence,
        premium_pence=outcome.premium_pence,
    )


#: Spec 2.4: an expense is a shop run when its words say it was bought at a shop.
SHOP_WORDS = re.compile(
    r"tesco|convenience|co-?op|aldi|lidl|sainsbury|asda|spar\b|morrisons|farmfoods|iceland",
    re.IGNORECASE,
)


def shop_runs_view(session: Session, *, months: int = 8) -> ShopRunsResponse:
    """Every shop run: logged here (tesco_routing) and found in the expenses log.

    `amount_pence` is null when nothing recorded what was paid -- a routing written by
    the draft builder knows the premium but not the till total -- and a month or the
    total containing such a run is null too, with the priced part beside it
    (invariant 8: never a partial sum passed off as the whole).
    """
    tz = settings.tz
    runs: list[ShopRunRowOut] = []
    for routing in session.scalars(select(TescoRouting).order_by(TescoRouting.occurred_at.desc())):
        ingredient = session.get(Ingredient, routing.ingredient_id)
        runs.append(
            ShopRunRowOut(
                occurred_at=routing.occurred_at,
                where=routing.retailer or "Tesco",
                note=routing.reason,
                ingredient_name=ingredient.name if ingredient is not None else None,
                amount_pence=routing.paid_pence,
                premium_pence=routing.premium_pence,
                source="tesco_routing",
                bought_by=routing.bought_by,
            )
        )
    for expense in session.scalars(select(Expense).where(Expense.deleted_at.is_(None))):
        text = f"{expense.description} {expense.notes or ''}"
        if not SHOP_WORDS.search(text):
            continue
        runs.append(
            ShopRunRowOut(
                occurred_at=datetime.combine(expense.paid_on, time(12), tzinfo=tz).astimezone(UTC),
                where=expense.description,
                note=expense.notes or "from the expenses log",
                ingredient_name=None,
                amount_pence=expense.amount_pence,
                premium_pence=None,
                source="expense",
                bought_by=expense.updated_by,
            )
        )
    runs.sort(key=lambda r: r.occurred_at, reverse=True)

    by_month: dict[str, list[ShopRunRowOut]] = {}
    for run in runs:
        local = run.occurred_at.astimezone(tz)
        by_month.setdefault(f"{local.year:04d}-{local.month:02d}", []).append(run)
    months_out: list[ShopRunMonthOut] = []
    for month in sorted(by_month)[-months:]:
        items = by_month[month]
        priced = sum(r.amount_pence for r in items if r.amount_pence is not None)
        complete = all(r.amount_pence is not None for r in items)
        months_out.append(
            ShopRunMonthOut(
                month=month,
                amount_pence=priced if complete else None,
                priced_amount_pence=priced,
                runs=len(items),
            )
        )
    priced_runs = [r for r in runs if r.amount_pence is not None]
    priced_total = sum(r.amount_pence or 0 for r in priced_runs)
    notes: list[str] = []
    unpriced = len(runs) - len(priced_runs)
    if unpriced:
        notes.append(
            f"{unpriced} run(s) have no amount recorded (nobody kept the receipt, or the "
            "ordering run only logged that they were needed), so the total covers the "
            "priced runs only and is shown as incomplete."
        )
    return ShopRunsResponse(
        runs=tuple(runs[:200]),
        by_month=tuple(months_out),
        total_pence=priced_total if unpriced == 0 else None,
        priced_total_pence=priced_total,
        priced_runs=len(priced_runs),
        runs_count=len(runs),
        premium_total_pence=sum(r.premium_pence or 0 for r in runs),
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------
# suppliers
# --------------------------------------------------------------------------


def _supplier_full(session: Session, row: Supplier) -> SupplierOut:
    count = len(
        list(
            session.scalars(
                select(SupplierProduct.id).where(
                    SupplierProduct.supplier_id == row.id, SupplierProduct.archived_at.is_(None)
                )
            )
        )
    )
    return _supplier_out(_terms(row), row, count)


def supplier_create_view(session: Session, *, body: SupplierCreateIn) -> SupplierWriteOut:
    row = suppliers.create_supplier(
        session,
        name=body.name,
        created_by=body.created_by,
        order_channel=OrderChannel[body.order_channel],
        kind=body.kind,
        contact=body.contact,
        order_url=body.order_url,
        notes=body.notes,
    )
    return SupplierWriteOut(supplier=_supplier_full(session, row), changed=("created",))


def supplier_patch_view(
    session: Session, *, supplier_id: int, body: SupplierPatchIn
) -> SupplierWriteOut:
    given = body.model_fields_set
    unset = suppliers.UNSET
    row, changed = suppliers.update_supplier_profile(
        session,
        supplier_id=supplier_id,
        changed_by=body.changed_by,
        name=body.name,
        order_channel=OrderChannel[body.order_channel] if body.order_channel else None,
        kind=body.kind if "kind" in given else unset,
        contact=body.contact if "contact" in given else unset,
        order_url=body.order_url if "order_url" in given else unset,
        notes=body.notes if "notes" in given else unset,
    )
    return SupplierWriteOut(supplier=_supplier_full(session, row), changed=tuple(changed))


def supplier_archive_view(
    session: Session, *, supplier_id: int, body: SupplierArchiveIn
) -> SupplierWriteOut:
    row, restarred = suppliers.archive_supplier(
        session, supplier_id=supplier_id, archived_by=body.archived_by
    )
    return SupplierWriteOut(
        supplier=_supplier_full(session, row), changed=("archived",), restarred=tuple(restarred)
    )


def _price_sources(session: Session) -> dict[tuple[int, int], PriceSource]:
    """Newest ingredient_price source per (ingredient, supplier)."""
    out: dict[tuple[int, int], PriceSource] = {}
    for row in session.scalars(
        select(IngredientPrice)
        .where(IngredientPrice.supplier_id.is_not(None))
        .order_by(IngredientPrice.effective_from, IngredientPrice.id)
    ):
        if row.supplier_id is not None:
            out[(row.ingredient_id, row.supplier_id)] = row.source
    return out


def _product_outs(session: Session, products: list[SupplierProduct]) -> list[SupplierProductOut]:
    """Rows with their per-unit price and the comparison against the cheapest other
    active link for the same ingredient (spec 3.1 'vs other suppliers')."""
    ingredient_ids = sorted({p.ingredient_id for p in products})
    ingredients = {
        i.id: i
        for i in session.scalars(select(Ingredient).where(Ingredient.id.in_(ingredient_ids)))
    }
    others = list(
        session.execute(
            select(SupplierProduct, Supplier)
            .join(Supplier, Supplier.id == SupplierProduct.supplier_id)
            .where(
                SupplierProduct.ingredient_id.in_(ingredient_ids),
                SupplierProduct.archived_at.is_(None),
                Supplier.archived_at.is_(None),
            )
        ).all()
    )
    sources = _price_sources(session)
    out: list[SupplierProductOut] = []
    for product in products:
        ingredient = ingredients.get(product.ingredient_id)
        if ingredient is None:
            continue
        mine = suppliers.unit_price_pence(product, ingredient.unit)
        best: tuple[Decimal, str] | None = None
        for other, supplier in others:
            if other.ingredient_id != product.ingredient_id:
                continue
            if other.supplier_id == product.supplier_id:
                continue
            price = suppliers.unit_price_pence(other, ingredient.unit)
            if price is None:
                continue
            if best is None or price < best[0]:
                best = (price, supplier.name)
        vs = None
        if best is not None and mine is not None and best[0] > 0:
            diff = (mine - best[0]) / best[0] * Decimal(100)
            vs = VsOtherOut(
                supplier_name=best[1],
                unit_price_pence=as_pence(best[0]) or "0",
                diff_pct=float(round(diff, 1)),
            )
        source = sources.get((product.ingredient_id, product.supplier_id))
        out.append(
            SupplierProductOut(
                supplier_product_id=product.id,
                supplier_id=product.supplier_id,
                ingredient_id=product.ingredient_id,
                ingredient_name=ingredient.name,
                ingredient_unit=ingredient.unit.value,
                sku=product.sku,
                pack_size=as_qty(product.pack_size) or "0",
                pack_unit=product.pack_unit.value,
                price_pence=product.price_pence,
                unit_price_pence=as_pence(mine),
                price_source=source.value if source is not None else None,
                is_preferred=product.is_preferred,
                moq_packs=product.moq_packs,
                last_seen_price_at=product.last_seen_price_at,
                vs_best_other=vs,
            )
        )
    return out


def supplier_products_view(session: Session, *, supplier_id: int) -> SupplierProductsResponse:
    row = session.get(Supplier, supplier_id)
    if row is None:
        raise LookupError(f"supplier {supplier_id} not found")
    products = list(
        session.scalars(
            select(SupplierProduct)
            .where(
                SupplierProduct.supplier_id == supplier_id, SupplierProduct.archived_at.is_(None)
            )
            .order_by(SupplierProduct.id)
        )
    )
    outs = sorted(_product_outs(session, products), key=lambda p: p.ingredient_name.lower())
    options = tuple(
        IngredientOptionOut(ingredient_id=i.id, name=i.name, unit=i.unit.value, category=i.category)
        for i in session.scalars(
            select(Ingredient).where(Ingredient.retired_at.is_(None)).order_by(Ingredient.name)
        )
    )
    return SupplierProductsResponse(
        supplier=_supplier_full(session, row), products=tuple(outs), ingredients=options
    )


def _product_write(
    session: Session, change: suppliers.ProductChange, restarred: str | None = None
) -> ProductWriteOut:
    return ProductWriteOut(
        product=_product_outs(session, [change.product])[0],
        changed=change.changed,
        recosted_items=change.recosted_items,
        price_row_id=change.price_row_id,
        restarred=restarred,
    )


def product_link_view(
    session: Session, *, supplier_id: int, body: ProductLinkIn
) -> ProductWriteOut:
    change = suppliers.link_product(
        session,
        supplier_id=supplier_id,
        ingredient_id=body.ingredient_id,
        sku=body.sku,
        pack_size=parse_qty(body.pack_size, "pack_size"),
        pack_unit=Unit(body.pack_unit),
        price_pence=body.price_pence,
        changed_by=body.changed_by,
    )
    return _product_write(session, change)


def product_patch_view(
    session: Session, *, product_id: int, body: ProductPatchIn
) -> ProductWriteOut:
    change = suppliers.edit_product(
        session,
        product_id=product_id,
        changed_by=body.changed_by,
        sku=body.sku,
        pack_size=None if body.pack_size is None else parse_qty(body.pack_size, "pack_size"),
        pack_unit=None if body.pack_unit is None else Unit(body.pack_unit),
        price_pence=body.price_pence,
    )
    return _product_write(session, change)


def product_prefer_view(
    session: Session, *, product_id: int, body: ProductActorIn
) -> ProductWriteOut:
    change = suppliers.prefer_product(session, product_id=product_id, changed_by=body.changed_by)
    return _product_write(session, change)


def product_archive_view(
    session: Session, *, product_id: int, body: ProductArchiveIn
) -> ProductWriteOut:
    product, promoted = suppliers.archive_product(
        session, product_id=product_id, archived_by=body.archived_by
    )
    change = suppliers.ProductChange(
        product=product, recosted_items=0, price_row_id=None, changed=("archived",)
    )
    return _product_write(session, change, restarred=promoted)
