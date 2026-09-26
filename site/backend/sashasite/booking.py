"""Table availability and the booking lifecycle.

Capacity model: a booking holds ``party`` covers for ``duration_minutes`` from its
start. A slot is available for a party iff, across [slot, slot + duration), the
peak number of covers held by overlapping CONFIRMED bookings plus the party fits
in ``covers_per_slot`` -- and the slot is far enough ahead, within the horizon,
and the café is open that day.
"""

from __future__ import annotations

import datetime as dt
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sashasite.config import CafeFacts, get_settings
from sashasite.db import SiteBooking, session_scope, utcnow
from sashasite.schemas import AvailabilityOut, BookingIn, SlotOut

#: No 0/O, 1/I/L, 2/Z, 5/S, 8/B -- readable over the phone and off a screen.
REF_ALPHABET = "ACDEFGHJKMNPQRTUVWXY34679"


def local_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().local_timezone)


class NotBookable(Exception):
    """The requested date/time is not a bookable slot at all (-> 422)."""


class SlotFull(Exception):
    """The slot exists but no longer has room for the party (-> 409)."""


@dataclass(frozen=True)
class _Held:
    start: dt.datetime
    end: dt.datetime
    party: int


def _day_state(cafe: CafeFacts, day: dt.date, now: dt.datetime) -> tuple[bool, str | None]:
    """(closed, reason). ``closed`` is only for days the café itself is shut."""
    today = now.astimezone(local_tz()).date()
    if day in cafe.booking.closed_dates:
        return True, "We're closed on this date."
    if cafe.day(day.weekday()).closed:
        return True, "We're closed on this day of the week."
    if day < today:
        return False, "That date has passed."
    if (day - today).days > cafe.booking.horizon_days:
        return False, f"Bookings open {cafe.booking.horizon_days} days ahead."
    return False, None


def slot_times(cafe: CafeFacts, day: dt.date) -> list[dt.time]:
    hours = cafe.day(day.weekday())
    if hours.closed or hours.open is None or hours.close is None:
        return []
    tz = local_tz()
    start = dt.datetime.combine(day, hours.open, tzinfo=tz)
    close = dt.datetime.combine(day, hours.close, tzinfo=tz)
    last = close - dt.timedelta(minutes=cafe.booking.last_seating_before_close_minutes)
    step = dt.timedelta(minutes=cafe.booking.slot_minutes)
    out: list[dt.time] = []
    t = start
    while t <= last:
        out.append(t.time())
        t += step
    return out


def _held_on(session: Session, day: dt.date) -> list[_Held]:
    rows = session.scalars(
        select(SiteBooking).where(SiteBooking.local_date == day, SiteBooking.status == "confirmed")
    )
    return [_Held(r.starts_at, r.ends_at, r.party) for r in rows]


def _peak(held: Sequence[_Held], start: dt.datetime, end: dt.datetime) -> int:
    """Max concurrent covers during [start, end). Occupancy only rises at a
    booking's start, so checking the window start and every start inside it
    is exhaustive."""
    overlapping = [h for h in held if h.start < end and h.end > start]
    points = [start] + [h.start for h in overlapping if start <= h.start < end]
    return max(
        (sum(h.party for h in overlapping if h.start <= p < h.end) for p in points), default=0
    )


def _start_utc(day: dt.date, at: dt.time) -> dt.datetime:
    # Wall-clock -> aware local -> UTC. zoneinfo resolves DST gaps/folds (fold=0).
    return dt.datetime.combine(day, at, tzinfo=local_tz()).astimezone(dt.UTC)


def availability(
    session: Session, cafe: CafeFacts, day: dt.date, party: int, now: dt.datetime | None = None
) -> AvailabilityOut:
    now = now or utcnow()
    closed, reason = _day_state(cafe, day, now)
    if closed:
        return AvailabilityOut(date=day, party=party, closed=True, reason=reason, slots=[])
    rules = cafe.booking
    held = _held_on(session, day)
    earliest = now + dt.timedelta(minutes=rules.min_lead_minutes)
    dur = dt.timedelta(minutes=rules.duration_minutes)
    slots: list[SlotOut] = []
    for t in slot_times(cafe, day):
        start = _start_utc(day, t)
        remaining = max(rules.covers_per_slot - _peak(held, start, start + dur), 0)
        ok = reason is None and start >= earliest and remaining >= party
        slots.append(SlotOut(time=t.strftime("%H:%M"), available=ok, remaining_covers=remaining))
    if reason is None and slots and not any(s.available for s in slots):
        last_start = _start_utc(day, slot_times(cafe, day)[-1])
        if last_start < earliest:
            reason = "There are no more bookable times on this date."
        else:
            reason = "No tables left for that party size on this date."
    return AvailabilityOut(date=day, party=party, closed=False, reason=reason, slots=slots)


