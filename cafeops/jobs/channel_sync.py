"""Pull channel metrics from whichever source is configured, and persist them.

The job owns three things the sources deliberately do not:

1. **The fallback.** `ChannelSourceUnavailable` from the primary source hands over
   to the fallback (spec 9: the browser agent "falls back to CSV when it breaks, and
   it will break"). An `UnmappableReportError` does *not* fall back -- a file whose
   columns are wrong is a thing to look at, not a thing to route around.
2. **Item resolution, and its refusal.** Neither platform knows our `menu_item.id`.
   A name that matches exactly one menu item is resolved; anything else -- no match,
   several matches, a size the menu does not have -- is **reported unresolved and
   skipped**. Same discipline as Agent A's Lightspeed matcher, for the same reason:
   a wrongly matched item attributes views and orders to the wrong product forever,
   and nothing downstream can detect it.
3. **Saying when provenances got mixed.** The repository records it; this reports it.

`sync_channel` and `sync_all_channels` do not commit: the caller owns the transaction,
so a report that half-resolves lands whole or not at all. `sync_channels_separately`
is the scheduler's entry point and commits once per channel, so one channel's
unreachable source neither rolls back nor hides the channels that did import.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session, sessionmaker

from cafeops.db.base import session_scope
from cafeops.db.repositories.channel import (
    ProvenanceChange,
    SqlChannelRepository,
    menu_item_index,
    size_variants,
)
from cafeops.domain.types import SalesChannelName
from cafeops.integrations.channels.analytics import ItemFigures
from cafeops.integrations.channels.base import (
    ChannelItemRow,
    ChannelReport,
    ChannelSource,
    ChannelSourceUnavailable,
)

log = logging.getLogger("cafeops.jobs.channel_sync")

#: Default window when the caller does not say. Long enough for a weekday pattern.
DEFAULT_WINDOW_DAYS = 14


@dataclass(frozen=True, slots=True)
class UnresolvedItem:
    """An item name the platform reported that this menu cannot place."""

    item_name: str
    size_code: str | None
    reason: str
    dates: tuple[date, ...] = ()

    def __str__(self) -> str:
        sized = f"{self.item_name} [{self.size_code}]" if self.size_code else self.item_name
        span = f" on {len(self.dates)} day(s)" if self.dates else ""
        return f"{sized}{span}: {self.reason}"


@dataclass(slots=True)
class ChannelSyncReport:
    channel: SalesChannelName
    since: date
    until: date
    source_used: str = ""
    fell_back: bool = False
    fallback_reason: str | None = None
    days_inserted: int = 0
    days_updated: int = 0
    items_inserted: int = 0
    items_updated: int = 0
    rejected_rows: int = 0
    unresolved: tuple[UnresolvedItem, ...] = ()
    provenance_changes: tuple[ProvenanceChange, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)
    source_refs: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> str:
        return (
            f"{self.channel.value} {self.since}..{self.until} via {self.source_used}: "
            f"days +{self.days_inserted}/~{self.days_updated}, "
            f"items +{self.items_inserted}/~{self.items_updated}, "
            f"{self.rejected_rows} row(s) rejected, "
            f"{len(self.unresolved)} item name(s) unresolved"
        )


def default_window(
    *, until: date | None = None, days: int = DEFAULT_WINDOW_DAYS
) -> tuple[date, date]:
    end = until or date.today()  # noqa: DTZ011 -- a reporting window, not an instant
    return end - timedelta(days=days - 1), end


def fetch_with_fallback(
    *,
    primary: ChannelSource,
    fallback: ChannelSource | None,
    channel: SalesChannelName,
    since: date,
    until: date,
) -> tuple[ChannelReport, str, str | None]:
    """Return (report, which source ran, why the primary was abandoned or None)."""
    try:
        return primary.fetch(channel=channel, since=since, until=until), primary.describe(), None
    except ChannelSourceUnavailable as exc:
        if fallback is None:
            raise
        report = fallback.fetch(channel=channel, since=since, until=until)
        return report, fallback.describe(), str(exc)


def resolve_items(
    session: Session, rows: Sequence[ChannelItemRow]
) -> tuple[list[ItemFigures], list[UnresolvedItem]]:
    """Match reported item names to menu items, or refuse to.

    Matching is exact on (lowercased name, size). No fuzzy matching and no
    "nearest": the failure mode of a wrong match is permanent misattribution, and a
    name in an unresolved list costs a minute of someone's attention.
    """
    index = menu_item_index(session)
    resolved: list[ItemFigures] = []
    problems: dict[tuple[str, str | None, str], list[date]] = {}

    for row in rows:
        key = (row.item_name.strip().lower(), row.size_code)
        candidates = index.get(key)
        if candidates is None and row.size_code is None:
            # A platform that does not report size can still be resolved when the
            # menu holds exactly one item of that name at any size.
            same_name = [
                ids for (name, _size), ids in index.items() if name == row.item_name.strip().lower()
            ]
            flat = [i for ids in same_name for i in ids]
            if len(flat) == 1:
                candidates = flat
            elif len(flat) > 1:
                sizes = size_variants(session, row.item_name.strip())
                problems.setdefault(
                    (
                        row.item_name,
                        row.size_code,
                        f"the export gives no size and the menu has {len(flat)} sizes"
                        + (f" ({', '.join(sizes)})" if sizes else ""),
                    ),
                    [],
                ).append(row.metric_date)
                continue
        if not candidates:
            sizes = size_variants(session, row.item_name.strip())
            reason = (
                f"no menu item of that name at size {row.size_code}"
                + (f"; it exists at {', '.join(sizes)}" if sizes else "")
                if row.size_code
                else "no menu item of that name"
            )
            problems.setdefault((row.item_name, row.size_code, reason), []).append(row.metric_date)
            continue
        if len(candidates) > 1:
            problems.setdefault(
                (
                    row.item_name,
                    row.size_code,
                    f"{len(candidates)} menu items share that name and size (ids "
                    + ", ".join(str(i) for i in candidates)
                    + "); refusing to choose",
                ),
                [],
            ).append(row.metric_date)
            continue
        resolved.append(
            ItemFigures(
                menu_item_id=candidates[0],
                item_name=row.item_name,
                channel=row.channel,
                metric_date=row.metric_date,
                source=row.source,
                views=row.views,
                orders=row.orders,
                revenue_pence=row.revenue_pence,
                rank_in_category=row.rank_in_category,
                size_code=row.size_code,
            )
        )

    unresolved = [
        UnresolvedItem(item_name=name, size_code=size, reason=reason, dates=tuple(sorted(dates)))
        for (name, size, reason), dates in sorted(problems.items())
    ]
    return resolved, unresolved


def sync_channel(
    session: Session,
    *,
    channel: SalesChannelName,
    since: date,
    until: date,
    primary: ChannelSource,
    fallback: ChannelSource | None = None,
) -> ChannelSyncReport:
    """Fetch one channel's window and persist it. Does not commit."""
    report, source_used, fallback_reason = fetch_with_fallback(
        primary=primary, fallback=fallback, channel=channel, since=since, until=until
    )
    repo = SqlChannelRepository(session)

    days_inserted, days_updated = repo.upsert_day(report.day_rows)
    provenance = list(repo.provenance_changes)

    resolved, unresolved = resolve_items(session, report.item_rows)
    items_inserted, items_updated = repo.upsert_item_day(resolved)
    provenance.extend(repo.provenance_changes)

    warnings = list(report.warnings)
    if fallback_reason:
        warnings.insert(0, f"primary source unavailable, fell back: {fallback_reason}")
    for rejected in report.rejected[:10]:
        warnings.append(f"row {rejected.line_no} rejected: {rejected.reason}")

    return ChannelSyncReport(
        channel=channel,
        since=since,
        until=until,
        source_used=source_used,
        fell_back=fallback_reason is not None,
        fallback_reason=fallback_reason,
        days_inserted=days_inserted,
        days_updated=days_updated,
        items_inserted=items_inserted,
        items_updated=items_updated,
        rejected_rows=len(report.rejected),
        unresolved=tuple(unresolved),
        provenance_changes=tuple(provenance),
        warnings=tuple(warnings),
        source_refs=report.source_refs,
    )


