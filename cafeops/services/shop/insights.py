"""Online-order statistics for the back office (`GET /api/shop-admin/insights`).

Reads only. A window is a run of LOCAL days (`settings.tz`), inclusive; an order belongs
to the day of its `requested_at` (the day it was for, which is also the day the money
arrived), so a late-night order for tomorrow morning sits on tomorrow. The previous
window is the same number of days immediately before, for the "vs previous" figures.

What counts where (owner's rule): **only COLLECTED orders count towards revenue, the
average basket, items, top products and top options.** "Orders" is everything a
customer actually placed (every status but PENDING_PAYMENT); cancelled and rejected are
counted separately, and never added to revenue. A figure with nothing behind it is
`None`, never 0: an average over no orders is not zero, it is unknown.

Money is integer pence throughout; shares are percentages (0-100) with one decimal.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from cafeops.clock import local_day_bounds, local_today, utcnow
from cafeops.config import settings
from cafeops.db.models.shop import (
    DiningOption,
    OrderStatus,
    ShopOrder,
    ShopPaymentMethod,
)

__all__ = [
    "DayPoint",
    "Figures",
    "HourPoint",
    "OptionRow",
    "ProductRow",
    "ShopInsights",
    "VsPrevious",
    "WeekdayPoint",
    "shop_insights",
]

#: Everything a customer actually placed. PENDING_PAYMENT never reached the counter.
PLACED: tuple[OrderStatus, ...] = (
    OrderStatus.NEW,
    OrderStatus.ACCEPTED,
    OrderStatus.PREPARING,
    OrderStatus.READY,
    OrderStatus.COLLECTED,
    OrderStatus.CANCELLED,
    OrderStatus.REJECTED,
)
DROPPED: tuple[OrderStatus, ...] = (OrderStatus.CANCELLED, OrderStatus.REJECTED)
LIVE: tuple[OrderStatus, ...] = (
    OrderStatus.NEW,
    OrderStatus.ACCEPTED,
    OrderStatus.PREPARING,
    OrderStatus.READY,
)
TOP_PRODUCTS = 12
TOP_OPTIONS = 10


@dataclass(frozen=True, slots=True)
class VsPrevious:
    orders_pct: float | None
    revenue_pct: float | None
    avg_basket_pct: float | None


@dataclass(frozen=True, slots=True)
class Figures:
    orders: int
    revenue_pence: int
    avg_basket_pence: int | None
    items: int
    members_share: float | None
    online_paid_share: float | None
    cancelled: int
    rejected: int
    avg_minutes_to_ready: float | None
    vs_previous: VsPrevious


@dataclass(frozen=True, slots=True)
class DayPoint:
    date: date
    orders: int
    revenue_pence: int
    cancelled: int


@dataclass(frozen=True, slots=True)
class HourPoint:
    hour: int
    orders: int


@dataclass(frozen=True, slots=True)
class WeekdayPoint:
    weekday: int
    orders: int
    revenue_pence: int


@dataclass(frozen=True, slots=True)
class ProductRow:
    product_id: int | None
    name: str
    qty: int
    revenue_pence: int


@dataclass(frozen=True, slots=True)
class OptionRow:
    group: str
    name: str
    qty: int


@dataclass(frozen=True, slots=True)
class ShopInsights:
    since: date
    until: date
    days: int
    prev_since: date
    prev_until: date
    figures: Figures
    per_day: list[DayPoint]
    by_hour: list[HourPoint]
    by_weekday: list[WeekdayPoint]
    top_products: list[ProductRow]
    top_options: list[OptionRow]
    dining_takeaway: int
    dining_eat_in: int
    payment_counter: int
    payment_online: int
    status_new: int
    status_accepted: int
    status_preparing: int
    status_ready: int
    caveats: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _local(at: datetime) -> datetime:
    return at.astimezone(settings.tz)


def _pct(now: float, before: float) -> float | None:
    if before <= 0:
        return None
    return round((now - before) / before * 100, 1)


def _share(part: int, whole: int) -> float | None:
    if whole <= 0:
        return None
    return round(part / whole * 100, 1)


@dataclass(slots=True)
class _Window:
    placed: list[ShopOrder]
    collected: list[ShopOrder]

    @property
    def revenue(self) -> int:
        return sum(o.total_pence for o in self.collected)

    @property
    def avg_basket(self) -> int | None:
        if not self.collected:
            return None
        # Integer pence, exact (invariant 11). Half-even, which is what the `round(float)`
        # this replaced did, so the figure is unchanged.
        mean = Decimal(self.revenue) / len(self.collected)
        return int(mean.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))


def _window(orders: Iterable[ShopOrder]) -> _Window:
    placed = [o for o in orders if o.status in PLACED]
    collected = [o for o in placed if o.status is OrderStatus.COLLECTED]
    return _Window(placed=placed, collected=collected)


# --------------------------------------------------------------------------
# the read
# --------------------------------------------------------------------------


def resolve_window(
    *, since: date | None, until: date | None, days: int, today: date | None = None
) -> tuple[date, date]:
    """`from`/`to` win; otherwise the last `days` days ending today (local)."""
    today = today or local_today(settings.tz)
    if since is not None and until is not None:
        if until < since:
            since, until = until, since
        return since, until
    if until is None:
        until = today if since is None else since + timedelta(days=max(1, days) - 1)
    if since is None:
        since = until - timedelta(days=max(1, days) - 1)
    return since, until


def shop_insights(
    session: Session,
    *,
    since: date | None = None,
    until: date | None = None,
    days: int = 30,
    now: datetime | None = None,
) -> ShopInsights:
    now = now or utcnow()
    since, until = resolve_window(since=since, until=until, days=days, today=_local(now).date())
    length = (until - since).days + 1
    prev_until = since - timedelta(days=1)
    prev_since = prev_until - timedelta(days=length - 1)

    start, end = local_day_bounds(prev_since, until, tz=settings.tz)
    rows = list(
        session.scalars(
            select(ShopOrder)
            .where(ShopOrder.requested_at >= start, ShopOrder.requested_at < end)
            .options(selectinload(ShopOrder.lines))
            .order_by(ShopOrder.requested_at, ShopOrder.id)
        )
    )
    cut = local_day_bounds(since, tz=settings.tz)[0]
    current = _window(o for o in rows if o.requested_at >= cut)
    previous = _window(o for o in rows if o.requested_at < cut)

    # --- figures -------------------------------------------------------------
    ready_minutes = [
        (o.ready_at - o.placed_at).total_seconds() / 60
        for o in current.placed
        if o.ready_at is not None and o.ready_at >= o.placed_at
    ]
    n_placed = len(current.placed)
    figures = Figures(
        orders=n_placed,
        revenue_pence=current.revenue,
        avg_basket_pence=current.avg_basket,
        items=sum(line.qty for o in current.collected for line in o.lines),
        members_share=_share(sum(1 for o in current.placed if o.member_id is not None), n_placed),
        online_paid_share=_share(
            sum(1 for o in current.placed if o.payment_method is ShopPaymentMethod.ONLINE),
            n_placed,
        ),
        cancelled=sum(1 for o in current.placed if o.status is OrderStatus.CANCELLED),
        rejected=sum(1 for o in current.placed if o.status is OrderStatus.REJECTED),
        avg_minutes_to_ready=(
            round(sum(ready_minutes) / len(ready_minutes), 1) if ready_minutes else None
        ),
        vs_previous=VsPrevious(
            orders_pct=_pct(n_placed, len(previous.placed)) if previous.placed else None,
            revenue_pct=_pct(current.revenue, previous.revenue) if previous.placed else None,
            avg_basket_pct=(
                _pct(current.avg_basket, previous.avg_basket)
                if previous.placed
                and previous.avg_basket is not None
                and current.avg_basket is not None
                else None
            ),
        ),
    )

    # --- per day, hour, weekday ---------------------------------------------------
    day_orders: dict[date, int] = defaultdict(int)
    day_revenue: dict[date, int] = defaultdict(int)
    day_dropped: dict[date, int] = defaultdict(int)
    hour_orders: dict[int, int] = defaultdict(int)
    wd_orders: dict[int, int] = defaultdict(int)
    wd_revenue: dict[int, int] = defaultdict(int)
    for o in current.placed:
        local = _local(o.requested_at)
        day_orders[local.date()] += 1
        hour_orders[local.hour] += 1
        wd_orders[local.weekday()] += 1
        if o.status in DROPPED:
            day_dropped[local.date()] += 1
        if o.status is OrderStatus.COLLECTED:
            day_revenue[local.date()] += o.total_pence
            wd_revenue[local.weekday()] += o.total_pence
    per_day = [
        DayPoint(
            date=d,
            orders=day_orders.get(d, 0),
            revenue_pence=day_revenue.get(d, 0),
            cancelled=day_dropped.get(d, 0),
        )
        for d in (since + timedelta(days=n) for n in range(length))
    ]
    by_hour = [HourPoint(hour=h, orders=hour_orders.get(h, 0)) for h in range(24)]
    by_weekday = [
        WeekdayPoint(weekday=w, orders=wd_orders.get(w, 0), revenue_pence=wd_revenue.get(w, 0))
        for w in range(7)
    ]

    # --- top products and options (COLLECTED only) --------------------------------
    prod_qty: dict[tuple[int | None, str], int] = defaultdict(int)
    prod_rev: dict[tuple[int | None, str], int] = defaultdict(int)
    opt_qty: dict[tuple[str, str], int] = defaultdict(int)
    for o in current.collected:
        for line in o.lines:
            key = (line.product_id, line.name)
            prod_qty[key] += line.qty
            prod_rev[key] += line.line_total_pence
            for opt in line.options:
                opt_qty[(str(opt.get("group", "")), str(opt.get("name", "")))] += line.qty
    top_products = sorted(
        (
            ProductRow(product_id=pid, name=name, qty=prod_qty[(pid, name)], revenue_pence=rev)
            for (pid, name), rev in prod_rev.items()
        ),
        key=lambda p: (-p.revenue_pence, -p.qty, p.name),
    )[:TOP_PRODUCTS]
    top_options = sorted(
        (OptionRow(group=g, name=n, qty=q) for (g, n), q in opt_qty.items()),
        key=lambda r: (-r.qty, r.group, r.name),
    )[:TOP_OPTIONS]

    # --- status now (not windowed) ------------------------------------------------
    live: dict[OrderStatus, int] = defaultdict(int)
    for status in session.scalars(select(ShopOrder.status).where(ShopOrder.status.in_(LIVE))):
        live[status] += 1

    caveats: list[str] = []
    if not current.placed:
        caveats.append("No online orders in this window.")
    elif not current.collected:
        caveats.append("Orders were placed but none collected yet: revenue is unknown, not zero.")

    return ShopInsights(
        since=since,
        until=until,
        days=length,
        prev_since=prev_since,
        prev_until=prev_until,
        figures=figures,
        per_day=per_day,
        by_hour=by_hour,
        by_weekday=by_weekday,
        top_products=top_products,
        top_options=top_options,
        dining_takeaway=sum(1 for o in current.placed if o.dining is DiningOption.TAKEAWAY),
        dining_eat_in=sum(1 for o in current.placed if o.dining is DiningOption.EAT_IN),
        payment_counter=sum(
            1 for o in current.placed if o.payment_method is ShopPaymentMethod.COUNTER
        ),
        payment_online=sum(
            1 for o in current.placed if o.payment_method is ShopPaymentMethod.ONLINE
        ),
        status_new=live.get(OrderStatus.NEW, 0),
        status_accepted=live.get(OrderStatus.ACCEPTED, 0),
        status_preparing=live.get(OrderStatus.PREPARING, 0),
        status_ready=live.get(OrderStatus.READY, 0),
        caveats=caveats,
    )
