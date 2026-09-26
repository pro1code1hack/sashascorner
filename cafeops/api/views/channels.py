"""Deliveroo and Just Eat. Spec 4.6.

They are an ad platform as well as a channel, so the three figures worth showing are
ROAS, contribution after commission **and** ad spend, and items that rank well but
convert badly. All three come from `integrations/channels/analytics.py`, which is pure
and already answers them.

The thing this view must not lose is **coverage**. A day that did not report ad spend is
dropped from the contribution total whole rather than part-subtracted, and `coverage`,
`net_incomplete_days` and `caveats` are what say so. The same reasoning as invariant 8:
a total over 9 of 14 days understates the argument it exists to make, and the only way
to see that is to be told.

`sources` matters for the same reason. Partner APIs are gated to certified POS
integrators (ARCHITECTURE 11.2), so every row here came from a hand export or a browser
agent, and those are not equally trustworthy.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from cafeops.api.schemas import (
    ChannelDayOut,
    ChannelFinding,
    ChannelPerformanceOut,
    ChannelsResponse,
    ConversionGapOut,
    FieldCoverageOut,
)
from cafeops.config import settings
from cafeops.db.repositories.channel import SqlChannelRepository
from cafeops.integrations.channels.analytics import (
    ChannelPerformance,
    RankConvertReport,
    channel_performance,
    rank_well_convert_badly,
)
from cafeops.integrations.channels.base import ChannelDayRow

__all__ = ["channels_view"]

DEFAULT_WINDOW_DAYS = 28
DEFAULT_MIN_VIEWS = 100


def _performance(
    perf: ChannelPerformance, day_rows: Sequence[ChannelDayRow]
) -> ChannelPerformanceOut:
    return ChannelPerformanceOut(
        channel=perf.channel.value,
        since=perf.since,
        until=perf.until,
        days=perf.days,
        sources=tuple(source.value for source in perf.sources),
        gross_pence=perf.gross_pence,
        commission_pence=perf.commission_pence,
        ad_spend_pence=perf.ad_spend_pence,
        attributed_revenue_pence=perf.attributed_revenue_pence,
        orders=perf.orders,
        impressions=perf.impressions,
        menu_views=perf.menu_views,
        net_pence=perf.net_pence,
        net_complete_days=perf.net_complete_days,
        net_incomplete_days=perf.net_incomplete_days,
        roas_bp=perf.roas_bp,
        commission_rate_bp=perf.commission_rate_bp,
        net_margin_bp=perf.net_margin_bp,
        conversion_bp=perf.conversion_bp,
        coverage=tuple(
            FieldCoverageOut(
                field_name=item.field,
                reported=item.reported,
                missing=item.missing,
                complete=item.complete,
            )
            for item in perf.coverage
        ),
        daily=tuple(
            ChannelDayOut(
                metric_date=row.metric_date,
                gross_pence=row.gross_pence,
                orders=row.orders,
                ad_spend_pence=row.ad_spend_pence,
            )
            for row in sorted(day_rows, key=lambda r: r.metric_date)
        ),
        caveats=perf.caveats(),
    )


def _finding(report: RankConvertReport) -> ChannelFinding:
    return ChannelFinding(
        channel=report.channel.value,
        benchmark_bp=report.benchmark_bp,
        considered=report.considered,
        min_views=report.min_views,
        rank_threshold=report.rank_threshold,
        gaps=tuple(
            ConversionGapOut(
                menu_item_id=gap.menu_item_id,
                item_name=gap.item_name,
                best_rank=gap.best_rank,
                views=gap.views,
                orders=gap.orders,
                conversion_bp=gap.conversion_bp,
                benchmark_bp=gap.benchmark_bp,
                shortfall_bp=gap.shortfall_bp,
                relative_shortfall_bp=gap.relative_shortfall_bp,
                lost_orders=gap.lost_orders,
                lost_revenue_pence=gap.lost_revenue_pence,
                days=gap.days,
                sentence=gap.sentence(),
            )
            for gap in report.gaps
        ),
        caveats=report.caveats(),
    )


def channels_view(
    session: Session,
    *,
    since: date | None = None,
    until: date | None = None,
    days: int = DEFAULT_WINDOW_DAYS,
    min_views: int = DEFAULT_MIN_VIEWS,
) -> ChannelsResponse:
    repo = SqlChannelRepository(session)
    if until is None:
        # Local calendar day, not UTC's -- a cafe's day is the unit here
        # (ARCHITECTURE 6.4), and `date.today()` is naive besides.
        until = datetime.now(settings.tz).date()
    if since is None:
        since = until - timedelta(days=days - 1)

    channels = repo.channels_present(since=since, until=until)
    notes: list[str] = []
    if not channels:
        notes.append(
            f"no channel metrics between {since} and {until}. The demo seed writes none -- "
            "run `cafeops channels import deliveroo --commit` (and just-eat) to load the "
            "shipped fixtures, or point CAFEOPS_CHANNEL_CSV_DIR at real portal exports. "
            "Partner APIs are gated to certified POS integrators, so a hand export is the "
            "path that works today (ARCHITECTURE 11.2)."
        )

    performance: list[ChannelPerformanceOut] = []
    findings: list[ChannelFinding] = []
    for channel in channels:
        day_rows = repo.day_figures(since=since, until=until, channel=channel)
        performance.append(
            _performance(
                channel_performance(day_rows, channel=channel, since=since, until=until),
                day_rows,
            )
        )
        item_rows = repo.item_figures(since=since, until=until, channel=channel)
        if item_rows:
            findings.append(
                _finding(rank_well_convert_badly(item_rows, channel=channel, min_views=min_views))
            )

    if channels and not findings:
        notes.append(
            "no per-item channel rows in this window, so ranks-well-converts-badly has "
            "nothing to judge. The day-level export and the item-level export are two "
            "different downloads from the portal."
        )

    return ChannelsResponse(
        since=since,
        until=until,
        channels_present=tuple(channel.value for channel in channels),
        sources_present=tuple(
            source.value for source in repo.sources_present(since=since, until=until)
        ),
        performance=tuple(performance),
        findings=tuple(findings),
        notes=tuple(notes),
    )