def _reference(session: Session) -> str:
    for _ in range(50):
        ref = "SC-" + "".join(secrets.choice(REF_ALPHABET) for _ in range(4))
        if session.scalar(select(SiteBooking.id).where(SiteBooking.reference == ref)) is None:
            return ref
    raise RuntimeError("could not allocate a unique booking reference")


def create_booking(cafe: CafeFacts, body: BookingIn) -> SiteBooking:
    """Check and write under one IMMEDIATE transaction: SQLite's write lock is
    taken at BEGIN, so two requests cannot both see the last seats free."""
    if body.party > cafe.booking.max_party:
        raise NotBookable(
            f"Online bookings are for up to {cafe.booking.max_party} people — "
            "for larger groups please use the contact form."
        )
    try:
        at = dt.time.fromisoformat(body.time)
    except ValueError as exc:
        raise NotBookable("That isn't a valid time.") from exc

    for _attempt in range(3):
        try:
            with session_scope(immediate=True) as session:
                now = utcnow()
                _closed, day_reason = _day_state(cafe, body.date, now)
                if day_reason is not None:
                    raise NotBookable(day_reason)
                start = _start_utc(body.date, at)
                if at not in slot_times(cafe, body.date):
                    raise NotBookable("That time isn't a bookable slot.")
                if start < now + dt.timedelta(minutes=cafe.booking.min_lead_minutes):
                    raise NotBookable(
                        f"Bookings need at least {cafe.booking.min_lead_minutes} minutes' notice."
                    )
                avail = availability(session, cafe, body.date, body.party, now)
                slot = next(s for s in avail.slots if s.time == body.time)
                if not slot.available:
                    raise SlotFull("That time has just filled up — please pick another.")
                booking = SiteBooking(
                    reference=_reference(session),
                    manage_token=secrets.token_urlsafe(24),
                    status="confirmed",
                    name=body.name,
                    email=str(body.email),
                    phone=body.phone,
                    party=body.party,
                    local_date=body.date,
                    local_time=body.time,
                    starts_at=start,
                    ends_at=start + dt.timedelta(minutes=cafe.booking.duration_minutes),
                    notes=body.notes,
                    created_at=utcnow(),
                )
                session.add(booking)
                session.flush()
                return booking
        except IntegrityError:
            continue  # reference/token collision raced in; try again
    raise RuntimeError("could not create booking")


def get_booking(token: str) -> SiteBooking | None:
    with session_scope() as session:
        return session.scalar(select(SiteBooking).where(SiteBooking.manage_token == token))


class BookingPassed(Exception):
    pass


def cancel_booking(token: str) -> tuple[SiteBooking, bool] | None:
    """Returns (booking, changed). Idempotent: cancelling twice is not an error."""
    with session_scope(immediate=True) as session:
        b = session.scalar(select(SiteBooking).where(SiteBooking.manage_token == token))
        if b is None:
            return None
        if b.status == "cancelled":
            return b, False
        if b.starts_at <= utcnow():
            raise BookingPassed("This booking's time has already passed.")
        b.status = "cancelled"
        b.cancelled_at = utcnow()
        session.flush()
        return b, True


def list_bookings(start: dt.date | None, end: dt.date | None) -> list[SiteBooking]:
    with session_scope() as session:
        q = select(SiteBooking)
        if start is not None:
            q = q.where(SiteBooking.local_date >= start)
        if end is not None:
            q = q.where(SiteBooking.local_date <= end)
        q = q.order_by(SiteBooking.local_date, SiteBooking.local_time, SiteBooking.id)
        return list(session.scalars(q))
