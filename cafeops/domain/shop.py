"""Order online: the arithmetic and the rules, with nothing else in them.

docs/shop/CONTRACT.md §3. Pure: no SQLAlchemy, no I/O, no config import. Times are
handed in as LOCAL naive or aware datetimes by the caller (`services/shop/slots.py`
converts with `settings.tz`); the functions here never ask what the timezone is.

What lives here:

- the vocabularies the admin edits against (`ALLERGENS`, `DIETARY`), the order-code
  alphabet and the size labels;
- **slot maths** (§3.4): a day's ordering window from the shop's own hours, closures,
  lead time and the last-order margin, and the slot starts inside it;
- **price maths** (§3.3): a line's unit price is the size price plus the option deltas,
  a line's total is unit x qty, and the reward takes ONE unit off the priciest eligible
  line, capped by the programme's `reward_max_price_pence`;
- **status transitions** (§3.8) as data, so the API, the admin and the scheduler cannot
  disagree about which moves are legal.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from cafeops.domain.enums import OrderStatus

__all__ = [
    "ALLERGENS",
    "ALLERGENS_NONE",
    "CODE_ALPHABET",
    "CUSTOMER_CANCELLABLE",
    "DIETARY",
    "LIVE_STATUSES",
    "SIZE_LABELS",
    "SIZE_ORDER",
    "STATUS_LABELS",
    "STATUS_STEPS",
    "TRANSITIONS",
    "DayHours",
    "DayWindow",
    "RewardPick",
    "allergens_problem",
    "allergens_state",
    "can_transition",
    "choose_reward_line",
    "day_window",
    "display_code",
    "earliest_asap",
    "is_open_at",
    "line_total",
    "line_unit_price",
    "new_code",
    "next_open",
    "next_open_label",
    "normalise_code",
    "parse_closures",
    "parse_hours",
    "selection_problem",
    "slot_starts",
    "slugify",
    "status_step",
    "validate_closures",
    "validate_hours",
]

# --------------------------------------------------------------------------
# vocabularies
# --------------------------------------------------------------------------

#: `"none"` is a positive claim -- somebody checked and there are no allergens. An
#: EMPTY list is not that claim: it means nobody has said (`allergens_state`).
ALLERGENS: tuple[str, ...] = (
    "milk",
    "gluten",
    "nuts",
    "soya",
    "egg",
    "sesame",
    "sulphites",
    "none",
)
ALLERGENS_NONE = "none"
DIETARY: tuple[str, ...] = (
    "vegan",
    "vegetarian",
    "gluten-free",
    "decaf-available",
    "contains-caffeine",
)

#: No 0/O/1/I: the code is read out at the counter.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6

SIZE_LABELS: Mapping[str, str] = {"S": "Small", "M": "Medium", "XL": "Large", "ONE": ""}
SIZE_ORDER: Mapping[str, int] = {"S": 0, "M": 1, "XL": 2, "ONE": 3}


def allergens_state(allergens: Iterable[str]) -> str:
    """'unknown' (nobody said), 'none' (confirmed: no allergens) or 'listed'.

    The site used to render an empty list as "no listed allergens", which is a
    food-safety claim nobody made. The three states keep "we don't know" honest.
    """
    values = [str(a).strip().lower() for a in allergens]
    if not values:
        return "unknown"
    if values == [ALLERGENS_NONE]:
        return "none"
    return "listed"


def allergens_problem(allergens: Iterable[str]) -> str | None:
    """Why this list cannot be saved, or None. `"none"` beside a real allergen is a
    contradiction, not a list."""
    values = {str(a).strip().lower() for a in allergens}
    if ALLERGENS_NONE in values and len(values) > 1:
        return "'none' means no allergens at all; it cannot be combined with other allergens"
    return None


def new_code(rng: Any = secrets) -> str:
    """Six characters from `CODE_ALPHABET`. Uniqueness is the database's (unique column);
    the caller retries on a collision."""
    return "".join(rng.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def display_code(code: str) -> str:
    return f"SC-{code}"


def normalise_code(raw: str) -> str | None:
    """`sc-abc123`, `SC-ABC123`, `ABC123` -> `ABC123`; anything else None."""
    text = raw.strip().upper().replace(" ", "")
    if text.startswith("SC-"):
        text = text[3:]
    if len(text) != CODE_LENGTH or any(ch not in CODE_ALPHABET for ch in text):
        return None
    return text


def slugify(name: str) -> str:
    s = name.casefold().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


# --------------------------------------------------------------------------
# status transitions (§3.8)
# --------------------------------------------------------------------------

TRANSITIONS: Mapping[str, frozenset[str]] = {
    "PENDING_PAYMENT": frozenset({"NEW", "CANCELLED"}),
    "NEW": frozenset({"ACCEPTED", "PREPARING", "READY", "REJECTED", "CANCELLED"}),
    "ACCEPTED": frozenset({"PREPARING", "READY", "CANCELLED"}),
    "PREPARING": frozenset({"READY", "CANCELLED"}),
    "READY": frozenset({"COLLECTED", "CANCELLED"}),
    "COLLECTED": frozenset(),
    "CANCELLED": frozenset(),
    "REJECTED": frozenset(),
}

#: The customer may cancel only here.
CUSTOMER_CANCELLABLE: frozenset[str] = frozenset({"NEW", "PENDING_PAYMENT"})

#: "Live" for the admin board: not yet collected, not cancelled or rejected.
LIVE_STATUSES: tuple[str, ...] = ("PENDING_PAYMENT", "NEW", "ACCEPTED", "PREPARING", "READY")

STATUS_STEPS: Mapping[str, int] = {
    "PENDING_PAYMENT": 0,
    "NEW": 0,
    "ACCEPTED": 1,
    "PREPARING": 2,
    "READY": 3,
    "COLLECTED": 4,
    "CANCELLED": 0,
    "REJECTED": 0,
}

STATUS_LABELS: Mapping[str, str] = {
    "PENDING_PAYMENT": "Awaiting payment",
    "NEW": "Order received",
    "ACCEPTED": "Accepted",
    "PREPARING": "Being made",
    "READY": "Ready to collect",
    "COLLECTED": "Collected",
    "CANCELLED": "Cancelled",
    "REJECTED": "Declined",
}

#: The tables above are keyed by the status NAME so the domain stays free of ORM types
#: at call sites; this is the guard that keeps them the same set as `OrderStatus`.
_STATUS_NAMES = frozenset(m.name for m in OrderStatus)
for _table in (TRANSITIONS, STATUS_STEPS, STATUS_LABELS):
    assert frozenset(_table) == _STATUS_NAMES, "shop status tables drifted from OrderStatus"
assert CUSTOMER_CANCELLABLE <= _STATUS_NAMES and set(LIVE_STATUSES) <= _STATUS_NAMES


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, frozenset())


def status_step(status: str) -> int:
    return STATUS_STEPS.get(status, 0)


# --------------------------------------------------------------------------
# hours, closures and slots (§3.4)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DayHours:
    open: time
    close: time


@dataclass(frozen=True, slots=True)
class DayWindow:
    """One day's ordering window, or why there is none. Local, naive."""

    day: date
    open_at: datetime | None
    #: Last slot start allowed: `close - last_order_minutes_before_close`.
    last_order_at: datetime | None
    reason: str | None

    @property
    def is_open(self) -> bool:
        return self.open_at is not None and self.last_order_at is not None


