"""Deliveroo and Just Eat as a channel *and* an ad platform. Spec 4.6.

Two implementations of one protocol, and which one runs is configuration:

    CAFEOPS_CHANNEL_SOURCE=CSV            # the path that works today
    CAFEOPS_CHANNEL_SOURCE=BROWSER_AGENT  # falls back to CSV when it breaks
    CAFEOPS_CHANNEL_CSV_DIR=./channel_exports

`build_source` is the only place that reads those settings, so "falling back to CSV
is a config change, not a rewrite" is literally true: the fallback is already
constructed and handed to the sync job alongside the primary source.
"""

from __future__ import annotations

from pathlib import Path

from cafeops.config import settings
from cafeops.domain.types import ChannelSourceKind, SalesChannelName
from cafeops.integrations.channels.analytics import (
    ChannelPerformance,
    ConversionGap,
    ItemAggregate,
    ItemFigures,
    RankConvertReport,
    aggregate_items,
    channel_performance,
    format_pct,
    format_x,
    rank_well_convert_badly,
    roas_bp,
)
from cafeops.integrations.channels.base import (
    ChannelDayRow,
    ChannelItemRow,
    ChannelReport,
    ChannelSource,
    ChannelSourceError,
    ChannelSourceUnavailable,
    RejectedRow,
    UnmappableReportError,
)
from cafeops.integrations.channels.browser_source import (
    PORTAL_PLANS,
    BrowserAgentChannelSource,
    BrowserDriver,
    FixtureBrowserDriver,
    ReportPlan,
)
from cafeops.integrations.channels.columns import (
    SCHEMAS,
    ReportSchema,
    detect_schema,
    map_headers,
    schema_for,
)
from cafeops.integrations.channels.csv_source import CsvChannelSource, ParsedFile

#: Sample portal exports for both platforms, plus one deliberately unmappable file.
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

__all__ = [
    "FIXTURES_DIR",
    "PORTAL_PLANS",
    "SCHEMAS",
    "BrowserAgentChannelSource",
    "BrowserDriver",
    "ChannelDayRow",
    "ChannelItemRow",
    "ChannelPerformance",
    "ChannelReport",
    "ChannelSource",
    "ChannelSourceError",
    "ChannelSourceUnavailable",
    "ConversionGap",
    "CsvChannelSource",
    "FixtureBrowserDriver",
    "ItemAggregate",
    "ItemFigures",
    "ParsedFile",
    "RankConvertReport",
    "RejectedRow",
    "ReportPlan",
    "ReportSchema",
    "UnmappableReportError",
    "aggregate_items",
    "build_source",
    "channel_performance",
    "csv_dir",
    "detect_schema",
    "format_pct",
    "format_x",
    "map_headers",
    "rank_well_convert_badly",
    "roas_bp",
    "schema_for",
]


def csv_dir() -> Path:
    """Where hand-exported CSVs live. Falls back to the shipped fixtures.

    Defaulting to the fixtures rather than raising is deliberate: a fresh checkout
    can run `cafeops channels import` and see the whole path work before anyone has
    downloaded anything from a portal.
    """
    configured = getattr(settings, "channel_csv_dir", None)
    if configured:
        return Path(configured).expanduser()
    return FIXTURES_DIR


def _browser_driver() -> BrowserDriver | None:
    """The configured driver, or None -- which is the normal state today.

    `fixture` wires the no-network driver against the shipped sample files so the
    BROWSER_AGENT path can be demonstrated end to end. Anything else is unknown, and
    an unknown driver name yields None rather than a guess, so the sync falls back.
    """
    name = (getattr(settings, "channel_browser_driver", None) or "").strip().lower()
    if name == "fixture":
        return FixtureBrowserDriver(
            fixtures={
                SalesChannelName.DELIVEROO: (
                    FIXTURES_DIR / "deliveroo_daily.csv",
                    FIXTURES_DIR / "deliveroo_items.csv",
                ),
                SalesChannelName.JUST_EAT: (
                    FIXTURES_DIR / "just_eat_daily.csv",
                    FIXTURES_DIR / "just_eat_items.csv",
                ),
            }
        )
    return None


def build_source(
    kind: ChannelSourceKind | None = None, *, directory: Path | None = None
) -> tuple[ChannelSource, ChannelSource | None]:
    """Return (primary source, fallback or None).

    The fallback is always the CSV source when the primary is the browser agent,
    because spec 4.6 says the browser agent will break and spec 9 says falling back
    is the expected behaviour rather than an incident.
    """
    if kind is None:
        raw = (getattr(settings, "channel_source", None) or "CSV").strip().upper()
        try:
            kind = ChannelSourceKind(raw)
        except ValueError as exc:
            raise ValueError(
                f"CAFEOPS_CHANNEL_SOURCE={raw!r} is not one of "
                + ", ".join(k.value for k in ChannelSourceKind)
            ) from exc

    where = directory or csv_dir()
    csv_source = CsvChannelSource(directory=where)

    if kind is ChannelSourceKind.CSV_UPLOAD:
        return csv_source, None
    if kind is ChannelSourceKind.MANUAL:
        return csv_source.with_source_kind(ChannelSourceKind.MANUAL), None
    if kind is ChannelSourceKind.BROWSER_AGENT:
        return BrowserAgentChannelSource(driver=_browser_driver()), csv_source
    raise ValueError(
        f"{kind.value} is not a source this system can build. PARTNER_API is gated to "
        "certified POS integrators (spec 4.6); there is nothing to construct."
    )
