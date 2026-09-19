"""The `ChannelSource` protocol and the inert rows every implementation returns.

Spec 4.6's access reality: Deliveroo's and Just Eat's partner APIs are gated to
certified POS integrators, so assume we never get in. The owner has portal logins
and exports by hand, which makes CSV the path that works *today* and a browser
agent the path that works *until the page changes*. Both sit behind this one
protocol so swapping between them is `CAFEOPS_CHANNEL_SOURCE=CSV` rather than a
rewrite (spec 9's job 1: "falls back to CSV when it breaks, and it will break").

Two rules the rows enforce:

1. **Every row carries its `source`.** A figure typed from a PDF and a figure
   scraped by a browser agent deserve different trust, and silently mixing them is
   unauditable. `source_ref` names the file or run so a bad import can be found.
2. **A field nobody supplied is `None`, never zero.** Invariant 8. "Deliveroo did
   not export commission" and "commission was zero" are different statements, and
   `ChannelMetric.net_pence` already refuses to subtract what it does not have.

A source never writes. `fetch` returns a `ChannelReport` and `jobs/channel_sync.py`
persists it, so a parser bug cannot half-commit a day.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

from cafeops.domain.types import ChannelSourceKind, SalesChannelName


class ChannelSourceError(Exception):
    """Base for anything a channel source refuses to do."""


class UnmappableReportError(ChannelSourceError):
    """The file's columns could not be mapped to a known platform export.

    Raised rather than guessing. Spec 4.6 gives us two platforms with different
    column sets; a parser that shrugs and picks the nearest header will one day put
    ad spend into the commission column, and nothing downstream could detect it.
    """

    def __init__(
        self,
        message: str,
        *,
        headers: tuple[str, ...] = (),
        missing: tuple[str, ...] = (),
        unrecognised: tuple[str, ...] = (),
        candidates: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.headers = headers
        self.missing = missing
        self.unrecognised = unrecognised
        self.candidates = candidates

    def report(self) -> str:
        parts = [str(self)]
        if self.headers:
            parts.append("  headers seen: " + ", ".join(self.headers))
        if self.missing:
            parts.append("  required but absent: " + ", ".join(self.missing))
        if self.unrecognised:
            parts.append("  not recognised: " + ", ".join(self.unrecognised))
        if self.candidates:
            parts.append("  schemas tried: " + ", ".join(self.candidates))
        return "\n".join(parts)


class ChannelSourceUnavailable(ChannelSourceError):
    """The source could not run at all -- no browser, no credentials, page changed.

    `jobs/channel_sync.py` catches this and uses the configured fallback source.
    That is the whole reason it is a distinct exception from a parse failure: a
    broken scrape should fall back to CSV, whereas a CSV whose columns are wrong
    should stop and be looked at.
    """


@dataclass(frozen=True, slots=True)
class RejectedRow:
    """A row the source declined to interpret, kept so the count reconciles."""

    line_no: int
    reason: str
    raw: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChannelDayRow:
    """One channel's performance on one day, as read. Money is integer pence."""

    channel: SalesChannelName
    metric_date: date
    source: ChannelSourceKind
    source_ref: str | None = None
    impressions: int | None = None
    menu_views: int | None = None
    conversions: int | None = None
    orders: int | None = None
    ad_spend_pence: int | None = None
    attributed_revenue_pence: int | None = None
    commission_pence: int | None = None
    gross_pence: int | None = None
    #: Basis points, so an average position of 3.5 needs no float. 3.5 -> 350.
    avg_position_bp: int | None = None
    rating_bp: int | None = None


@dataclass(frozen=True, slots=True)
class ChannelItemRow:
    """One item on one channel on one day.

    The item arrives as a *name*, because neither platform knows our `menu_item.id`.
    Resolution happens in the sync job and refuses to guess, the same discipline
    Agent A's Lightspeed matcher uses: a wrongly matched item attributes views and
    orders to the wrong product forever.
    """

    channel: SalesChannelName
    metric_date: date
    source: ChannelSourceKind
    item_name: str
    size_code: str | None = None
    views: int | None = None
    orders: int | None = None
    revenue_pence: int | None = None
    rank_in_category: int | None = None


@dataclass(frozen=True, slots=True)
class ChannelReport:
    """Everything one fetch produced. Inert: nothing here has touched the database."""

    channel: SalesChannelName
    source: ChannelSourceKind
    since: date
    until: date
    day_rows: tuple[ChannelDayRow, ...] = ()
    item_rows: tuple[ChannelItemRow, ...] = ()
    rejected: tuple[RejectedRow, ...] = ()
    warnings: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()

    def summary(self) -> str:
        return (
            f"{self.channel.value} via {self.source.value}: "
            f"{len(self.day_rows)} day row(s), {len(self.item_rows)} item row(s), "
            f"{len(self.rejected)} rejected, {len(self.warnings)} warning(s)"
        )


@runtime_checkable
class ChannelSource(Protocol):
    """One way of getting Deliveroo / Just Eat numbers into the system."""

    #: Stamped onto every row this source produces.
    kind: ChannelSourceKind

    def describe(self) -> str:
        """One line for the CLI and the sync log, naming what this will actually do."""
        ...

    def fetch(self, *, channel: SalesChannelName, since: date, until: date) -> ChannelReport:
        """Read the reports for `channel` over [since, until].

        Must not write anything. Raises `ChannelSourceUnavailable` when the source
        itself cannot run (so the caller may fall back) and `UnmappableReportError`
        when it ran but the data is not interpretable (so the caller must not).
        """
        ...