_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _parse_time(raw: Any) -> time | None:
    if not isinstance(raw, str):
        return None
    match = _TIME_RE.match(raw.strip())
    if match is None:
        return None
    return time(int(match.group(1)), int(match.group(2)))


def parse_hours(raw: Iterable[Any] | None) -> dict[int, DayHours]:
    """`[{"weekday": 0, "open": "09:00", "close": "19:00"}, ...]` -> by weekday.

    Tolerant of anything stored: a malformed entry is dropped (that weekday is closed),
    which is the safe failure for a form that takes orders.
    """
    out: dict[int, DayHours] = {}
    for entry in raw or ():
        if not isinstance(entry, Mapping):
            continue
        try:
            weekday = int(entry.get("weekday", -1))
        except (TypeError, ValueError):
            continue
        opens, closes = _parse_time(entry.get("open")), _parse_time(entry.get("close"))
        if not 0 <= weekday <= 6 or opens is None or closes is None or closes <= opens:
            continue
        out[weekday] = DayHours(open=opens, close=closes)
    return out


def validate_hours(raw: Any) -> list[str]:
    """Problems with an hours list, for the admin's PUT. Empty = fine."""
    problems: list[str] = []
    if not isinstance(raw, list):
        return ["hours must be a list"]
    seen: set[int] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            problems.append(f"hours[{i}]: not an object")
            continue
        try:
            weekday = int(entry.get("weekday", -1))
        except (TypeError, ValueError):
            weekday = -1
        if not 0 <= weekday <= 6:
            problems.append(f"hours[{i}]: weekday must be 0 (Monday) to 6 (Sunday)")
        elif weekday in seen:
            problems.append(f"hours[{i}]: weekday {weekday} listed twice")
        seen.add(weekday)
        opens, closes = _parse_time(entry.get("open")), _parse_time(entry.get("close"))
        if opens is None or closes is None:
            problems.append(f"hours[{i}]: open and close must be HH:MM")
        elif closes <= opens:
            problems.append(f"hours[{i}]: close must be after open")
    return problems


