"""Shared finance helpers: errors, months, the settings row, working days.

Everything here is sync and small. Money is integer pence throughout; percentages are
basis points (integers). No float ever touches a figure (invariant 11).
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from sqlalchemy.orm import Session

from cafeops.db.models.finance import FinanceSetting
from cafeops.services.actor import require_actor

__all__ = [
    "UNSET",
    "WEEKDAY_ABBR",
    "FinanceConflict",
    "FinanceRefused",
    "Period",
    "Unset",
    "add_working_days",
    "bank_holidays",
    "clean_text",
    "editor",
    "finance_settings",
    "month_key",
    "month_label",
    "month_long",
    "month_range",
    "month_start",
    "ordinal",
    "parse_month",
    "parse_period",
    "prev_month",
    "require_pence",
]


class FinanceConflict(Exception):
    """The write collides with something that exists: a duplicate date, a mirrored field.

    The API answers 409 with the message verbatim.
    """


class FinanceRefused(ValueError):
    """The request is malformed or breaks a finance rule. The API answers 422."""


class Unset:
    """Sentinel: a PATCH field that was not sent (distinct from an explicit null)."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET = Unset()

#: Monday-first weekday abbreviations, indexed by `date.weekday()`. The one copy.
WEEKDAY_ABBR: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

#: A reporting period: one month (its first day) or everything (None).
Period = date | None


def month_start(d: date) -> date:
    return d.replace(day=1)


def month_range(month: date) -> tuple[date, date]:
    """First and last day of the month containing `month`."""
    first = month.replace(day=1)
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    return first, last


def prev_month(month: date) -> date:
    first = month.replace(day=1)
    return (first - timedelta(days=1)).replace(day=1)


def month_key(month: date) -> str:
    return f"{month.year:04d}-{month.month:02d}"


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def month_label(month: date) -> str:
    """'Apr 26' -- the design's `mLabel`."""
    return f"{_MONTHS[month.month - 1]} {month.year % 100:02d}"


def month_long(month: date) -> str:
    """'April 2026'."""
    return f"{calendar.month_name[month.month]} {month.year}"


def parse_month(raw: str) -> date:
    """'YYYY-MM' -> first day of that month. Refuses anything else."""
    text = raw.strip()
    try:
        year_s, month_s = text.split("-")
        if len(year_s) != 4 or len(month_s) != 2:
            raise ValueError
        return date(int(year_s), int(month_s), 1)
    except ValueError as exc:
        raise FinanceRefused(f"{raw!r}: expected a month as YYYY-MM") from exc


def parse_period(raw: str | None) -> Period:
    """'YYYY-MM' | 'all' | None (= all)."""
    if raw is None or raw.strip().lower() in {"", "all"}:
        return None
    return parse_month(raw)


def ordinal(n: int) -> str:
    """1st, 2nd, 3rd, 11th, 21st, 31st. The design printed '31th'."""
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def clean_text(raw: str | None) -> str | None:
    """Trim; blank becomes None."""
    if raw is None:
        return None
    text = raw.strip()
    return text or None


def require_pence(value: int | None, field: str, *, allow_zero: bool = True) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise FinanceRefused(f"{field}: money is integer pence")
    if value < 0:
        raise FinanceRefused(f"{field}: cannot be negative")
    if not allow_zero and value == 0:
        raise FinanceRefused(f"{field}: must be more than zero")
    if value > 100_000_000:
        raise FinanceRefused(f"{field}: over £1,000,000 -- check the figure")


def editor(operator: str | None) -> str:
    """Who edited a row. Never None after a person's edit: the workbook importer reads a
    non-null `updated_by` as "edited in the app, leave it alone".

    Deliberately NOT `services.actor.require_actor`'s refusal: an unnamed finance edit is
    accepted and signed "web app (no name given)" -- an owner decision for the money
    screens, kept as it was. A given name follows the actor rule (trimmed, 120 chars).
    """
    try:
        return require_actor(operator)
    except ValueError:
        return "web app (no name given)"


def finance_settings(session: Session) -> FinanceSetting:
    """The single settings row. The migration seeds it; recreate defaults if it is gone."""
    row = session.get(FinanceSetting, 1)
    if row is None:
        row = FinanceSetting(id=1)
        session.add(row)
        session.flush()
    return row


# --------------------------------------------------------------------------
# working days: weekends and England & Wales bank holidays
# --------------------------------------------------------------------------
#
# Card payouts travel on the Bacs / Faster Payments calendar, which follows the
# England & Wales bank holidays even for a Dundee account. The design skipped weekends
# only, which puts Easter and Christmas payouts on days no money moves.


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _first_monday(year: int, month: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(7 - d.weekday()) % 7)


def _last_monday(year: int, month: int) -> date:
    d = date(year, month, calendar.monthrange(year, month)[1])
    return d - timedelta(days=d.weekday())


def bank_holidays(year: int) -> frozenset[date]:
    """England & Wales bank holidays, with weekend substitutes."""
    out: set[date] = set()
    new_year = date(year, 1, 1)
    while new_year.weekday() >= 5:
        new_year += timedelta(days=1)
    out.add(new_year)
    easter = _easter_sunday(year)
    out.add(easter - timedelta(days=2))
    out.add(easter + timedelta(days=1))
    out.add(_first_monday(year, 5))
    out.add(_last_monday(year, 5))
    out.add(_last_monday(year, 8))
    christmas, boxing = date(year, 12, 25), date(year, 12, 26)
    if christmas.weekday() == 5:  # Sat -> Mon, Boxing Sun -> Tue
        out.update({date(year, 12, 27), date(year, 12, 28)})
    elif christmas.weekday() == 6:  # Sun -> Tue (Boxing Mon stays)
        out.update({boxing, date(year, 12, 27)})
    elif boxing.weekday() == 5:  # Fri Christmas, Sat Boxing -> Mon
        out.update({christmas, date(year, 12, 28)})
    else:
        out.update({christmas, boxing})
    return frozenset(out)


def _is_working_day(d: date) -> bool:
    return d.weekday() < 5 and d not in bank_holidays(d.year)


def add_working_days(start: date, days: int) -> date:
    """`days` banking days after `start` (the sale day itself never counts)."""
    d = start
    remaining = days
    while remaining > 0:
        d += timedelta(days=1)
        if _is_working_day(d):
            remaining -= 1
    return d
