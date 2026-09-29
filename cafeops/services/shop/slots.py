"""Collection slots from the shop's OWN hours (CONTRACT §3.4, `GET /api/shop/slots`).

The rule, in one place so the slots route, the quote and order placement agree:
a slot is offered only inside `shop_settings.hours` for that day, not on a closure,
at least `lead_minutes` from now, no later than `close - last_order_minutes_before_close`,
within `days_ahead`, and while fewer than `max_orders_per_slot` live orders
(not CANCELLED / REJECTED) already sit in it.

Times: the domain works in naive local time (Europe/London); everything stored or
returned as an instant is tz-aware UTC. `to_local` / `to_utc` are the only conversions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import OrderStatus, ShopOrder, ShopSettings
from cafeops.domain.shop import (
    DayWindow,
    day_window,
    earliest_asap,
    is_open_at,
    next_open,
    next_open_label,
    parse_closures,
    parse_hours,
    slot_starts,
)
from cafeops.services.shop.errors import ShopError

__all__ = [
    "AsapView",
    "SlotView",
    "SlotsView",
    "local_label",
    "next_open_local",
    "open_now",
    "resolve_requested",
    "slots_for",
    "to_local",
    "to_utc",
    "window_for",
]

_DEAD = (OrderStatus.CANCELLED, OrderStatus.REJECTED)


@dataclass(frozen=True, slots=True)
class SlotView:
    at: datetime
    local: str
    available: bool


@dataclass(frozen=True, slots=True)
class AsapView:
    available: bool
    at: datetime | None
    local: str | None


@dataclass(frozen=True, slots=True)
class SlotsView:
    day: date
    open: bool
    reason: str | None
    asap: AsapView
    slots: tuple[SlotView, ...]
    days: tuple[date, ...]


def to_local(at: datetime) -> datetime:
    """A UTC instant as naive local time, for the domain."""
    return at.astimezone(settings.tz).replace(tzinfo=None)


def to_utc(local: datetime) -> datetime:
    """Naive local time back to a tz-aware UTC instant."""
    return local.replace(tzinfo=settings.tz).astimezone(UTC)


def local_label(at: datetime) -> str:
    return at.astimezone(settings.tz).strftime("%H:%M")


def window_for(shop: ShopSettings, day: date) -> DayWindow:
    return day_window(
        day,
        hours=parse_hours(shop.hours),
        closures=parse_closures(shop.closures),
        last_order_minutes_before_close=shop.last_order_minutes_before_close,
    )


def open_now(shop: ShopSettings, now: datetime) -> bool:
    return shop.enabled and is_open_at(
        to_local(now),
        hours=parse_hours(shop.hours),
        closures=parse_closures(shop.closures),
        last_order_minutes_before_close=shop.last_order_minutes_before_close,
    )


def next_open_local(shop: ShopSettings, now: datetime) -> str | None:
    local_now = to_local(now)
    opens = next_open(
        local_now,
        hours=parse_hours(shop.hours),
        closures=parse_closures(shop.closures),
        last_order_minutes_before_close=shop.last_order_minutes_before_close,
    )
    if opens is None:
        return None
    return next_open_label(opens, local_now)


def _taken(session: Session, shop: ShopSettings, day: date) -> dict[datetime, int]:
    """Live orders per slot start (UTC) on `day`."""
    start = to_utc(datetime.combine(day, datetime.min.time()))
    end = start + timedelta(days=1)
    step = max(1, shop.slot_minutes)
    counts: dict[datetime, int] = {}
    rows = session.execute(
        select(ShopOrder.requested_at, func.count(ShopOrder.id))
        .where(
            ShopOrder.requested_at >= start,
            ShopOrder.requested_at < end,
            ShopOrder.status.not_in(_DEAD),
        )
        .group_by(ShopOrder.requested_at)
    )
    for at, n in rows:
        local = to_local(at)
        floored = local.replace(minute=local.minute - local.minute % step, second=0, microsecond=0)
        key = to_utc(floored)
        counts[key] = counts.get(key, 0) + int(n)
    return counts


def _has_room(shop: ShopSettings, taken: dict[datetime, int], slot_utc: datetime) -> bool:
    if shop.max_orders_per_slot <= 0:
        return True
    return taken.get(slot_utc, 0) < shop.max_orders_per_slot


def _days(shop: ShopSettings, now_local: datetime) -> list[date]:
    out: list[date] = []
    for offset in range(max(0, shop.days_ahead) + 1):
        day = now_local.date() + timedelta(days=offset)
        window = window_for(shop, day)
        if not window.is_open:
            continue
        if offset == 0 and window.last_order_at is not None and window.last_order_at < now_local:
            continue
        out.append(day)
    return out


def slots_for(
    session: Session, shop: ShopSettings, day: date | None, *, now: datetime | None = None
) -> SlotsView:
    now = now or datetime.now(UTC)
    now_local = to_local(now)
    day = day or now_local.date()
    days = _days(shop, now_local)
    window = window_for(shop, day)
    reason: str | None = None
    if not shop.enabled:
        reason = "Online ordering is off."
    elif day < now_local.date():
        reason = "That day has passed."
    elif day not in days:
        reason = (
            window.reason
            if not window.is_open
            else "Too late today."
            if day == now_local.date()
            else f"Orders can be placed up to {shop.days_ahead} day(s) ahead."
        )
    starts = (
        slot_starts(
            window, now=now_local, lead_minutes=shop.lead_minutes, slot_minutes=shop.slot_minutes
        )
        if reason is None
        else []
    )
    taken = _taken(session, shop, day) if starts else {}
    slots = tuple(
        SlotView(
            at=to_utc(s), local=s.strftime("%H:%M"), available=_has_room(shop, taken, to_utc(s))
        )
        for s in starts
    )
    asap = AsapView(False, None, None)
    if day == now_local.date() and reason is None:
        first = next((s for s in slots if s.available), None)
        if first is not None:
            asap = AsapView(True, first.at, first.local)
    return SlotsView(
        day=day,
        open=reason is None and bool(slots),
        reason=reason if reason is not None else (None if slots else "No more slots today."),
        asap=asap,
        slots=slots,
        days=tuple(days),
    )


def resolve_requested(
    session: Session,
    shop: ShopSettings,
    *,
    asap: bool,
    requested_at: datetime | None,
    now: datetime | None = None,
) -> datetime:
    """The UTC slot start an order gets, or a refusal. ASAP takes the first slot with
    room today; a chosen time must be an offered slot with room."""
    now = now or datetime.now(UTC)
    now_local = to_local(now)
    if not shop.enabled:
        raise ShopError(403, "shop_closed", "Online ordering is off at the moment.")
    if asap:
        view = slots_for(session, shop, None, now=now)
        if not view.asap.available or view.asap.at is None:
            raise ShopError(
                403, "shop_closed", view.reason or "We are not taking orders right now."
            )
        return view.asap.at
    if requested_at is None:
        raise ShopError(422, "slot_required", "Pick a collection time.")
    if requested_at.tzinfo is None:
        raise ShopError(422, "bad_slot", "The collection time needs a timezone.")
    wanted_local = to_local(requested_at)
    step = max(1, shop.slot_minutes)
    floored = wanted_local.replace(
        minute=wanted_local.minute - wanted_local.minute % step, second=0, microsecond=0
    )
    view = slots_for(session, shop, floored.date(), now=now)
    if view.reason is not None and not view.slots:
        raise ShopError(409, "slot_unavailable", view.reason)
    slot = next((s for s in view.slots if to_local(s.at) == floored), None)
    if slot is None:
        earliest = earliest_asap(now_local, lead_minutes=shop.lead_minutes, slot_minutes=step)
        if floored < earliest:
            raise ShopError(
                409,
                "slot_unavailable",
                f"That time is too soon: the earliest collection is {earliest:%H:%M}.",
            )
        raise ShopError(409, "slot_unavailable", "That collection time is not offered.")
    if not slot.available:
        raise ShopError(409, "slot_full", f"The {slot.local} slot is full. Pick another time.")
    return slot.at
