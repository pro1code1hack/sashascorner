"""The three numbers spec 4.6 actually asks for. Pure: dataclasses in, dataclasses out.

1. **ROAS** -- attributed revenue over ad spend. In basis points, so no float touches it.
2. **Contribution after commission AND ad spend** -- the marketplace is a channel and
   an ad platform at once, and a "revenue" figure that nets off only commission
   flatters a channel that is buying its own orders.
3. **Items that rank well but convert badly** -- the most actionable output here.
   An item the platform is already putting in front of people, which people then do
   not order, is a photo or a description problem. It is fixable this afternoon for
   nothing, and it is invisible in any revenue-ranked list because a poor converter
   with good placement still shows respectable sales.

Missing data is handled the way invariant 8 requires throughout: a figure nobody
reported is `None`, is excluded from the aggregate, and the exclusion is *counted*
and reported rather than absorbed. `ChannelMetric.net_pence` already sets that
precedent per day -- it returns `None` rather than subtracting what it happens to
have -- and these aggregates keep it.

No SQLAlchemy, no I/O, no config import. The repository assembles the inputs.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from cafeops.domain.types import ChannelSourceKind, SalesChannelName
from cafeops.integrations.channels.base import ChannelDayRow

#: One "unit" of ROAS in basis points. 10_000 bp == 1.0x == break-even on the ad.
ONE_X_BP = 10_000

#: Defaults for the rank/convert report, all overridable at the call site.
DEFAULT_MIN_VIEWS = 100
DEFAULT_RANK_THRESHOLD = 5
#: How far below the benchmark counts as "converting badly". 2_500 bp = 25% worse.
DEFAULT_SHORTFALL_TOLERANCE_BP = 2_500


@dataclass(frozen=True, slots=True)
class ItemFigures:
    """One item's numbers on one channel on one day, already resolved to a menu item."""

    menu_item_id: int
    item_name: str
    channel: SalesChannelName
    metric_date: date
    source: ChannelSourceKind
    views: int | None = None
    orders: int | None = None
    revenue_pence: int | None = None
    rank_in_category: int | None = None
    size_code: str | None = None

    @property
    def display_name(self) -> str:
        return f"{self.item_name} [{self.size_code}]" if self.size_code else self.item_name


def ratio_bp(numerator: int | None, denominator: int | None) -> int | None:
    """`numerator / denominator` in basis points, or `None`.

    `None` when either side is unknown, and also when the denominator is zero --
    "no views, so no conversion rate" is a different statement from 0%, and a
    divide-by-zero dressed as a percentage is how a 0-view item tops a badness
    ranking.
    """
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator * ONE_X_BP // denominator


def roas_bp(attributed_revenue_pence: int | None, ad_spend_pence: int | None) -> int | None:
    """Return on ad spend in basis points. `None` when unknown or nothing was spent."""
    return ratio_bp(attributed_revenue_pence, ad_spend_pence)


def format_x(value_bp: int | None) -> str:
    return "-" if value_bp is None else f"{value_bp / ONE_X_BP:.2f}x"


def format_pct(value_bp: int | None) -> str:
    return "-" if value_bp is None else f"{value_bp / 100:.2f}%"


@dataclass(frozen=True, slots=True)
class FieldCoverage:
    """How many days in the window actually reported one field."""

    field: str
    reported: int
    missing: int

    @property
    def complete(self) -> bool:
        return self.missing == 0

    def __str__(self) -> str:
        return f"{self.field}: {self.reported} of {self.reported + self.missing} day(s)"


