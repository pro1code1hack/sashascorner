"""Table availability and the booking lifecycle.

Capacity model: a booking holds ``party`` covers for ``duration_minutes`` from its
start. A slot is available for a party iff, across [slot, slot + duration), the
peak number of covers held by overlapping bookings plus the party fits in
``covers_per_slot`` -- and the slot is far enough ahead, within the horizon, and
the café is open that day.

Every status except ``cancelled`` holds its covers. ``arrived`` and ``no_show``
are only ever set on past (or current) bookings, where freeing them could not
make an earlier time bookable anyway; a cancellation frees them at once.

Admin (phone-in) bookings skip the lead-time and horizon rules and the online
``max_party`` limit, but not capacity, unless the owner overrides it.
"""

from __future__ import annotations

import datetime as dt
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sashasite.config import CafeFacts, get_settings
from sashasite.db import SiteBooking, session_scope, utcnow
from sashasite.schemas import (
    AdminBookingIn,
    AdminBookingOut,
    AdminBookingPatchIn,
    AvailabilityOut,
    BookingIn,
    BookingSource,
    BookingStatus,
    DayOut,
    SlotCoversOut,
    SlotOut,
)

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
    closure = cafe.closure(day)
    if closure is not None:
        return True, closure.note or "We're closed on this date."
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


#: Statuses that hold their covers (everything but a cancellation).
HOLDING = ("confirmed", "arrived", "no_show")


def _held_on(session: Session, day: dt.date, exclude_id: int | None = None) -> list[_Held]:
    """Bookings holding covers that could overlap ``day``: the day itself and the
    day before (a late booking's hold can run past midnight)."""
    q = select(SiteBooking).where(
        SiteBooking.local_date.in_([day - dt.timedelta(days=1), day]),
        SiteBooking.status.in_(HOLDING),
    )
    if exclude_id is not None:
        q = q.where(SiteBooking.id != exclude_id)
    return [_Held(r.starts_at, r.ends_at, r.party) for r in session.scalars(q)]


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
                    source="web",
                    created_at=now,
                    updated_at=now,
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


def list_bookings(
    start: dt.date | None, end: dt.date | None, status: str | None = None
) -> list[SiteBooking]:
    with session_scope() as session:
        q = select(SiteBooking)
        if start is not None:
            q = q.where(SiteBooking.local_date >= start)
        if end is not None:
            q = q.where(SiteBooking.local_date <= end)
        if status is not None:
            q = q.where(SiteBooking.status == status)
        q = q.order_by(SiteBooking.local_date, SiteBooking.local_time, SiteBooking.id)
        return list(session.scalars(q))


# --- admin ----------------------------------------------------------------------


class NotFound(Exception):
    pass


def _check_open_at(cafe: CafeFacts, day: dt.date, at: dt.time) -> None:
    """Admin bookings may be at any minute (a phone-in for 09:15 is fine), but only
    on an open day and within opening hours."""
    closure = cafe.closure(day)
    if closure is not None:
        raise NotBookable(
            "The café is closed on this date" + (f": {closure.note}" if closure.note else ".")
        )
    hours = cafe.day(day.weekday())
    if hours.closed or hours.open is None or hours.close is None:
        raise NotBookable("The café is closed on this day of the week.")
    if not (hours.open <= at < hours.close):
        raise NotBookable(
            f"That time is outside opening hours ({hours.open:%H:%M}-{hours.close:%H:%M})."
        )


def _check_capacity(
    session: Session,
    cafe: CafeFacts,
    day: dt.date,
    start: dt.datetime,
    party: int,
    exclude_id: int | None,
) -> None:
    end = start + dt.timedelta(minutes=cafe.booking.duration_minutes)
    peak = _peak(_held_on(session, day, exclude_id), start, end)
    cap = cafe.booking.covers_per_slot
    if peak + party > cap:
        raise SlotFull(
            f"Over capacity: {peak} of {cap} covers are already held then, "
            f"so {party} more would make {peak + party}. Override capacity to book anyway."
        )


