"""Channel metric persistence. Implements `protocols.ChannelRepository`.

Two rules this module keeps, both from spec 4.6:

* **Upsert is idempotent on the unique key** -- (channel, date) for a day and
  (channel, date, item) for an item -- so re-importing an overlapping export is a
  no-op rather than a duplicate. The owner exports by hand; overlapping windows are
  the normal case, not the exception.
* **A provenance change is reported, never silent.** Overwriting a `CSV_UPLOAD`
  figure with a `BROWSER_AGENT` one is allowed, but the caller is told, because "a
  figure typed from a PDF and one scraped by an agent are never silently mixed".

No derived numbers live here. ROAS and the rank/convert report are pure functions in
`integrations/channels/analytics.py`; this module only assembles their inputs.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models import ChannelItemMetric, ChannelMetric, MenuItem
from cafeops.domain.types import ChannelSourceKind, SalesChannelName
from cafeops.integrations.channels.analytics import ItemFigures
from cafeops.integrations.channels.base import ChannelDayRow

#: The scalar columns a day row carries, in one place so the upsert and the read
#: cannot drift apart.
DAY_FIELDS: tuple[str, ...] = (
    "impressions",
    "menu_views",
    "conversions",
    "orders",
    "ad_spend_pence",
    "attributed_revenue_pence",
    "commission_pence",
    "gross_pence",
    "avg_position_bp",
    "rating_bp",
)

ITEM_FIELDS: tuple[str, ...] = ("views", "orders", "revenue_pence", "rank_in_category")


@dataclass(frozen=True, slots=True)
class ProvenanceChange:
    """An existing figure was replaced by one that arrived a different way."""

    channel: SalesChannelName
    metric_date: date
    was: ChannelSourceKind
    now: ChannelSourceKind
    item_name: str | None = None

    def __str__(self) -> str:
        what = f"{self.channel.value} {self.metric_date}"
        if self.item_name:
            what += f" / {self.item_name}"
        return f"{what}: {self.was.value} -> {self.now.value}"


class SqlChannelRepository:
    """Deliveroo / Just Eat metrics. Spec 4.6."""

    def __init__(self, session: Session) -> None:
        self.session = session
        #: Populated by the last upsert. The sync job surfaces these.
        self.provenance_changes: list[ProvenanceChange] = []

    # -- writes ------------------------------------------------------------

    def upsert_day(self, rows: Iterable[object]) -> tuple[int, int]:
        """Idempotent on (channel, metric_date). Returns (inserted, updated)."""
        inserted = 0
        updated = 0
        self.provenance_changes = []
        for row in rows:
            if not isinstance(row, ChannelDayRow):
                raise TypeError(f"upsert_day wants ChannelDayRow, got {type(row).__name__}")
            existing = self.session.scalars(
                select(ChannelMetric).where(
                    ChannelMetric.channel == row.channel,
                    ChannelMetric.metric_date == row.metric_date,
                )
            ).one_or_none()
            if existing is None:
                record = ChannelMetric(
                    channel=row.channel,
                    metric_date=row.metric_date,
                    source=row.source,
                    source_ref=row.source_ref,
                )
                for name in DAY_FIELDS:
                    setattr(record, name, getattr(row, name))
                self.session.add(record)
                inserted += 1
                continue
            if existing.source is not row.source:
                self.provenance_changes.append(
                    ProvenanceChange(
                        channel=row.channel,
                        metric_date=row.metric_date,
                        was=existing.source,
                        now=row.source,
                    )
                )
            for name in DAY_FIELDS:
                setattr(existing, name, getattr(row, name))
            existing.source = row.source
            existing.source_ref = row.source_ref
            updated += 1
        self.session.flush()
        return inserted, updated

    def upsert_item_day(self, rows: Iterable[object]) -> tuple[int, int]:
        """Idempotent on (channel, metric_date, menu_item_id). Returns (inserted, updated).

        Takes `ItemFigures`, which already carries a resolved `menu_item_id`.
        Resolution refuses to guess and happens in `jobs/channel_sync.py`, so a
        name this system cannot place never reaches the table at all.
        """
        inserted = 0
        updated = 0
        for row in rows:
            if not isinstance(row, ItemFigures):
                raise TypeError(f"upsert_item_day wants ItemFigures, got {type(row).__name__}")
            existing = self.session.scalars(
                select(ChannelItemMetric).where(
                    ChannelItemMetric.channel == row.channel,
                    ChannelItemMetric.metric_date == row.metric_date,
                    ChannelItemMetric.menu_item_id == row.menu_item_id,
                )
            ).one_or_none()
            if existing is None:
                record = ChannelItemMetric(
                    channel=row.channel,
                    metric_date=row.metric_date,
                    menu_item_id=row.menu_item_id,
                    source=row.source,
                )
                for name in ITEM_FIELDS:
                    setattr(record, name, getattr(row, name))
                self.session.add(record)
                inserted += 1
                continue
            if existing.source is not row.source:
                self.provenance_changes.append(
                    ProvenanceChange(
                        channel=row.channel,
                        metric_date=row.metric_date,
                        was=existing.source,
                        now=row.source,
                        item_name=row.item_name,
                    )
                )
            for name in ITEM_FIELDS:
                setattr(existing, name, getattr(row, name))
            existing.source = row.source
            updated += 1
        self.session.flush()
        return inserted, updated

    # -- reads -------------------------------------------------------------

    def range(self, *, since: date, until: date) -> list[object]:
        """Every channel's day rows over [since, until], oldest first."""
        stmt = (
            select(ChannelMetric)
            .where(ChannelMetric.metric_date >= since, ChannelMetric.metric_date <= until)
            .order_by(ChannelMetric.metric_date, ChannelMetric.channel)
        )
        return list(self.session.scalars(stmt))

    def latest_metric_date(self, channel: object) -> date | None:
        if not isinstance(channel, SalesChannelName):
            raise TypeError(f"latest_metric_date wants SalesChannelName, got {channel!r}")
        stmt = (
            select(ChannelMetric.metric_date)
            .where(ChannelMetric.channel == channel)
            .order_by(ChannelMetric.metric_date.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).one_or_none()

    def day_figures(
        self, *, since: date, until: date, channel: SalesChannelName | None = None
    ) -> list[ChannelDayRow]:
        """Stored days as the inert dataclass `analytics` consumes."""
        stmt = select(ChannelMetric).where(
            ChannelMetric.metric_date >= since, ChannelMetric.metric_date <= until
        )
        if channel is not None:
            stmt = stmt.where(ChannelMetric.channel == channel)
        stmt = stmt.order_by(ChannelMetric.metric_date, ChannelMetric.channel)
        return [
            ChannelDayRow(
                channel=row.channel,
                metric_date=row.metric_date,
                source=row.source,
                source_ref=row.source_ref,
                **{name: getattr(row, name) for name in DAY_FIELDS},
            )
            for row in self.session.scalars(stmt)
        ]

    def item_figures(
        self, *, since: date, until: date, channel: SalesChannelName | None = None
    ) -> list[ItemFigures]:
        """Stored item rows joined to their menu item's name and size."""
        stmt = (
            select(ChannelItemMetric, MenuItem.name, MenuItem.size_code)
            .join(MenuItem, MenuItem.id == ChannelItemMetric.menu_item_id)
            .where(
                ChannelItemMetric.metric_date >= since,
                ChannelItemMetric.metric_date <= until,
            )
        )
        if channel is not None:
            stmt = stmt.where(ChannelItemMetric.channel == channel)
        stmt = stmt.order_by(ChannelItemMetric.metric_date, MenuItem.name)
        out: list[ItemFigures] = []
        for record, name, size_code in self.session.execute(stmt):
            out.append(
                ItemFigures(
                    menu_item_id=record.menu_item_id,
                    item_name=name,
                    channel=record.channel,
                    metric_date=record.metric_date,
                    source=record.source,
                    size_code=size_code.value if size_code is not None else None,
                    **{field: getattr(record, field) for field in ITEM_FIELDS},
                )
            )
        return out

    def channels_present(self, *, since: date, until: date) -> list[SalesChannelName]:
        stmt = (
            select(ChannelMetric.channel)
            .where(ChannelMetric.metric_date >= since, ChannelMetric.metric_date <= until)
            .distinct()
        )
        return sorted(self.session.scalars(stmt), key=lambda c: c.value)

    def sources_present(self, *, since: date, until: date) -> list[ChannelSourceKind]:
        stmt = (
            select(ChannelMetric.source)
            .where(ChannelMetric.metric_date >= since, ChannelMetric.metric_date <= until)
            .distinct()
        )
        return sorted(self.session.scalars(stmt), key=lambda s: s.value)


def menu_item_index(session: Session) -> dict[tuple[str, str | None], list[int]]:
    """`(lowercased name, size code or None) -> menu_item ids`.

    A list rather than a single id on purpose: a duplicate is a refusal upstream,
    and collapsing it here would hide it.
    """
    index: dict[tuple[str, str | None], list[int]] = {}
    stmt = select(MenuItem.id, MenuItem.name, MenuItem.size_code)
    for item_id, name, size_code in session.execute(stmt):
        size = size_code.value if size_code is not None else None
        index.setdefault((name.strip().lower(), size), []).append(item_id)
    return index


def size_variants(session: Session, name: str) -> Sequence[str]:
    """Which sizes a given item name exists in. Used to explain a failed match."""
    stmt = select(MenuItem.size_code).where(MenuItem.name == name)
    return sorted(
        {row.value for row in session.scalars(stmt) if row is not None},
    )