@dataclass(frozen=True, slots=True)
class ChannelPerformance:
    """One channel over one window. Every total states what it is a total *of*."""

    channel: SalesChannelName
    since: date
    until: date
    days: int
    sources: tuple[ChannelSourceKind, ...]

    gross_pence: int | None
    commission_pence: int | None
    ad_spend_pence: int | None
    attributed_revenue_pence: int | None
    orders: int | None
    impressions: int | None
    menu_views: int | None

    #: Contribution after commission AND ad spend, summed over the days that
    #: reported all three. `None` when no day did.
    net_pence: int | None
    net_complete_days: int
    net_incomplete_days: int

    coverage: tuple[FieldCoverage, ...]

    @property
    def roas_bp(self) -> int | None:
        return roas_bp(self.attributed_revenue_pence, self.ad_spend_pence)

    @property
    def commission_rate_bp(self) -> int | None:
        return ratio_bp(self.commission_pence, self.gross_pence)

    @property
    def net_margin_bp(self) -> int | None:
        """Contribution as a share of gross, over the complete days only."""
        if self.net_pence is None or self.net_complete_days == 0:
            return None
        return ratio_bp(self.net_pence, self._gross_over_complete_days)

    @property
    def conversion_bp(self) -> int | None:
        return ratio_bp(self.orders, self.menu_views)

    #: Set alongside `net_pence` so `net_margin_bp` compares like with like.
    _gross_over_complete_days: int | None = None

    def incomplete_fields(self) -> tuple[str, ...]:
        return tuple(c.field for c in self.coverage if not c.complete)

    def caveats(self) -> tuple[str, ...]:
        notes: list[str] = []
        incomplete = self.incomplete_fields()
        if incomplete:
            notes.append(
                "not every day reported " + ", ".join(incomplete) + "; totals cover the days "
                "that did and the rest are excluded, not zeroed"
            )
        if self.net_incomplete_days:
            notes.append(
                f"contribution covers {self.net_complete_days} of "
                f"{self.net_complete_days + self.net_incomplete_days} day(s) -- the other "
                "day(s) did not report gross, commission and ad spend together"
            )
        if len(self.sources) > 1:
            notes.append(
                "this window mixes provenances ("
                + ", ".join(s.value for s in self.sources)
                + "); a hand export and a scrape are not equally trustworthy"
            )
        if self.ad_spend_pence == 0 and self.attributed_revenue_pence:
            notes.append("ad spend is zero over this window, so ROAS is undefined rather than huge")
        return tuple(notes)


def _sum_optional(values: Iterable[int | None]) -> tuple[int | None, int, int]:
    """Sum the reported values. Returns (total or None, reported count, missing count)."""
    total = 0
    reported = 0
    missing = 0
    for value in values:
        if value is None:
            missing += 1
        else:
            total += value
            reported += 1
    return (total if reported else None), reported, missing


def channel_performance(
    rows: Sequence[ChannelDayRow], *, channel: SalesChannelName, since: date, until: date
) -> ChannelPerformance:
    """Aggregate one channel's days. `rows` must already be filtered to that channel."""
    mine = [r for r in rows if r.channel is channel and since <= r.metric_date <= until]
    coverage: list[FieldCoverage] = []

    def total(field: str) -> int | None:
        value, reported, missing = _sum_optional(getattr(r, field) for r in mine)
        coverage.append(FieldCoverage(field=field, reported=reported, missing=missing))
        return value

    gross = total("gross_pence")
    commission = total("commission_pence")
    ad_spend = total("ad_spend_pence")
    attributed = total("attributed_revenue_pence")
    orders = total("orders")
    impressions = total("impressions")
    menu_views = total("menu_views")

    # Contribution is computed per day and summed, so a day missing one component
    # is dropped whole rather than contributing a partial subtraction.
    net = 0
    net_gross = 0
    complete = 0
    incomplete = 0
    for row in mine:
        if row.gross_pence is None or row.commission_pence is None or row.ad_spend_pence is None:
            incomplete += 1
            continue
        net += row.gross_pence - row.commission_pence - row.ad_spend_pence
        net_gross += row.gross_pence
        complete += 1

    return ChannelPerformance(
        channel=channel,
        since=since,
        until=until,
        days=len(mine),
        sources=tuple(sorted({r.source for r in mine}, key=lambda s: s.value)),
        gross_pence=gross,
        commission_pence=commission,
        ad_spend_pence=ad_spend,
        attributed_revenue_pence=attributed,
        orders=orders,
        impressions=impressions,
        menu_views=menu_views,
        net_pence=net if complete else None,
        net_complete_days=complete,
        net_incomplete_days=incomplete,
        coverage=tuple(coverage),
        _gross_over_complete_days=net_gross if complete else None,
    )


