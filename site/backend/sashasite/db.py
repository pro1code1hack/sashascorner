"""Engine, session, and the site's own tables (all prefixed ``site_``).

The SQLite file is shared with cafeops. The site WRITES only ``site_*`` tables;
the ops ``menu_item`` table is read with a Core ``text()`` select in drift.py only.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Engine,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from sashasite.config import get_settings


class UTCDateTime(TypeDecorator[dt.datetime]):
    """Tz-aware UTC in Python, naive UTC on disk. Naive input is refused."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect: Dialect) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime refused; use tz-aware UTC")
        return value.astimezone(dt.UTC).replace(tzinfo=None)

    def process_result_value(
        self, value: dt.datetime | None, dialect: Dialect
    ) -> dt.datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=dt.UTC)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={"pk": "pk_%(table_name)s", "uq": "uq_%(table_name)s_%(column_0_name)s"}
    )


class SiteBooking(Base):
    __tablename__ = "site_booking"
    __table_args__ = (
        CheckConstraint("party > 0", name="ck_site_booking_party_positive"),
        CheckConstraint("status IN ('confirmed', 'cancelled')", name="ck_site_booking_status"),
        Index("ix_site_booking_date_status", "local_date", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(16), unique=True)
    manage_token: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="confirmed")
    name: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(30))
    party: Mapped[int] = mapped_column(Integer)
    #: Café-local calendar date and wall-clock start, as the guest chose them.
    local_date: Mapped[dt.date] = mapped_column(Date)
    local_time: Mapped[str] = mapped_column(String(5))
    #: The same instant in UTC, and when the table is released.
    starts_at: Mapped[dt.datetime] = mapped_column(UTCDateTime())
    ends_at: Mapped[dt.datetime] = mapped_column(UTCDateTime())
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime())


class SiteContactMessage(Base):
    __tablename__ = "site_contact_message"
    __table_args__ = (
        CheckConstraint(
            "topic IN ('general', 'order', 'events', 'feedback', 'press', 'jobs')",
            name="ck_site_contact_message_topic",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(254))
    topic: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class SiteMedia(Base):
    """One uploaded photo. Files: ``{SITE_MEDIA_DIR}/{id}-{w}.webp`` for each w in
    ``widths``. AUTOINCREMENT so an id is never reused after a delete: the files
    are served as immutable, so a reused id would show a stale cached photo."""

    __tablename__ = "site_media"
    __table_args__ = (
        CheckConstraint("width > 0 AND height > 0", name="ck_site_media_dimensions"),
        CheckConstraint("bytes > 0", name="ck_site_media_bytes"),
        CheckConstraint(
            "content_type IN ('image/jpeg', 'image/png', 'image/webp')",
            name="ck_site_media_content_type",
        ),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    original_name: Mapped[str] = mapped_column(String(255))
    #: What the upload actually was (sniffed), not what it claimed to be.
    content_type: Mapped[str] = mapped_column(String(32))
    #: Upright (post-EXIF-rotation) pixel size of the original.
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    #: Size of the uploaded file.
    bytes: Mapped[int] = mapped_column(Integer)
    alt: Mapped[str] = mapped_column(Text, default="")
    #: Comma-separated variant widths that exist on disk, ascending, e.g. "480,960,1600".
    widths: Mapped[str] = mapped_column(String(32))
    #: Tiny blurred WebP as a data: URI, shown while the real image loads.
    blur: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)

    @property
    def width_list(self) -> list[int]:
        return [int(w) for w in self.widths.split(",") if w]


class SiteSlotItem(Base):
    """One photo placed in a slot (config/slots.toml) at a position."""

    __tablename__ = "site_slot_item"
    __table_args__ = (
        UniqueConstraint("slot_key", "position", name="uq_site_slot_item_slot_key_position"),
        CheckConstraint("position >= 0", name="ck_site_slot_item_position"),
        CheckConstraint("focal_x >= 0 AND focal_x <= 1", name="ck_site_slot_item_focal_x"),
        CheckConstraint("focal_y >= 0 AND focal_y <= 1", name="ck_site_slot_item_focal_y"),
        Index("ix_site_slot_item_media_id", "media_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slot_key: Mapped[str] = mapped_column(String(80))
    position: Mapped[int] = mapped_column(Integer)
    media_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("site_media.id", name="fk_site_slot_item_media_id", ondelete="RESTRICT"),
    )
    alt_override: Mapped[str | None] = mapped_column(Text)
    focal_x: Mapped[float] = mapped_column(Float, default=0.5)
    focal_y: Mapped[float] = mapped_column(Float, default=0.5)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)

    media: Mapped[SiteMedia] = relationship(lazy="joined")


# --- engine -------------------------------------------------------------------


def create_db_engine(url: str) -> Engine:
    engine = create_engine(url, future=True)
    if engine.dialect.name != "sqlite":
        return engine

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: Any, _record: Any) -> None:
        # Take transaction control away from pysqlite so BEGIN is ours to emit:
        # booking creation needs BEGIN IMMEDIATE (write lock up front), which
        # pysqlite's implicit deferred BEGIN cannot give.
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn: Any) -> None:
        mode = conn.get_execution_options().get("sqlite_begin", "DEFERRED")
        conn.exec_driver_sql(f"BEGIN {mode}")

    return engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_db_engine(get_settings().database_url)


@contextmanager
def session_scope(*, immediate: bool = False) -> Iterator[Session]:
    """Commit on success, roll back on error. ``immediate`` takes SQLite's write
    lock at BEGIN, so a read-check-write sequence cannot interleave with another."""
    engine = get_engine()
    if immediate:
        engine = engine.execution_options(sqlite_begin="IMMEDIATE")
    with Session(engine, expire_on_commit=False) as session:
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
