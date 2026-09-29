"""Money -> Sales: what the till sold, sliced for the dashboard. Read-only.

Source: Lightspeed receipt lines in `sale` (one row per line, several per receipt).
This is a different ledger from the daily takings (`trading_day` / `payment_day`):
takings say how much money came in and how it was paid; these lines say *what* was
sold, *when* in the day, and through which channel. The two are never added
together, and the dashboard labels each figure with its source.

Rules:

- Voided lines are left out of every figure. A receipt whose lines are all voided
  does not count as a receipt.
- A refund line carries a negative qty and gross, so sums net out on their own.
- A day inside the window with no lines is `None`, not 0: the till may have been
  closed, or not synced. Nothing here can tell those apart (invariant 8).
- Dates and hours are Europe/London business time (`settings.tz`).
- The previous period is the same number of days immediately before `since`,
  under the same filters, so "vs previous" compares like with like.

Money is integer pence. Quantities are `Decimal`, rendered as strings on the wire.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models.enums import SaleChannel
from cafeops.db.models.menu import MenuItem
from cafeops.db.models.sale import Sale
from cafeops.services.finance.common import FinanceRefused

__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "UNCATEGORISED",
    "SalesInsights",
    "sales_insights",
]

DEFAULT_WINDOW_DAYS = 30
MAX_WINDOW_DAYS = 3660
#: The category filter value for products with no category set.
UNCATEGORISED = "__none__"
#: Receipt ids the demo seed writes. Real Lightspeed ids never start with this.
DEMO_PREFIX = "DEMO-"
_SIZE_ORDER = ("S", "M", "XL", "ONE")
_BASKET_CAP = 4  # 1, 2, 3, "4+"


@dataclass(frozen=True, slots=True)
class Totals:
    gross_pence: int
    receipts: int
    items: Decimal
    trading_days: int
    refund_receipts: int
    avg_basket_pence: int | None
    items_per_basket: Decimal | None
    per_day_pence: int | None


@dataclass(frozen=True, slots=True)
class DayPoint:
    date: date
    gross_pence: int | None
    receipts: int


@dataclass(frozen=True, slots=True)
class HourPoint:
    hour: int
    gross_pence: int
    receipts: int


@dataclass(frozen=True, slots=True)
class HeatCell:
    weekday: int
    hour: int
    receipts: int
    gross_pence: int


@dataclass(frozen=True, slots=True)
class WeekdayPoint:
    weekday: int
    gross_pence: int
    receipts: int
    trading_days: int


@dataclass(frozen=True, slots=True)
class Share:
    key: str
    gross_pence: int
    qty: Decimal
    receipts: int


@dataclass(frozen=True, slots=True)
class ProductRow:
    name: str
    category: str | None
    gross_pence: int
    qty: Decimal
    receipts: int
    sizes: dict[str, Decimal]


@dataclass(frozen=True, slots=True)
class BasketBin:
    items: str
    receipts: int


@dataclass(frozen=True, slots=True)
class Options:
    channels: list[str]
    categories: list[str]
    products: list[str]
    sizes: list[str]


@dataclass(frozen=True, slots=True)
class SalesInsights:
    since: date
    until: date
    prev_since: date
    prev_until: date
    first_sale_date: date | None
    last_sale_date: date | None
    months: list[str]
    is_demo: bool
    totals: Totals
    previous: Totals | None
    by_day: list[DayPoint]
    by_hour: list[HourPoint]
    heat: list[HeatCell]
    by_weekday: list[WeekdayPoint]
    by_channel: list[Share]
    by_category: list[Share]
    by_size: list[Share]
    products: list[ProductRow]
    baskets: list[BasketBin]
    options: Options
    caveats: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Line:
    receipt: str
    at: datetime  # local time
    channel: str
    name: str
    category: str | None
    size: str
    qty: Decimal
    gross: int


def _load(session: Session, since: date, until: date) -> list[_Line]:
    tz = settings.tz
    stmt = (
        select(Sale, MenuItem.name, MenuItem.category, MenuItem.size_code)
        .join(MenuItem, MenuItem.id == Sale.menu_item_id)
        .where(Sale.voided.is_(False))
        .where(Sale.sold_at >= datetime.combine(since, time.min, tzinfo=tz))
        .where(Sale.sold_at < datetime.combine(until + timedelta(days=1), time.min, tzinfo=tz))
    )
    out: list[_Line] = []
    for sale, name, category, size in session.execute(stmt):
        out.append(
            _Line(
                receipt=sale.lightspeed_receipt_id,
                at=sale.sold_at.astimezone(tz),
                channel=sale.channel.value,
                name=name,
                category=(category or "").strip() or None,
                size=size.value if size is not None else "ONE",
                qty=sale.qty,
                gross=sale.gross_pence,
            )
        )
    return out


def _keep(
    line: _Line,
    *,
    channel: str | None,
    category: str | None,
    product: str | None,
    size: str | None,
    weekdays: frozenset[int] | None,
) -> bool:
    if channel is not None and line.channel != channel:
        return False
    if category is not None:
        if category == UNCATEGORISED:
            if line.category is not None:
                return False
        elif line.category != category:
            return False
    if product is not None and line.name != product:
        return False
    if size is not None and line.size != size:
        return False
    return weekdays is None or line.at.weekday() in weekdays


def _half_up(num: int, den: int) -> int:
    """Integer division rounding half away from zero, for money."""
    q = (abs(num) * 2 + den) // (den * 2)
    return q if num >= 0 else -q


def _totals(lines: list[_Line]) -> Totals:
    receipts: dict[str, int] = defaultdict(int)
    refunds: set[str] = set()
    days: set[date] = set()
    items = Decimal(0)
    for ln in lines:
        receipts[ln.receipt] += ln.gross
        items += ln.qty
        days.add(ln.at.date())
        if ln.qty < 0:
            refunds.add(ln.receipt)
    gross = sum(receipts.values())
    n = len(receipts)
    return Totals(
        gross_pence=gross,
        receipts=n,
        items=items,
        trading_days=len(days),
        refund_receipts=len(refunds),
        avg_basket_pence=_half_up(gross, n) if n else None,
        items_per_basket=(items / n).quantize(Decimal("0.01")) if n else None,
        per_day_pence=_half_up(gross, len(days)) if days else None,
    )


def _shares(lines: list[_Line], key: str) -> list[Share]:
    gross: dict[str, int] = defaultdict(int)
    qty: dict[str, Decimal] = defaultdict(Decimal)
    rec: dict[str, set[str]] = defaultdict(set)
    for ln in lines:
        k = getattr(ln, key)
        k = UNCATEGORISED if k is None else str(k)
        gross[k] += ln.gross
        qty[k] += ln.qty
        rec[k].add(ln.receipt)
    rows = [Share(key=k, gross_pence=gross[k], qty=qty[k], receipts=len(rec[k])) for k in gross]
    if key == "size":
        rows.sort(key=lambda s: _SIZE_ORDER.index(s.key) if s.key in _SIZE_ORDER else 99)
    else:
        rows.sort(key=lambda s: (-s.gross_pence, s.key))
    return rows


def sales_insights(
    session: Session,
    *,
    since: date | None = None,
    until: date | None = None,
    channel: str | None = None,
    category: str | None = None,
    product: str | None = None,
    size: str | None = None,
    weekdays: frozenset[int] | None = None,
    whole: bool = False,
) -> SalesInsights:
    """Default window: the 30 days up to the last sale. `whole`: first sale to last."""
    if channel is not None:
        try:
            channel = SaleChannel[channel.upper()].value
        except KeyError as exc:
            raise FinanceRefused("channel: EPOS, CASH, DELIVEROO, JUST_EAT, WEB or OTHER") from exc
    if size is not None and size.upper() not in _SIZE_ORDER:
        raise FinanceRefused("size: S, M, XL or ONE")
    size = size.upper() if size is not None else None
    if weekdays is not None and not weekdays <= frozenset(range(7)):
        raise FinanceRefused("weekdays: 0 (Monday) to 6 (Sunday)")

    tz = settings.tz
    first_at, last_at = session.execute(
        select(func.min(Sale.sold_at), func.max(Sale.sold_at)).where(Sale.voided.is_(False))
    ).one()
    first = first_at.astimezone(tz).date() if first_at is not None else None
    last = last_at.astimezone(tz).date() if last_at is not None else None

    if whole and first is not None and last is not None:
        since, until = first, last
    # Default: the 30 days up to the last sale, so a quiet week still shows data.
    if until is None:
        until = last if last is not None else datetime.now(tz).date()
    if since is None:
        since = until - timedelta(days=DEFAULT_WINDOW_DAYS - 1)
    if since > until:
        raise FinanceRefused("from: must not be after to")
    span = (until - since).days + 1
    if span > MAX_WINDOW_DAYS:
        raise FinanceRefused(f"window: at most {MAX_WINDOW_DAYS} days")
    prev_until = since - timedelta(days=1)
    prev_since = prev_until - timedelta(days=span - 1)

    both = _load(session, prev_since, until)
    window_all = [ln for ln in both if ln.at.date() >= since]

    def keep(ln: _Line) -> bool:
        return _keep(
            ln, channel=channel, category=category, product=product, size=size, weekdays=weekdays
        )

    now = [ln for ln in window_all if keep(ln)]
    before = [ln for ln in both if ln.at.date() < since and keep(ln)]

    # by day: every date in the window, None where nothing was rung up
    day_gross: dict[date, int] = defaultdict(int)
    day_rec: dict[date, set[str]] = defaultdict(set)
    for ln in now:
        d = ln.at.date()
        day_gross[d] += ln.gross
        day_rec[d].add(ln.receipt)
    by_day = [
        DayPoint(
            date=since + timedelta(days=i),
            gross_pence=day_gross.get(since + timedelta(days=i)),
            receipts=len(day_rec.get(since + timedelta(days=i), ())),
        )
        for i in range(span)
    ]

    # A receipt belongs to the hour and weekday of its first line.
    first_line: dict[str, datetime] = {}
    rec_gross: dict[str, int] = defaultdict(int)
    rec_items: dict[str, Decimal] = defaultdict(Decimal)
    for ln in now:
        if ln.receipt not in first_line or ln.at < first_line[ln.receipt]:
            first_line[ln.receipt] = ln.at
        rec_gross[ln.receipt] += ln.gross
        rec_items[ln.receipt] += ln.qty
    hour_g: dict[int, int] = defaultdict(int)
    hour_r: dict[int, int] = defaultdict(int)
    heat_g: dict[tuple[int, int], int] = defaultdict(int)
    heat_r: dict[tuple[int, int], int] = defaultdict(int)
    wd_g: dict[int, int] = defaultdict(int)
    wd_r: dict[int, int] = defaultdict(int)
    for rid, at in first_line.items():
        hour_g[at.hour] += rec_gross[rid]
        hour_r[at.hour] += 1
        heat_g[(at.weekday(), at.hour)] += rec_gross[rid]
        heat_r[(at.weekday(), at.hour)] += 1
        wd_g[at.weekday()] += rec_gross[rid]
        wd_r[at.weekday()] += 1
    wd_days: dict[int, int] = defaultdict(int)
    for d in day_gross:
        wd_days[d.weekday()] += 1
    hours = sorted(hour_r)
    by_hour = (
        [
            HourPoint(hour=h, gross_pence=hour_g.get(h, 0), receipts=hour_r.get(h, 0))
            for h in range(hours[0], hours[-1] + 1)
        ]
        if hours
        else []
    )
    heat = [
        HeatCell(weekday=w, hour=h, receipts=heat_r[(w, h)], gross_pence=heat_g[(w, h)])
        for (w, h) in sorted(heat_r)
    ]
    by_weekday = [
        WeekdayPoint(
            weekday=w,
            gross_pence=wd_g.get(w, 0),
            receipts=wd_r.get(w, 0),
            trading_days=wd_days.get(w, 0),
        )
        for w in range(7)
    ]

    baskets_n: dict[int, int] = defaultdict(int)
    for items in rec_items.values():
        n = int(items.to_integral_value())
        if n >= 1:
            baskets_n[min(n, _BASKET_CAP)] += 1
    baskets = [
        BasketBin(items=f"{n}+" if n == _BASKET_CAP else str(n), receipts=baskets_n.get(n, 0))
        for n in range(1, _BASKET_CAP + 1)
    ]

    prod_g: dict[str, int] = defaultdict(int)
    prod_q: dict[str, Decimal] = defaultdict(Decimal)
    prod_r: dict[str, set[str]] = defaultdict(set)
    prod_c: dict[str, str | None] = {}
    prod_s: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for ln in now:
        prod_g[ln.name] += ln.gross
        prod_q[ln.name] += ln.qty
        prod_r[ln.name].add(ln.receipt)
        prod_c.setdefault(ln.name, ln.category)
        prod_s[ln.name][ln.size] += ln.qty
    products = sorted(
        (
            ProductRow(
                name=n,
                category=prod_c[n],
                gross_pence=prod_g[n],
                qty=prod_q[n],
                receipts=len(prod_r[n]),
                sizes=dict(sorted(prod_s[n].items(), key=lambda kv: _SIZE_ORDER.index(kv[0]))),
            )
            for n in prod_g
        ),
        key=lambda p: (-p.gross_pence, p.name),
    )

    # Options come from the whole window, unfiltered, so picking one filter
    # never empties another's list.
    options = Options(
        channels=sorted({ln.channel for ln in window_all}),
        categories=sorted({ln.category or UNCATEGORISED for ln in window_all}),
        products=sorted({ln.name for ln in window_all}),
        sizes=[s for s in _SIZE_ORDER if any(ln.size == s for ln in window_all)],
    )

    months = _months(first, last)

    caveats: list[str] = []
    is_demo = bool(window_all) and all(ln.receipt.startswith(DEMO_PREFIX) for ln in window_all)
    if is_demo:
        caveats.append(
            "These till lines are the demo seed, not real Lightspeed sales. Connect Lightspeed "
            "(or run `cafeops sync`) to replace them."
        )
    if not window_all:
        caveats.append("No till lines in this window.")
    if options.channels == ["EPOS"]:
        caveats.append(
            "Only in-store till sales in this window. Deliveroo and Just Eat orders reach the "
            "till only once the apps are linked to Lightspeed; their monthly statements are "
            "under Takings."
        )

    return SalesInsights(
        since=since,
        until=until,
        prev_since=prev_since,
        prev_until=prev_until,
        first_sale_date=first,
        last_sale_date=last,
        months=months,
        is_demo=is_demo,
        totals=_totals(now),
        previous=_totals(before) if before else None,
        by_day=by_day,
        by_hour=by_hour,
        heat=heat,
        by_weekday=by_weekday,
        by_channel=_shares(now, "channel"),
        by_category=_shares(now, "category"),
        by_size=_shares(now, "size"),
        products=products,
        baskets=baskets,
        options=options,
        caveats=caveats,
    )


def _months(first: date | None, last: date | None) -> list[str]:
    """Every YYYY-MM from the first sale to the last, for the period picker."""
    if first is None or last is None:
        return []
    out: list[str] = []
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out
