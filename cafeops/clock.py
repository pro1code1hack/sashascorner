"""The one clock. Every "now" in the application is a tz-aware UTC instant (CLAUDE.md §4).

One module so the rules have one home:

* `utcnow()` is the instant. Domain code never calls it: pure functions take `at` as
  an argument, and the caller reads the clock here.
* `local_today()` is the café's calendar day, which is what "today's sales" or "this
  week" mean to the owner. Dundee is on UK time; a UTC date is wrong for an hour a day
  in winter and two in summer.
* `local_day_bounds()` turns a local date window into the half-open UTC interval a
  query needs. It is the ONLY correct way to do that: `start + timedelta(days=1)` is
  wrong twice a year, because a clock-change day is 23 or 25 hours long.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, tzinfo

__all__ = ["local_day_bounds", "local_today", "utcnow"]


def utcnow() -> datetime:
    return datetime.now(UTC)


def local_today(tz: tzinfo, *, now: datetime | None = None) -> date:
    """The calendar date in `tz` at `now` (default: this instant)."""
    return (now or utcnow()).astimezone(tz).date()


def local_day_bounds(
    since: date, until: date | None = None, *, tz: tzinfo
) -> tuple[datetime, datetime]:
    """UTC `[start, end)` covering the local days `since`..`until` inclusive.

    `until` defaults to `since`, so `local_day_bounds(d, tz=tz)` is one local day. The
    end is midnight at the START of the day after `until`, computed on the calendar and
    then converted, so DST days keep their real length.
    """
    last = until or since
    if last < since:
        raise ValueError(f"window ends before it starts: {since} > {last}")
    start = datetime.combine(since, time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(last + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    return start, end
