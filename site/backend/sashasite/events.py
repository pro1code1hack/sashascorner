"""Events at the café (a chess night, a tasting) and their RSVPs.

Tables ``site_event`` and ``site_event_rsvp`` (migration ``site_0006``). The site
writes only these; nothing here touches an ops table.

Times: the owner enters an event in café-local wall-clock time (Europe/London);
it is stored as UTC (``starts_at`` / ``ends_at``) and served with both, so the
static build (which may run in UTC) never has to convert.

Capacity: ``capacity`` is a number of people. An RSVP holds ``party`` places until
it is cancelled. The check and the insert run under one ``BEGIN IMMEDIATE``
transaction, as bookings do, so two requests cannot both take the last places.
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    false,
    func,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from sashasite.config import get_settings
from sashasite.db import Base, SiteMedia, UTCDateTime, session_scope, utcnow

#: The most places one RSVP can hold. Bigger groups use the contact form.
MAX_PARTY = 6


class SiteEvent(Base):
    __tablename__ = "site_event"
    __table_args__ = (
        CheckConstraint("price_pence IS NULL OR price_pence >= 0", name="ck_site_event_price"),
        CheckConstraint("capacity IS NULL OR capacity > 0", name="ck_site_event_capacity"),
        CheckConstraint("ends_at IS NULL OR ends_at > starts_at", name="ck_site_event_ends_after"),
        Index("ix_site_event_starts_at", "starts_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(120))
    starts_at: Mapped[dt.datetime] = mapped_column(UTCDateTime())
    ends_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime())
    description: Mapped[str] = mapped_column(Text, default="")
    #: NULL = free (or "pay on the night" -- the description says which).
    price_pence: Mapped[int | None] = mapped_column(Integer)
    #: People, not RSVPs. NULL = no limit.
    capacity: Mapped[int | None] = mapped_column(Integer)
    #: A photo from the site's media library (site_media), optional.
    image_media_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("site_media.id", name="fk_site_event_image_media_id", ondelete="SET NULL"),
    )
    published: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)


class SiteEventRsvp(Base):
    __tablename__ = "site_event_rsvp"
    __table_args__ = (
        CheckConstraint("party >= 1 AND party <= 6", name="ck_site_event_rsvp_party"),
        CheckConstraint(
            "email IS NOT NULL OR phone IS NOT NULL", name="ck_site_event_rsvp_contact"
        ),
        Index("ix_site_event_rsvp_event_id", "event_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("site_event.id", name="fk_site_event_rsvp_event_id", ondelete="CASCADE"),
    )
    name: Mapped[str] = mapped_column(String(80))
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(30))
    party: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), default=utcnow)
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime())


# --- time --------------------------------------------------------------------------


def local_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().local_timezone)


def to_utc(day: dt.date, hhmm: str) -> dt.datetime:
    """Café wall-clock -> aware UTC. zoneinfo resolves DST gaps/folds (fold=0)."""
    at = dt.time.fromisoformat(hhmm)
    return dt.datetime.combine(day, at, tzinfo=local_tz()).astimezone(dt.UTC)


def local_parts(at: dt.datetime) -> tuple[dt.date, str]:
    loc = at.astimezone(local_tz())
    return loc.date(), loc.strftime("%H:%M")


def slugify(text: str) -> str:
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:60].strip("-") or "event"


# --- queries -----------------------------------------------------------------------


def seats_taken(session: Session, event_id: int) -> int:
    n = session.scalar(
        select(func.coalesce(func.sum(SiteEventRsvp.party), 0)).where(
            SiteEventRsvp.event_id == event_id, SiteEventRsvp.cancelled_at.is_(None)
        )
    )
    return int(n or 0)


def rsvp_count(session: Session, event_id: int) -> int:
    n = session.scalar(
        select(func.count(SiteEventRsvp.id)).where(
            SiteEventRsvp.event_id == event_id, SiteEventRsvp.cancelled_at.is_(None)
        )
    )
    return int(n or 0)


def is_over(e: SiteEvent, now: dt.datetime) -> bool:
    """Past once it has ended; an event with no end time is past once it starts
    plus three hours (a sensible evening)."""
    end = e.ends_at or (e.starts_at + dt.timedelta(hours=3))
    return end <= now


def upcoming(session: Session, now: dt.datetime | None = None) -> list[SiteEvent]:
    now = now or utcnow()
    # Coarse filter in SQL (anything that started less than a day ago), exact in Python.
    rows = session.scalars(
        select(SiteEvent)
        .where(
            SiteEvent.published.is_(True),
            SiteEvent.starts_at >= now - dt.timedelta(days=1),
        )
        .order_by(SiteEvent.starts_at, SiteEvent.id)
    )
    return [e for e in rows if not is_over(e, now)]


def image_of(session: Session, e: SiteEvent) -> SiteMedia | None:
    if e.image_media_id is None:
        return None
    return session.get(SiteMedia, e.image_media_id)


# --- RSVP --------------------------------------------------------------------------


class EventNotFound(Exception):
    """No published, upcoming event with that slug (-> 404)."""


class EventFull(Exception):
    """Not enough places left for the party (-> 409)."""

    def __init__(self, left: int) -> None:
        self.left = left
        super().__init__(
            "Sorry, this event is full."
            if left <= 0
            else f"Only {left} place{'s' if left != 1 else ''} left. Try a smaller party."
        )


@dataclass(frozen=True)
class RsvpResult:
    event_id: int
    slug: str
    title: str
    starts_at: dt.datetime
    rsvp_id: int
    name: str
    email: str | None
    phone: str | None
    party: int
    note: str | None
    #: None when the event has no capacity.
    places_left: int | None


def create_rsvp(
    slug: str,
    *,
    name: str,
    email: str | None,
    phone: str | None,
    party: int,
    note: str | None,
    now: dt.datetime | None = None,
) -> RsvpResult:
    now = now or utcnow()
    with session_scope(immediate=True) as session:
        e = session.scalar(select(SiteEvent).where(SiteEvent.slug == slug))
        if e is None or not e.published or e.starts_at <= now:
            # A started event takes no more RSVPs: people just come along.
            raise EventNotFound
        left: int | None = None
        if e.capacity is not None:
            left = e.capacity - seats_taken(session, e.id)
            if party > left:
                raise EventFull(left)
            left -= party
        r = SiteEventRsvp(
            event_id=e.id,
            name=name,
            email=email,
            phone=phone,
            party=party,
            note=note,
            created_at=now,
        )
        session.add(r)
        session.flush()
        return RsvpResult(
            event_id=e.id,
            slug=e.slug,
            title=e.title,
            starts_at=e.starts_at,
            rsvp_id=r.id,
            name=r.name,
            email=r.email,
            phone=r.phone,
            party=r.party,
            note=r.note,
            places_left=left,
        )