def sync_all_channels(
    session: Session,
    *,
    since: date,
    until: date,
    primary: ChannelSource,
    fallback: ChannelSource | None = None,
    channels: Sequence[SalesChannelName] = tuple(SalesChannelName),
) -> list[ChannelSyncReport]:
    """One report per channel. A channel with no export yields an empty report,
    not an error: the owner exports one platform at a time."""
    return [
        sync_channel(
            session,
            channel=channel,
            since=since,
            until=until,
            primary=primary,
            fallback=fallback,
        )
        for channel in channels
    ]


@dataclass(frozen=True, slots=True)
class ChannelOutcome:
    """One channel's night: a committed report, or why nothing was committed for it."""

    channel: SalesChannelName
    report: ChannelSyncReport | None
    #: None when `report` was committed; otherwise what stopped this channel.
    error: str | None = None

    def summary(self) -> str:
        if self.report is not None:
            return self.report.summary()
        return f"{self.channel.value}: nothing imported ({self.error})"


def sync_channels_separately(
    factory: sessionmaker[Session] | None,
    *,
    since: date,
    until: date,
    primary: ChannelSource,
    fallback: ChannelSource | None = None,
    channels: Sequence[SalesChannelName] = tuple(SalesChannelName),
) -> list[ChannelOutcome]:
    """`sync_channel` per channel, each in its own `session_scope` (committed on
    success, rolled back on failure), with an explicit outcome per channel.

    `ChannelSourceUnavailable` is the expected failure (spec 9: the browser source
    will break) and is reported as a warning line. Anything else -- a report whose
    columns are wrong -- is logged with its traceback and reported the same way, so
    one bad export does not stop the other platform importing.
    """
    outcomes: list[ChannelOutcome] = []
    for channel in channels:
        try:
            with session_scope(factory) as session:
                report = sync_channel(
                    session,
                    channel=channel,
                    since=since,
                    until=until,
                    primary=primary,
                    fallback=fallback,
                )
        except ChannelSourceUnavailable as exc:
            outcomes.append(ChannelOutcome(channel, None, f"no source reachable: {exc}"))
            continue
        except Exception as exc:
            log.exception("channel_sync %s: import failed and was rolled back", channel.value)
            outcomes.append(
                ChannelOutcome(channel, None, f"failed, rolled back: {type(exc).__name__}: {exc}")
            )
            continue
        outcomes.append(ChannelOutcome(channel, report))
    return outcomes
