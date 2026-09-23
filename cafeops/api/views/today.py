"""The "today" summary. Spec 10's today/money screen, read-only.

Spec 10 is explicit that *"nothing in it is urgent -- it should not look like an
operations console with live tiles"*. So this is deliberately not a KPI feed. It is the
set of things that would otherwise require opening four other screens to discover, each
with the sentence that says what to do about it, and severity is `act` only where a
threshold has actually been crossed -- the same rule as *"colour only for crossed
thresholds"*.

The draft-order figures are optional and off by default (`with_orders`). A full ordering
run is several seconds of forecasting across eight suppliers, and a summary screen that
takes five seconds to paint is a summary screen nobody opens. When it is asked for,
nothing is written -- see `views/orders.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from cafeops.api.encoding import as_pence
from cafeops.api.schemas import TodayAlert, TodayResponse
from cafeops.api.views.orders import draft_orders_view
from cafeops.api.views.stock import SHORT_DATED_DAYS, stock_view
from cafeops.config import settings
from cafeops.db.repositories.batch import SqlBatchRepository
from cafeops.domain.composition import Availability
from cafeops.domain.types import Tier
from cafeops.services.menu_margin import menu_availability

__all__ = ["today_view"]


def today_view(
    session: Session,
    *,
    as_of: datetime | None = None,
    with_orders: bool = False,
) -> TodayResponse:
    at = as_of or datetime.now(UTC)
    local = at.astimezone(settings.tz).date()

    stock = stock_view(session, as_of=at, tiers=(Tier.A, Tier.B), with_run_out=False)
    rows = stock.rows

    short_dated = tuple(
        f"{row.name} ({row.soonest_expiry_days}d)"
        for row in sorted(
            (r for r in rows if r.is_short_dated),
            key=lambda r: r.soonest_expiry_days if r.soonest_expiry_days is not None else 0,
        )
    )
    forced = tuple(row.name for row in rows if row.drift.verdict == "FORCE_MANUAL")
    tuning = tuple(row.name for row in rows if row.drift.verdict == "TUNE_WASTE_FACTOR")

    batches = SqlBatchRepository(session)
    losses = batches.expiry_losses_due(at=at)
    loss_value: Decimal | None = Decimal("0")
    for loss in losses:
        pence = loss.loss_pence
        if pence is None:
            # Invariant 8 again: a write-off total that omits an unpriced batch
            # understates the waste it exists to report.
            loss_value = None
            break
        if loss_value is not None:
            loss_value += pence

    unavailable = tuple(
        answer.label
        for answer in menu_availability(session, on=local, unavailable_only=True)
        if answer.availability is not Availability.INACTIVE
    )

    draft_total: int | None = None
    supplier_count: int | None = None
    capped: int | None = None
    emergency: int | None = None
    order_notes: tuple[str, ...] = ()
    if with_orders:
        orders = draft_orders_view(session, order_date=local)
        draft_total = orders.total_pence
        supplier_count = sum(1 for order in orders.suppliers if order.lines)
        capped = orders.capped_line_count
        emergency = len(orders.emergency)
        order_notes = orders.notes

    alerts: list[TodayAlert] = []
    if forced:
        alerts.append(
            TodayAlert(
                kind="drift",
                severity="act",
                subject=", ".join(forced),
                message=(
                    f"{len(forced)} ingredient(s) are over 15% drift, so auto-ordering is "
                    "forced off and every quantity sized from their on-hand is wrong from "
                    "now (spec 5.2). Count them, then read the attribution on the stock "
                    "screen: over-ordering and a bad recipe have opposite fixes."
                ),
            )
        )
    if tuning:
        alerts.append(
            TodayAlert(
                kind="drift",
                severity="watch",
                subject=", ".join(tuning),
                message=(
                    f"{len(tuning)} ingredient(s) are in the 10-15% tuning band: a "
                    "waste_factor change is proposed and they stay manual until two "
                    "consecutive counts come in under 10% (invariant 2)."
                ),
            )
        )
    if short_dated:
        alerts.append(
            TodayAlert(
                kind="expiry",
                severity="act",
                subject=", ".join(short_dated[:6]),
                message=(
                    f"{len(short_dated)} ingredient(s) hold stock expiring within "
                    f"{SHORT_DATED_DAYS} days. Still sellable -- this is the window in which "
                    "it can still be sold through rather than binned."
                ),
            )
        )
    if losses:
        alerts.append(
            TodayAlert(
                kind="expiry",
                severity="act",
                subject=None,
                message=(
                    f"{len(losses)} batch(es) have reached expiry with stock left and are not "
                    "yet written off"
                    + (
                        f", worth GBP {(loss_value or Decimal('0')) / 100:.2f}"
                        if loss_value is not None
                        else " (value unknown -- at least one is unpriced)"
                    )
                    + ". Run `cafeops expiry-sweep --commit`. This figure is the argument "
                    "that the cover window is too long, not that the recipe is wrong."
                ),
            )
        )
    if stock.summary.negative:
        alerts.append(
            TodayAlert(
                kind="stock",
                severity="act",
                subject=None,
                message=(
                    f"{stock.summary.negative} ingredient(s) show negative theoretical "
                    "on-hand: the ledger has consumed more than the last count recorded."
                ),
            )
        )
    if capped:
        alerts.append(
            TodayAlert(
                kind="ordering",
                severity="info",
                subject=None,
                message=(
                    f"{capped} draft order line(s) were capped by shelf life or a season "
                    "(invariant 4). Each carries its cap_reason. Raising one recreates "
                    "exactly the waste the cap prevents -- order again sooner instead."
                ),
            )
        )
    if emergency:
        alerts.append(
            TodayAlert(
                kind="ordering",
                severity="watch",
                subject=None,
                message=(
                    f"{emergency} item(s) cannot wait for a scheduled delivery and are routed "
                    "to Tesco at retail. One is a bad week; a pattern is a broken ordering "
                    "cadence, and the premium is logged so it can be summed over a quarter."
                ),
            )
        )
    if unavailable:
        alerts.append(
            TodayAlert(
                kind="menu",
                severity="info",
                subject=", ".join(unavailable[:6]),
                message=(
                    f"{len(unavailable)} menu item(s) should not be offered today -- out of "
                    "season. Their recipes still resolve and past sales still depleted what "
                    "they depleted; only today's menu is in question."
                ),
            )
        )

    quality: list[TodayAlert] = []
    if stock.summary.unanchored:
        # Only when there are some. "0 ingredients have no count" is noise, and a screen
        # of satisfied warnings is how the real ones stop being read.
        quality.append(
            TodayAlert(
                kind="data_quality",
                severity="watch",
                subject=None,
                message=(
                    f"{stock.summary.unanchored} tracked ingredient(s) have no physical "
                    "count behind their on-hand figure. That number is a bare movement sum "
                    "with no anchor and is not good enough to order against (invariant 6)."
                ),
            )
        )
    quality.append(
        TodayAlert(
            kind="data_quality",
            severity="watch",
            subject=None,
            message=(
                "42 of 113 ingredient prices are ESTIMATE and all 113 shelf lives are "
                "ESTIMATE defaults. Prices make every margin an estimate; shelf lives cap "
                "order size, so a wrong one either wastes stock or causes a stockout. The "
                "~15 perishables that actually move are worth confirming first "
                "(ARCHITECTURE 8F.1, 11.1b)."
            ),
        )
    )

    notes = [
        "Every stock figure on this response is THEORETICAL (invariant 6). Physical counts "
        "are the source of truth.",
    ]
    if not with_orders:
        notes.append(
            "Draft-order figures are omitted: pass with_orders=true to compute them. A full "
            "ordering run forecasts every tracked ingredient across eight suppliers, which "
            "is seconds rather than milliseconds."
        )
    notes.extend(order_notes)

    return TodayResponse(
        as_of=at,
        local_date=local,
        stock=stock.summary,
        short_dated=short_dated,
        expiry_write_offs_due=len(losses),
        expiry_write_offs_value_pence=as_pence(loss_value),
        drift_forced_manual=forced,
        drift_tuning_band=tuning,
        auto_order_enabled_count=stock.summary.auto_order_enabled,
        draft_order_total_pence=draft_total,
        draft_order_supplier_count=supplier_count,
        capped_line_count=capped,
        emergency_line_count=emergency,
        unavailable_menu_items=unavailable,
        data_quality=tuple(quality),
        alerts=tuple(alerts),
        notes=tuple(notes),
    )
