"""One row per Lightspeed sync attempt. shell-agents spec 3.1 / 4.3.

The stale-sync banner needs the last SUCCESSFUL sync, not `max(sale.sold_at)`: the
café closes in the evening and the sync runs at 02:30, so the newest sale is always
>12h old by morning and the banner would fire daily.

The row is also the lock for `POST /api/sync`: SQLite has one writer, so a RUNNING row
younger than 15 minutes refuses a second sync (older ones are marked FAILED
"abandoned" by the service).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from cafeops.db.base import Base
from cafeops.db.models._common import UTCDateTime, enum_col, utcnow
from cafeops.db.models.enums import SyncSource, SyncStatus, SyncTrigger


class SyncRun(Base):
    __tablename__ = "sync_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[SyncTrigger] = mapped_column(enum_col(SyncTrigger), nullable=False)
    source: Mapped[SyncSource] = mapped_column(enum_col(SyncSource), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[SyncStatus] = mapped_column(
        enum_col(SyncStatus), nullable=False, default=SyncStatus.RUNNING
    )
    window_since: Mapped[date] = mapped_column(Date, nullable=False)
    window_until: Mapped[date] = mapped_column(Date, nullable=False)
    receipts_seen: Mapped[int | None] = mapped_column(Integer)
    lines_ingested: Mapped[int | None] = mapped_column(Integer)
    unresolved_count: Mapped[int | None] = mapped_column(Integer)
    #: skipped_reason / partial_reason / exception text.
    detail: Mapped[str | None] = mapped_column(Text)
    #: Operator name for MANUAL_WEB (DECISIONS.md 6); "scheduler" / "cli" otherwise.
    requested_by: Mapped[str | None] = mapped_column(String(120))

    __table_args__ = (
        CheckConstraint(
            "(status = 'RUNNING') = (finished_at IS NULL)", name="finished_iff_not_running"
        ),
        CheckConstraint("window_until >= window_since", name="window_ordered"),
        Index("ix_sync_run_status_started", "status", "started_at"),
        Index("ix_sync_run_started", "started_at"),
    )


__all__ = ["SyncRun"]