def admin_create(cafe: CafeFacts, body: AdminBookingIn) -> SiteBooking:
    """A phone-in booking. No lead-time, horizon or online max-party rule; capacity
    applies unless ``override_capacity``. Same IMMEDIATE transaction as the web path."""
    at = dt.time.fromisoformat(body.time)
    _check_open_at(cafe, body.date, at)
    for _attempt in range(3):
        try:
            with session_scope(immediate=True) as session:
                start = _start_utc(body.date, at)
                if not body.override_capacity:
                    _check_capacity(session, cafe, body.date, start, body.party, None)
                now = utcnow()
                booking = SiteBooking(
                    reference=_reference(session),
                    manage_token=secrets.token_urlsafe(24),
                    status="confirmed",
                    name=body.name,
                    email=str(body.email) if body.email else "",
                    phone=body.phone,
                    party=body.party,
                    local_date=body.date,
                    local_time=body.time,
                    starts_at=start,
                    ends_at=start + dt.timedelta(minutes=cafe.booking.duration_minutes),
                    notes=body.notes,
                    source="admin",
                    created_at=now,
                    updated_at=now,
                )
                session.add(booking)
                session.flush()
                return booking
        except IntegrityError:
            continue
    raise RuntimeError("could not create booking")


def admin_patch(
    cafe: CafeFacts, booking_id: int, body: AdminBookingPatchIn
) -> tuple[SiteBooking, dict[str, list[object]]]:
    """Apply a status/notes/party/date/time change. Anything that adds load (a
    move, a bigger party, un-cancelling) re-checks capacity under BEGIN IMMEDIATE,
    unless ``override_capacity``. Returns the booking and {field: [before, after]}."""
    fields = body.model_dump(exclude_unset=True, exclude={"override_capacity"})
    with session_scope(immediate=True) as session:
        b = session.get(SiteBooking, booking_id)
        if b is None:
            raise NotFound
        new_date = fields.get("date") or b.local_date
        new_time = fields.get("time") or b.local_time
        new_party = fields.get("party") or b.party
        new_status = fields.get("status") or b.status
        moved = new_date != b.local_date or new_time != b.local_time
        at = dt.time.fromisoformat(new_time)
        if moved:
            _check_open_at(cafe, new_date, at)
        adds_load = new_status != "cancelled" and (
            moved or new_party > b.party or b.status == "cancelled"
        )
        start = _start_utc(new_date, at)
        if adds_load and not body.override_capacity:
            _check_capacity(session, cafe, new_date, start, new_party, b.id)

        changes: dict[str, list[object]] = {}

        def put(name: str, before: object, after: object) -> None:
            if before != after:
                changes[name] = [before, after]

        put("status", b.status, new_status)
        put("party", b.party, new_party)
        put("date", b.local_date.isoformat(), new_date.isoformat())
        put("time", b.local_time, new_time)
        if "notes" in fields:
            put("notes", b.notes, fields["notes"])
            b.notes = fields["notes"]
        if new_status != b.status:
            b.cancelled_at = utcnow() if new_status == "cancelled" else None
        b.status = new_status
        b.party = new_party
        if moved:
            b.local_date = new_date
            b.local_time = new_time
            b.starts_at = start
            b.ends_at = start + dt.timedelta(minutes=cafe.booking.duration_minutes)
        if changes:
            b.updated_at = utcnow()
        session.flush()
        return b, changes


def occupancy(held: Sequence[_Held], at: dt.datetime) -> int:
    """Covers seated at the instant ``at``."""
    return sum(h.party for h in held if h.start <= at < h.end)


def day_view(cafe: CafeFacts, day: dt.date) -> DayOut:
    """Every booking that day (all statuses) and, per slot, the covers seated at
    that moment against capacity."""
    closure = cafe.closure(day)
    hours = cafe.day(day.weekday())
    closed = closure is not None or hours.closed
    reason = None
    if closure is not None:
        reason = closure.note or "Closed on this date."
    elif hours.closed:
        reason = "Closed on this day of the week."
    cap = cafe.booking.covers_per_slot
    with session_scope() as session:
        held = _held_on(session, day)
    bookings = list_bookings(day, day)
    slots = [
        SlotCoversOut(
            time=t.strftime("%H:%M"),
            covers_booked=occupancy(held, _start_utc(day, t)),
            capacity=cap,
        )
        for t in slot_times(cafe, day)
    ]
    return DayOut(
        date=day,
        closed=closed,
        reason=reason,
        capacity=cap,
        slots=slots,
        bookings=[admin_out(b) for b in bookings],
    )


def admin_out(b: SiteBooking) -> AdminBookingOut:
    return AdminBookingOut(
        id=b.id,
        reference=b.reference,
        name=b.name,
        email=b.email or None,
        phone=b.phone,
        party=b.party,
        date=b.local_date,
        time=b.local_time,
        status=cast(BookingStatus, b.status),  # the CHECK constraint guarantees the set
        notes=b.notes,
        source=cast(BookingSource, b.source),
        created_at=b.created_at,
        cancelled_at=b.cancelled_at,
    )