def parse_closures(raw: Iterable[Any] | None) -> dict[date, str]:
    out: dict[date, str] = {}
    for entry in raw or ():
        if not isinstance(entry, Mapping):
            continue
        try:
            day = date.fromisoformat(str(entry.get("date", "")))
        except ValueError:
            continue
        out[day] = str(entry.get("note") or "").strip()
    return out


def validate_closures(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return ["closures must be a list"]
    problems: list[str] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            problems.append(f"closures[{i}]: not an object")
            continue
        try:
            date.fromisoformat(str(entry.get("date", "")))
        except ValueError:
            problems.append(f"closures[{i}]: date must be YYYY-MM-DD")
    return problems


def day_window(
    day: date,
    *,
    hours: Mapping[int, DayHours],
    closures: Mapping[date, str],
    last_order_minutes_before_close: int,
) -> DayWindow:
    if day in closures:
        note = closures[day]
        return DayWindow(day, None, None, f"Closed{': ' + note if note else ''}")
    spec = hours.get(day.weekday())
    if spec is None:
        return DayWindow(day, None, None, "Closed today")
    open_at = datetime.combine(day, spec.open)
    last = datetime.combine(day, spec.close) - timedelta(
        minutes=max(0, last_order_minutes_before_close)
    )
    if last < open_at:
        return DayWindow(day, None, None, "Closed today")
    return DayWindow(day, open_at, last, None)


def _ceil_to_slot(at: datetime, slot_minutes: int) -> datetime:
    """Round UP to the next multiple of `slot_minutes` past the hour."""
    step = max(1, slot_minutes)
    base = at.replace(second=0, microsecond=0)
    extra = base.minute % step
    if extra == 0 and base == at:
        return base
    return base + timedelta(minutes=step - extra)


def earliest_asap(now: datetime, *, lead_minutes: int, slot_minutes: int) -> datetime:
    """Earliest collection: now + lead, rounded up to a slot boundary."""
    return _ceil_to_slot(now + timedelta(minutes=max(0, lead_minutes)), slot_minutes)


def slot_starts(
    window: DayWindow, *, now: datetime, lead_minutes: int, slot_minutes: int
) -> list[datetime]:
    """Every slot start inside the day's window that is still >= now + lead."""
    if window.open_at is None or window.last_order_at is None:
        return []
    step = max(1, slot_minutes)
    first = max(window.open_at, earliest_asap(now, lead_minutes=lead_minutes, slot_minutes=step))
    first = _ceil_to_slot(first, step)
    out: list[datetime] = []
    at = first
    while at <= window.last_order_at:
        out.append(at)
        at += timedelta(minutes=step)
    return out


def is_open_at(
    at: datetime,
    *,
    hours: Mapping[int, DayHours],
    closures: Mapping[date, str],
    last_order_minutes_before_close: int,
) -> bool:
    """Taking orders at `at`: inside the day's window (last-order margin applied)."""
    window = day_window(
        at.date(),
        hours=hours,
        closures=closures,
        last_order_minutes_before_close=last_order_minutes_before_close,
    )
    if window.open_at is None or window.last_order_at is None:
        return False
    return window.open_at <= at <= window.last_order_at


def next_open(
    now: datetime,
    *,
    hours: Mapping[int, DayHours],
    closures: Mapping[date, str],
    last_order_minutes_before_close: int,
    horizon_days: int = 14,
) -> datetime | None:
    """When ordering next opens, or None when nothing opens within the horizon."""
    for offset in range(horizon_days + 1):
        day = now.date() + timedelta(days=offset)
        window = day_window(
            day,
            hours=hours,
            closures=closures,
            last_order_minutes_before_close=last_order_minutes_before_close,
        )
        if window.open_at is None or window.last_order_at is None:
            continue
        if offset == 0 and window.last_order_at < now:
            continue
        return max(window.open_at, now) if offset == 0 else window.open_at
    return None


_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def next_open_label(opens: datetime, now: datetime) -> str:
    """ "Today 09:00" / "Tomorrow 09:00" / "Mon 09:00" / "Mon 6 Oct 09:00"."""
    hhmm = opens.strftime("%H:%M")
    delta = (opens.date() - now.date()).days
    if delta == 0:
        return f"Today {hhmm}"
    if delta == 1:
        return f"Tomorrow {hhmm}"
    if delta < 7:
        return f"{_WEEKDAYS[opens.weekday()]} {hhmm}"
    return f"{_WEEKDAYS[opens.weekday()]} {opens.day} {opens.strftime('%b')} {hhmm}"


# --------------------------------------------------------------------------
# prices (§3.3) and the reward (§3.5)
# --------------------------------------------------------------------------


def line_unit_price(size_price_pence: int, option_deltas: Iterable[int]) -> int:
    return size_price_pence + sum(option_deltas)


def line_total(unit_price_pence: int, qty: int) -> int:
    return unit_price_pence * qty


@dataclass(frozen=True, slots=True)
class RewardPick:
    line_index: int | None
    discount_pence: int
    #: Why nothing was picked, when nothing was.
    reason: str | None = None


def choose_reward_line(lines: Sequence[tuple[int, bool]], *, cap_pence: int | None) -> RewardPick:
    """`lines` = (unit_price_pence, eligible) per basket line. The reward covers ONE unit
    of the priciest eligible line, up to `cap_pence`. A line over the cap is still the
    one chosen (the customer pays the difference) -- that is what the till does."""
    best: tuple[int, int] | None = None
    for index, (unit, eligible) in enumerate(lines):
        if not eligible or unit <= 0:
            continue
        if best is None or unit > best[1]:
            best = (index, unit)
    if best is None:
        return RewardPick(None, 0, "Nothing in the basket is covered by the free drink.")
    index, unit = best
    discount = unit if cap_pence is None else min(unit, cap_pence)
    return RewardPick(index, discount, None)


def selection_problem(
    *,
    group_name: str,
    kind: str,
    required: bool,
    min_select: int,
    max_select: int | None,
    chosen: int,
) -> str | None:
    """Why a set of picks in one option group is not allowed, or None."""
    if kind == "SINGLE":
        if chosen > 1:
            return f"{group_name}: choose one."
        if (required or min_select >= 1) and chosen == 0:
            return f"{group_name}: choose one."
        return None
    floor = max(min_select, 1 if required else 0)
    if chosen < floor:
        return f"{group_name}: choose at least {floor}."
    if max_select is not None and chosen > max_select:
        return f"{group_name}: choose at most {max_select}."
    return None
