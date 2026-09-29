"""Delivery apps by month: what customers paid, commission, ads. Finance spec 2.5, 3.3.

Read rule: a `channel_statement` row if present; else the sum of `channel_metric` days
in the month, each figure only when every day reported it; else the month is missing.
Missing is never zero (invariant 8) and the design's invented Deliveroo months are never
seeded.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.clock import utcnow
from cafeops.db.models.channel import ChannelMetric
from cafeops.db.models.finance import ChannelStatement
from cafeops.domain.enums import ChannelSourceKind, SalesChannelName
from cafeops.services.finance.common import (
    FinanceRefused,
    month_label,
    month_range,
    require_pence,
)

__all__ = [
    "CHANNELS",
    "MonthChannel",
    "UploadOutcome",
    "channel_label",
    "month_figures",
    "months_with_channel_data",
    "upload_csv",
    "upsert_statement",
]

CHANNELS: tuple[SalesChannelName, ...] = (SalesChannelName.DELIVEROO, SalesChannelName.JUST_EAT)


def channel_label(c: SalesChannelName) -> str:
    return "Deliveroo" if c is SalesChannelName.DELIVEROO else "Just Eat"


@dataclass(frozen=True, slots=True)
class MonthChannel:
    month: date
    channel: SalesChannelName
    present: bool
    gross_pence: int | None
    commission_pence: int | None
    ads_pence: int | None
    source: str | None
    note: str | None

    @property
    def kept_pence(self) -> int | None:
        """Refuses partial subtraction: any unreported figure makes it unknown."""
        if self.gross_pence is None or self.commission_pence is None or self.ads_pence is None:
            return None
        return self.gross_pence - self.commission_pence - self.ads_pence

    @property
    def kept_bp(self) -> int | None:
        kept = self.kept_pence
        if kept is None or not self.gross_pence:
            return None
        return (kept * 10000 + self.gross_pence // 2) // self.gross_pence


def _sum_if_complete(values: list[int | None]) -> int | None:
    if not values or any(v is None for v in values):
        return None
    return sum(v for v in values if v is not None)


def month_figures(session: Session, month: date, channel: SalesChannelName) -> MonthChannel:
    first, last = month_range(month)
    st = session.scalars(
        select(ChannelStatement).where(
            ChannelStatement.channel == channel, ChannelStatement.month == first
        )
    ).one_or_none()
    if st is not None:
        return MonthChannel(
            month=first,
            channel=channel,
            present=True,
            gross_pence=st.gross_pence,
            commission_pence=st.commission_pence,
            ads_pence=st.ad_spend_pence,
            source=st.source.value,
            note=st.notes,
        )
    days = list(
        session.scalars(
            select(ChannelMetric).where(
                ChannelMetric.channel == channel,
                ChannelMetric.metric_date >= first,
                ChannelMetric.metric_date <= last,
            )
        )
    )
    if not days:
        return MonthChannel(first, channel, False, None, None, None, None, None)
    sources = {d.source.value for d in days}
    return MonthChannel(
        month=first,
        channel=channel,
        present=True,
        gross_pence=_sum_if_complete([d.gross_pence for d in days]),
        commission_pence=_sum_if_complete([d.commission_pence for d in days]),
        ads_pence=_sum_if_complete([d.ad_spend_pence for d in days]),
        source=sources.pop() if len(sources) == 1 else "MIXED",
        note=f"summed from {len(days)} daily export row(s)",
    )


def months_with_channel_data(session: Session) -> set[date]:
    out = {m.replace(day=1) for m in session.scalars(select(ChannelStatement.month))}
    out |= {d.replace(day=1) for d in session.scalars(select(ChannelMetric.metric_date))}
    return out


def upsert_statement(
    session: Session,
    *,
    month: date,
    channel: SalesChannelName,
    gross_pence: int | None,
    commission_pence: int | None,
    ads_pence: int | None,
    operator: str | None = None,
) -> MonthChannel:
    """Type a month's figures. All three null removes the typed statement."""
    for name, v in (
        ("gross_pence", gross_pence),
        ("commission_pence", commission_pence),
        ("ads_pence", ads_pence),
    ):
        require_pence(v, name)
    first = month.replace(day=1)
    st = session.scalars(
        select(ChannelStatement).where(
            ChannelStatement.channel == channel, ChannelStatement.month == first
        )
    ).one_or_none()
    if gross_pence is None and commission_pence is None and ads_pence is None:
        if st is not None:
            session.delete(st)
            session.flush()
        return month_figures(session, first, channel)
    if st is None:
        st = ChannelStatement(channel=channel, month=first, source=ChannelSourceKind.MANUAL)
        session.add(st)
    st.gross_pence = gross_pence
    st.commission_pence = commission_pence
    st.ad_spend_pence = ads_pence
    st.source = ChannelSourceKind.MANUAL
    st.source_ref = f"typed on Reconcile ({month_label(first)})"
    st.updated_by = operator
    st.updated_at = utcnow()
    session.flush()
    return month_figures(session, first, channel)


@dataclass(frozen=True, slots=True)
class UploadOutcome:
    inserted: int
    updated: int
    rejected: list[str]
    unmapped: list[str]
    notes: list[str]


def upload_csv(
    session: Session, *, channel: SalesChannelName, filename: str, text: str
) -> UploadOutcome:
    """Import a portal CSV export (daily rows) through the existing channel pipeline.

    The file is parsed by `CsvChannelSource`, which refuses a file whose columns do not
    map rather than guessing. The rows land in `channel_metric`; month figures are then
    summed from them by `month_figures`.
    """
    from cafeops.integrations.channels.base import UnmappableReportError
    from cafeops.integrations.channels.csv_source import CsvChannelSource
    from cafeops.jobs.channel_sync import sync_channel

    if not text.strip():
        raise FinanceRefused("the file is empty")
    if len(text) > 5_000_000:
        raise FinanceRefused("the file is over 5 MB; export a shorter window")
    safe = Path(filename or "export.csv").name or "export.csv"
    if not safe.lower().endswith(".csv"):
        safe += ".csv"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / safe
        path.write_text(text, encoding="utf-8")
        try:
            report = sync_channel(
                session,
                channel=channel,
                since=date(2020, 1, 1),
                until=date(2100, 1, 1),
                primary=CsvChannelSource(files=[path], platform=channel),
            )
        except UnmappableReportError as exc:
            return UploadOutcome(0, 0, [], [exc.report()], [])
    return UploadOutcome(
        inserted=report.days_inserted,
        updated=report.days_updated,
        rejected=[w for w in report.warnings if w.startswith("row ")],
        unmapped=[],
        notes=[w for w in report.warnings if not w.startswith("row ")],
    )