# ==========================================================================
# Ranks well, converts badly
# ==========================================================================


@dataclass(frozen=True, slots=True)
class ItemAggregate:
    """One item on one channel over the window."""

    menu_item_id: int
    item_name: str
    channel: SalesChannelName
    views: int | None
    orders: int | None
    revenue_pence: int | None
    best_rank: int | None
    days: int
    #: Named so the caller can say *why* an item was left out of the comparison.
    missing_fields: tuple[str, ...] = ()

    @property
    def conversion_bp(self) -> int | None:
        return ratio_bp(self.orders, self.views)

    @property
    def revenue_per_order_pence(self) -> int | None:
        if self.revenue_pence is None or not self.orders:
            return None
        return self.revenue_pence // self.orders


@dataclass(frozen=True, slots=True)
class ConversionGap:
    """An item the platform is already showing people, which people do not order."""

    menu_item_id: int
    item_name: str
    channel: SalesChannelName
    best_rank: int
    views: int
    orders: int
    conversion_bp: int
    benchmark_bp: int
    #: How far below the benchmark, in basis points of conversion rate.
    shortfall_bp: int
    #: Orders the item would have taken at the benchmark rate, on the same views.
    lost_orders: int
    #: That many orders at this item's own average order value. `None` when the
    #: platform did not report revenue -- an estimate is fine, an invention is not.
    lost_revenue_pence: int | None
    days: int

    @property
    def relative_shortfall_bp(self) -> int:
        """Shortfall as a share of the benchmark. 5_000 = converts half as well."""
        return self.shortfall_bp * ONE_X_BP // self.benchmark_bp

    def sentence(self) -> str:
        money = (
            f" about GBP {self.lost_revenue_pence / 100:.2f} of orders"
            if self.lost_revenue_pence is not None
            else f" about {self.lost_orders} orders"
        )
        return (
            f"{self.item_name} sits at #{self.best_rank} on {self.channel.value} with "
            f"{self.views} views but only {self.orders} orders "
            f"({format_pct(self.conversion_bp)} against {format_pct(self.benchmark_bp)} "
            f"for the rest of the menu) --{money} over {self.days} day(s). "
            "Good placement, poor conversion: the photo or the description, not the product."
        )


def aggregate_items(rows: Sequence[ItemFigures]) -> list[ItemAggregate]:
    """Collapse per-day item rows into one row per (channel, item)."""
    buckets: dict[tuple[SalesChannelName, int], list[ItemFigures]] = {}
    for row in rows:
        buckets.setdefault((row.channel, row.menu_item_id), []).append(row)

    out: list[ItemAggregate] = []
    for (channel, menu_item_id), group in buckets.items():
        views, _, views_missing = _sum_optional(r.views for r in group)
        orders, _, orders_missing = _sum_optional(r.orders for r in group)
        revenue, _, _ = _sum_optional(r.revenue_pence for r in group)
        ranks = [r.rank_in_category for r in group if r.rank_in_category is not None]
        missing: list[str] = []
        if views_missing:
            missing.append(f"views on {views_missing} day(s)")
        if orders_missing:
            missing.append(f"orders on {orders_missing} day(s)")
        if not ranks:
            missing.append("rank")
        out.append(
            ItemAggregate(
                menu_item_id=menu_item_id,
                item_name=group[0].display_name,
                channel=channel,
                views=views,
                orders=orders,
                revenue_pence=revenue,
                best_rank=min(ranks) if ranks else None,
                days=len({r.metric_date for r in group}),
                missing_fields=tuple(missing),
            )
        )
    out.sort(key=lambda a: (a.channel.value, a.item_name))
    return out


