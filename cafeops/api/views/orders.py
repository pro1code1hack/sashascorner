"""Draft orders, grouped by supplier. Spec 4.4, 5.4, 5.5 -- and invariants 1, 4, 5, 9.

**This endpoint computes an ordering run and persists nothing.** `build_split` is the
same service the CLI's `simulate` and the ordering job call, but `create_draft_po` is
never reached from here: invariant 1 says nothing is ordered without human
confirmation, and confirmation happens in Telegram. There is no endpoint anywhere in
this API that creates, confirms or sends a purchase order, and `writes_nothing: true`
on the response says so in the payload rather than only in a docstring.

Four things must survive the trip to the screen or the quantities become dangerous:

* **`cap_reason`** (invariant 4). A line the shelf-life or season cap shortened carries
  the phrase, and `cap_detail` carries the arithmetic. Spec 5.4: *"Otherwise the user
  overrides it and creates the waste the cap prevented."*
* **`terms_are_placeholders`**. Six of eight suppliers' lead times, delivery days,
  cutoffs and minimums are invented (ARCHITECTURE 8F.4), and every cover window is built
  on them.
* **The forecast, withheld when low-confidence** (invariant 9). `domain/ordering.py`
  withholds the need quantity with it, because need is the forecast minus two numbers
  already on screen.
* **The sourcing trade-off** (spec 4.4). A cheaper option not taken is reported with the
  saving forgone, never resolved silently.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.api.encoding import as_pence, as_qty
from cafeops.api.schemas import (
    DraftOrdersResponse,
    EmergencyLineOut,
    Forecast,
    NamedIngredientOut,
    OrderLineOut,
    PersistedLineOut,
    PersistedOrderOut,
    SkippedOut,
    SourcingChoiceOut,
    SupplierOrderOut,
    SupplierOut,
    TopUpCandidateOut,
)
from cafeops.api.views.common import forecast_out
from cafeops.config import settings
from cafeops.db.models import (
    Ingredient,
    POLine,
    POStatus,
    PurchaseOrder,
    Supplier,
    SupplierProduct,
)
from cafeops.db.repositories.ingredient import SqlIngredientRepository
from cafeops.db.repositories.sourcing import SqlSourcingRepository
from cafeops.domain.ordering import SizingOutcome, SizingPlan, cap_note, terms_of, top_up_pool
from cafeops.domain.types import EmergencyLine, SourcingChoice, SupplierTerms, Tier
from cafeops.services.build_order import SplitResult, build_split
from cafeops.services.suppliers import contact_details

__all__ = ["draft_orders_view", "persisted_order_out", "suppliers_view"]

#: A purchase order still in flight. The pending basket on the draft screen is the one
#: of these matching (supplier, target_delivery_date) -- the pre-delivery job's own
#: idempotency key (spec C14).
OPEN_STATUSES: tuple[POStatus, ...] = (
    POStatus.DRAFT,
    POStatus.PENDING_CONFIRM,
    POStatus.CONFIRMED,
    POStatus.SENT,
)

#: How many top-up suggestions a basket shows (the design's chips).
TOP_UP_CANDIDATES_SHOWN = 4


def _supplier_out(
    terms: SupplierTerms, row: Supplier | None = None, product_count: int | None = None
) -> SupplierOut:
    return SupplierOut(
        kind=row.kind if row is not None else None,
        contact=row.contact if row is not None else None,
        order_url=row.order_url if row is not None else None,
        notes=row.notes if row is not None else None,
        email=contact_details(row)["email"] if row is not None else None,
        phone=contact_details(row)["phone"] if row is not None else None,
        archived=row is not None and row.archived_at is not None,
        product_count=product_count,
        supplier_id=terms.supplier_id,
        name=terms.name,
        lead_time_days=terms.lead_time_days,
        delivery_weekdays=tuple(terms.delivery_weekdays),
        min_order_pence=terms.min_order_pence,
        order_channel=terms.order_channel.value,
        cutoff_time=terms.cutoff_time.isoformat() if terms.cutoff_time is not None else None,
        delivery_fee_pence=terms.delivery_fee_pence,
        free_delivery_threshold_pence=terms.free_delivery_threshold_pence,
        terms_are_placeholders=terms.terms_are_placeholders,
    )


def suppliers_view(session: Session) -> tuple[SupplierOut, ...]:
    """Active suppliers, with their profile and linked-product count. Archived ones are
    "deleted" on the Suppliers screen (spec C12) and do not appear."""
    counts = dict(
        session.execute(
            select(SupplierProduct.supplier_id, func.count(SupplierProduct.id))
            .where(SupplierProduct.archived_at.is_(None))
            .group_by(SupplierProduct.supplier_id)
        ).all()
    )
    rows = {row.id: row for row in session.scalars(select(Supplier))}
    return tuple(
        _supplier_out(terms, rows.get(terms.supplier_id), int(counts.get(terms.supplier_id, 0)))
        for terms in sorted(SqlSourcingRepository(session).all_terms(), key=lambda t: t.name)
    )


def persisted_order_out(session: Session, po: PurchaseOrder) -> PersistedOrderOut:
    """A stored purchase order and its lines, for the draft screen and history."""
    lines: list[PersistedLineOut] = []
    for line, product, ingredient in session.execute(
        select(POLine, SupplierProduct, Ingredient)
        .join(SupplierProduct, SupplierProduct.id == POLine.supplier_product_id)
        .join(Ingredient, Ingredient.id == POLine.ingredient_id)
        .where(POLine.po_id == po.id)
        .order_by(POLine.id)
    ).all():
        lines.append(
            PersistedLineOut(
                po_line_id=line.id,
                ingredient_id=line.ingredient_id,
                ingredient_name=ingredient.name,
                unit=ingredient.unit.value,
                suggested_packs=line.suggested_packs,
                final_packs=line.final_packs,
                unit_price_pence=line.unit_price_pence,
                pack_size=as_qty(product.pack_size) or "0",
                pack_unit=product.pack_unit.value,
                received_qty=as_qty(line.received_qty),
                received_expires_at=line.received_expires_at,
                cap_reason=line.cap_reason,
                is_top_up=line.is_top_up,
                checklist_requested_by=line.checklist_requested_by,
            )
        )
    return PersistedOrderOut(
        po_id=po.id,
        status=po.status.value,
        created_at=po.created_at,
        target_delivery_date=po.target_delivery_date,
        confirmed_by=po.confirmed_by,
        confirmed_at=po.confirmed_at,
        sent_at=po.sent_at,
        sent_by=po.sent_by,
        total_pence=po.total_pence,
        delivery_fee_pence=po.delivery_fee_pence,
        lines=tuple(lines),
    )


def _line_out(outcome: SizingOutcome) -> OrderLineOut:
    line = outcome.line
    if line is None:  # pragma: no cover -- every caller iterates `plan.ordered`
        raise ValueError("_line_out was given an outcome that produced no line")
    candidate = outcome.candidate
    # The line is sized on the EFFECTIVE window, so that is the window reported and
    # `forecast_qty` -- not `forecast.total` -- is its figure. A capped line otherwise
    # shows the full-window sum labelled with the capped day count.
    forecast: Forecast = forecast_out(
        candidate.forecast,
        unit=candidate.unit,
        window_days=candidate.effective_cover_days,
        total=candidate.forecast_qty,
    )
    capped_out = candidate.full_forecast_qty - candidate.forecast_qty
    return OrderLineOut(
        ingredient_id=line.ingredient_id,
        ingredient_name=line.ingredient_name,
        unit=line.unit.value,
        packs=line.packs,
        pack_size=as_qty(line.pack.pack_size) or "0",
        pack_unit=line.pack.pack_unit.value,
        pack_price_pence=line.pack.price_pence,
        line_total_pence=line.line_total_pence,
        sku=line.pack.sku,
        # Invariant 9 again: need, forecast and resulting on-hand are all the forecast
        # plus numbers already on screen, so a withheld forecast withholds them too.
        # `domain/ordering.forecast_text` takes exactly this line.
        need_qty=_withheld_or(line.need_qty, line.low_confidence),
        on_hand_qty=as_qty(line.on_hand_qty) or "0",
        on_open_pos_qty=as_qty(line.on_open_pos_qty) or "0",
        resulting_on_hand_qty=_withheld_or(line.resulting_on_hand, line.low_confidence),
        forecast=forecast,
        cover_days=line.cover_days,
        full_cover_days=candidate.cover.length,
        # Structured, not only in the prose of `cap_detail`: the quantity deliberately
        # NOT ordered is the number that answers "by how much?", and a screen that can
        # only quote a sentence cannot put it next to the packs.
        forecast_full_window_qty=(
            None if line.low_confidence else as_qty(candidate.full_forecast_qty)
        ),
        capped_out_qty=(
            None if line.low_confidence or not candidate.is_capped else as_qty(capped_out)
        ),
        cap_reason=line.cap_reason,
        cap_detail=cap_note(candidate),
        is_capped=outcome.capped or line.cap_reason is not None,
        is_top_up=line.is_top_up,
        clamped=line.clamped,
        cap_kind=line.cap_kind.value if line.cap_kind is not None else None,
        # Invariant 9: the rate is the forecast, so a withheld forecast withholds it.
        daily_rate_qty=(None if line.low_confidence else as_qty(candidate.forecast.base_daily)),
        supplier_product_id=line.pack.supplier_product_id,
    )


def _withheld_or(value: Decimal, low_confidence: bool) -> str:
    """A derived quantity, or the withholding marker when the forecast is withheld.

    Returned as the string `"withheld"` rather than null: null on a required field would
    read as "zero needed" to a careless renderer, and this one must read as an absence.
    """
    return "withheld" if low_confidence else (as_qty(value) or "0")


def _skipped(outcome: SizingOutcome) -> SkippedOut:
    candidate = outcome.candidate
    return SkippedOut(
        ingredient_id=candidate.ingredient_id,
        ingredient_name=candidate.ingredient_name,
        reason=outcome.note or "nothing needed over this cover window",
        below_par_floor=outcome.below_par_floor,
        out_of_season=outcome.out_of_season,
        is_capped=outcome.capped,
        cap_reason=candidate.cap_reason,
        data_error=outcome.data_error,
        clamp_blocked=outcome.clamp_blocked,
    )


def _top_up_candidates(plan: SizingPlan) -> tuple[TopUpCandidateOut, ...]:
    """What could clear a minimum: `domain.ordering.top_up_pool`, the same pool the
    automatic top-up draws from, so it is non-perishable tier B only (invariant 5)."""
    ordered = frozenset(o.candidate.ingredient_id for o in plan.ordered)
    pool = top_up_pool((o.candidate for o in plan.outcomes), ordered)
    out: list[TopUpCandidateOut] = []
    for cover, candidate in pool.eligible[:TOP_UP_CANDIDATES_SHOWN]:
        out.append(
            TopUpCandidateOut(
                ingredient_id=candidate.ingredient_id,
                name=candidate.ingredient_name,
                supplier_product_id=candidate.pack.supplier_product_id,
                pack_size=as_qty(candidate.pack.pack_size) or "0",
                pack_unit=candidate.pack.pack_unit.value,
                pack_price_pence=candidate.pack.price_pence,
                cover_days=as_qty(cover.quantize(Decimal("0.1"))),
            )
        )
    return tuple(out)


def _supplier_order(
    plan: SizingPlan,
    terms: SupplierTerms | None,
    *,
    persisted: PersistedOrderOut | None = None,
) -> SupplierOrderOut:
    suggestion = plan.suggestion
    resolved = terms or terms_of(suggestion.supplier)
    window = suggestion.cover_window
    fee = 0
    if resolved.delivery_fee_pence > 0:
        threshold = resolved.free_delivery_threshold_pence
        if threshold is None or suggestion.total_pence < threshold:
            fee = resolved.delivery_fee_pence

    lines = tuple(_line_out(outcome) for outcome in plan.ordered)
    return SupplierOrderOut(
        supplier=_supplier_out(resolved),
        target_delivery_date=suggestion.target_delivery_date,
        cover_window_days=window.length,
        cover_window_from=window.days[0] if window.days else None,
        cover_window_to=window.days[-1] if window.days else None,
        lead_time_days=window.lead_time_days,
        days_until_next_delivery=window.days_until_next_delivery,
        lines=lines,
        skipped=tuple(
            _skipped(outcome)
            for outcome in plan.outcomes
            if outcome.line is None
            and (
                outcome.below_par_floor
                or outcome.out_of_season
                # A cap that applied and then found nothing to order still explains an
                # absent line, and an unexplained absence is what gets overridden by hand.
                or outcome.capped
                or outcome.data_error is not None
                or outcome.clamp_blocked is not None
            )
        ),
        subtotal_pence=suggestion.total_pence,
        delivery_fee_pence=fee,
        total_pence=suggestion.total_pence + fee,
        meets_minimum=suggestion.meets_minimum,
        min_order_topped_up=suggestion.min_order_topped_up,
        capped_line_count=sum(1 for line in lines if line.is_capped),
        low_confidence_line_count=sum(1 for line in lines if line.forecast.is_low_confidence),
        notes=tuple(suggestion.notes),
        # Matches `jobs/pre_delivery_orders.derive_schedule`: the order day is the
        # delivery day walked back through the lead time.
        order_by_date=suggestion.target_delivery_date - timedelta(days=resolved.lead_time_days),
        next_delivery_date=suggestion.target_delivery_date
        + timedelta(days=window.days_until_next_delivery),
        fee_applies=fee > 0,
        top_up_candidates=_top_up_candidates(plan),
        persisted=persisted,
    )


def _choice_out(
    choice: SourcingChoice,
    *,
    ingredient_names: dict[int, str],
    supplier_names: dict[int, str],
) -> SourcingChoiceOut:
    chosen = choice.chosen
    rejected = choice.cheaper_rejected
    return SourcingChoiceOut(
        ingredient_id=choice.ingredient_id,
        ingredient_name=ingredient_names.get(choice.ingredient_id, str(choice.ingredient_id)),
        chosen_supplier_id=chosen.supplier_id,
        chosen_supplier_name=supplier_names.get(chosen.supplier_id),
        chosen_is_preferred=chosen.is_preferred,
        chosen_unit_price_pence=as_pence(chosen.unit_price_pence),
        reason=choice.reason,
        cheaper_rejected_supplier_id=rejected.supplier_id if rejected is not None else None,
        cheaper_rejected_unit_price_pence=(
            as_pence(rejected.unit_price_pence) if rejected is not None else None
        ),
        forgone_saving_pence=as_pence(choice.forgone_saving_pence),
        alternative_count=len(choice.alternatives),
    )


def _emergency_out(line: EmergencyLine) -> EmergencyLineOut:
    return EmergencyLineOut(
        ingredient_id=line.ingredient_id,
        ingredient_name=line.ingredient_name,
        qty=as_qty(line.qty) or "0",
        unit=line.unit.value,
        reason=line.reason,
        retail_unit_price_pence=as_pence(line.retail_unit_price_pence),
        preferred_unit_price_pence=as_pence(line.preferred_unit_price_pence),
        premium_pence=as_pence(line.premium_pence),
        raw_premium_pence=as_pence(line.raw_premium_pence),
        retail_is_cheaper=line.retail_is_cheaper,
    )


def draft_orders_view(
    session: Session,
    *,
    order_date: date | None = None,
    tiers: tuple[Tier, ...] = (Tier.A, Tier.B),
    reorder_cadence_days: int | None = None,
    require_auto_order: bool = False,
    supplier_ids: tuple[int, ...] | None = None,
    min_order_pence: int | None = None,
) -> DraftOrdersResponse:
    """One ordering run: N drafts, the sourcing choices, and the Tesco list. Writes nothing."""
    computed_at = datetime.now(UTC)
    order_date = order_date or computed_at.astimezone(settings.tz).date()

    result: SplitResult = build_split(
        session,
        order_date=order_date,
        tiers=tiers,
        reorder_cadence_days=reorder_cadence_days,
        require_auto_order=require_auto_order,
        supplier_ids=list(supplier_ids) if supplier_ids is not None else None,
        min_order_pence=min_order_pence,
    )

    sourcing = SqlSourcingRepository(session)
    terms_by_id = sourcing.terms_by_id()
    supplier_names = {sid: terms.name for sid, terms in terms_by_id.items()}
    ingredient_names: dict[int, str] = {}
    for plan in result.plans:
        for outcome in plan.outcomes:
            ingredient_names[outcome.candidate.ingredient_id] = outcome.candidate.ingredient_name
    for line in result.split.emergency:
        ingredient_names[line.ingredient_id] = line.ingredient_name

    open_orders: dict[tuple[int, date], PurchaseOrder] = {}
    for po in session.scalars(
        select(PurchaseOrder)
        .where(PurchaseOrder.status.in_(list(OPEN_STATUSES)))
        .order_by(PurchaseOrder.id)
    ):
        open_orders[(po.supplier_id, po.target_delivery_date)] = po

    def _pending(plan: SizingPlan) -> PersistedOrderOut | None:
        key = (plan.suggestion.supplier.id, plan.suggestion.target_delivery_date)
        po = open_orders.get(key)
        return None if po is None else persisted_order_out(session, po)

    suppliers = tuple(
        _supplier_order(
            plan, terms_by_id.get(plan.suggestion.supplier.id), persisted=_pending(plan)
        )
        for plan in sorted(result.plans, key=lambda p: p.suggestion.supplier.name)
    )

    uncounted: dict[int, str] = {}
    for plan in result.plans:
        for outcome in plan.outcomes:
            if outcome.no_count:
                uncounted[outcome.candidate.ingredient_id] = outcome.candidate.ingredient_name
    tracked = SqlIngredientRepository(session).list_tracked(tiers=tiers)
    options = sourcing.options_for_many([i.id for i in tracked])
    unsourced = [
        NamedIngredientOut(ingredient_id=i.id, name=i.name)
        for i in sorted(tracked, key=lambda i: i.name)
        if not options.get(i.id)
    ]
    return DraftOrdersResponse(
        order_date=order_date,
        computed_at=computed_at,
        reorder_cadence_days=reorder_cadence_days,
        tiers=tuple(tier.value for tier in tiers),
        suppliers=suppliers,
        sourcing_choices=tuple(
            _choice_out(choice, ingredient_names=ingredient_names, supplier_names=supplier_names)
            for choice in sorted(result.split.choices, key=lambda c: c.ingredient_id)
        ),
        emergency=tuple(_emergency_out(line) for line in result.split.emergency),
        emergency_total_premium_pence=as_pence(result.emergency.total_premium_pence),
        emergency_notes=tuple(result.emergency.notes),
        total_pence=sum(order.total_pence for order in suppliers),
        capped_line_count=sum(order.capped_line_count for order in suppliers),
        placeholder_supplier_names=tuple(sourcing.placeholder_suppliers()),
        notes=tuple(result.split.notes),
        uncounted=tuple(
            NamedIngredientOut(ingredient_id=k, name=v)
            for k, v in sorted(uncounted.items(), key=lambda kv: kv[1])
        ),
        unsourced=tuple(unsourced),
        supplier_count=len(terms_by_id),
    )