@dataclass(frozen=True, slots=True)
class RankConvertReport:
    """The rank/convert finding plus everything it had to leave out, and why."""

    channel: SalesChannelName
    gaps: tuple[ConversionGap, ...]
    benchmark_bp: int | None
    considered: int
    #: Items excluded because a figure was missing -- never treated as zero.
    excluded_unknown: tuple[str, ...]
    #: Items excluded for too few views to say anything. Not a finding, just quiet.
    excluded_low_views: tuple[str, ...]
    min_views: int
    rank_threshold: int

    def caveats(self) -> tuple[str, ...]:
        notes: list[str] = []
        if self.benchmark_bp is None:
            notes.append(
                "no benchmark: fewer than two items cleared "
                f"{self.min_views} views, so there is nothing to compare against"
            )
        if self.excluded_unknown:
            notes.append(
                f"{len(self.excluded_unknown)} item(s) excluded for missing figures: "
                + ", ".join(self.excluded_unknown[:6])
            )
        if self.excluded_low_views:
            notes.append(
                f"{len(self.excluded_low_views)} item(s) under {self.min_views} views "
                "in the window -- too quiet to judge"
            )
        return tuple(notes)


def rank_well_convert_badly(
    rows: Sequence[ItemFigures],
    *,
    channel: SalesChannelName,
    min_views: int = DEFAULT_MIN_VIEWS,
    rank_threshold: int = DEFAULT_RANK_THRESHOLD,
    tolerance_bp: int = DEFAULT_SHORTFALL_TOLERANCE_BP,
) -> RankConvertReport:
    """Items with good placement and bad conversion, against the menu's own rate.

    The benchmark is *pooled over the other qualifying items*, not the item's own
    cohort average and not a figure from outside this café. Excluding the candidate
    matters: a single dominant item otherwise drags the benchmark down towards
    itself and stops looking like an outlier.
    """
    aggregates = [a for a in aggregate_items(rows) if a.channel is channel]

    qualifying: list[ItemAggregate] = []
    excluded_unknown: list[str] = []
    excluded_low_views: list[str] = []
    for agg in aggregates:
        if agg.views is None or agg.orders is None:
            excluded_unknown.append(f"{agg.item_name} ({', '.join(agg.missing_fields)})")
            continue
        if agg.views < min_views:
            excluded_low_views.append(agg.item_name)
            continue
        qualifying.append(agg)

    total_views = sum(a.views or 0 for a in qualifying)
    total_orders = sum(a.orders or 0 for a in qualifying)
    pooled = ratio_bp(total_orders, total_views)

    gaps: list[ConversionGap] = []
    for agg in qualifying:
        views = agg.views or 0
        orders = agg.orders or 0
        own_bp = agg.conversion_bp
        if own_bp is None or agg.best_rank is None:
            if agg.best_rank is None:
                excluded_unknown.append(f"{agg.item_name} (no rank reported)")
            continue
        benchmark = ratio_bp(total_orders - orders, total_views - views)
        if benchmark is None:
            continue
        if agg.best_rank > rank_threshold:
            continue
        shortfall = benchmark - own_bp
        if shortfall <= 0 or shortfall * ONE_X_BP // benchmark < tolerance_bp:
            continue
        lost_orders = views * shortfall // ONE_X_BP
        per_order = agg.revenue_per_order_pence
        gaps.append(
            ConversionGap(
                menu_item_id=agg.menu_item_id,
                item_name=agg.item_name,
                channel=channel,
                best_rank=agg.best_rank,
                views=views,
                orders=orders,
                conversion_bp=own_bp,
                benchmark_bp=benchmark,
                shortfall_bp=shortfall,
                lost_orders=lost_orders,
                lost_revenue_pence=(lost_orders * per_order if per_order is not None else None),
                days=agg.days,
            )
        )

    gaps.sort(key=lambda g: (-(g.lost_revenue_pence or 0), -g.shortfall_bp, g.item_name))
    return RankConvertReport(
        channel=channel,
        gaps=tuple(gaps),
        benchmark_bp=pooled if len(qualifying) >= 2 else None,
        considered=len(qualifying),
        excluded_unknown=tuple(excluded_unknown),
        excluded_low_views=tuple(excluded_low_views),
        min_views=min_views,
        rank_threshold=rank_threshold,
    )
