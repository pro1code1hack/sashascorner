"""The bridge between async handlers and sync database work.

Spec 3: async only at the I/O edges, and SQLite has one writer. Repositories are sync and
stay sync; a handler reaches them through `run_sync`, which opens one `session_scope` per
call on a worker thread. One call, one transaction, committed or rolled back before the
handler sees anything -- so a handler cannot hold a write transaction open across an
`await` while the person types.

`parse_qty` and `parse_expiry` live here rather than in a handler because both flows need
them and both have a wrong answer that matters: a comma decimal separator is what a
Russian-speaking person types, and a float reaching a quantity is a bug the `Qty` column
raises on (invariant 11).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from cafeops.config import settings
from cafeops.db.base import SessionFactory, session_scope

__all__ = [
    "OWNER_FALLBACK_NAME",
    "is_owner",
    "owner_name",
    "parse_expiry",
    "parse_qty",
    "run_sync",
    "run_sync_factory",
]


#: `confirmed_by`, `counted_by` and `received_by` must never be blank -- invariant 1 and
#: the `CHECK` constraint behind it. A Telegram account with no name still has an id, and
#: recording `telegram:123456` is a real answer to "who confirmed this"; recording nothing
#: is not, and the constraint would refuse the write anyway.
OWNER_FALLBACK_NAME = "telegram"


def run_sync_factory(
    factory: sessionmaker[Session] | None,
) -> Callable[..., Any]:
    """Build a `run_sync` bound to a specific session factory. For the local preview."""

    async def _run[T](fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
        def work() -> T:
            with session_scope(factory) as session:
                return fn(session, *args, **kwargs)

        return await asyncio.to_thread(work)

    return _run


async def run_sync[T](fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run one sync unit of work on a thread, inside one transaction.

    `fn` takes a `Session` as its first argument. Nothing async is allowed inside it --
    that is the point: everything below this line is the ordinary sync services layer.
    """

    def work() -> T:
        with session_scope(SessionFactory) as session:
            return fn(session, *args, **kwargs)

    return await asyncio.to_thread(work)


def is_owner(user_id: int | None) -> bool:
    """One shared operator, by chat id (spec 1: no user management in v1).

    With no id configured the bot answers nobody rather than everybody. An inventory bot
    that takes counts and confirms spend from any passer-by is worse than one that is
    silent until configured.
    """
    if settings.telegram_owner_chat_id is None:
        return False
    return user_id == settings.telegram_owner_chat_id


def owner_name(user_id: int | None, username: str | None = None) -> str:
    """The name written to `confirmed_by` / `counted_by` / `received_by`."""
    if username:
        return f"telegram:{username}"
    if user_id is not None:
        return f"telegram:{user_id}"
    return OWNER_FALLBACK_NAME


def parse_qty(raw: str) -> Decimal | None:
    """A typed quantity as an exact `Decimal`, or None.

    Comma to point, because that is what gets typed. `Decimal(str)` rather than
    `float(...)` -- a float assigned to a `Qty` column raises `TypeError` on purpose
    (invariant 11), and rounding a count before it reaches the ledger would be a silent
    version of the same bug.
    """
    text = raw.strip().replace(",", ".").replace(" ", "")
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if value < 0 or not value.is_finite():
        return None
    return value


def parse_expiry(raw: str, *, today: date | None = None) -> datetime | None:
    """`DD.MM` or `DD.MM.YYYY` as an aware UTC instant at local end of day.

    End of day, not midnight: a carton stamped the 30th is good *through* the 30th, and
    treating it as expiring at 00:00 would write the stock off a day early and invent
    waste that did not happen.

    A bare `DD.MM` resolves to the next occurrence, so `05.01` typed in December means
    January -- a delivery's expiry is always in the future, and reading it as eleven
    months past would expire the stock the moment it arrived.
    """
    text = raw.strip().replace("/", ".").replace("-", ".")
    parts = [p for p in text.split(".") if p]
    today = today or datetime.now(UTC).astimezone(settings.tz).date()
    try:
        if len(parts) == 2:
            day, month = int(parts[0]), int(parts[1])
            year = today.year
            candidate = date(year, month, day)
            if candidate < today:
                candidate = date(year + 1, month, day)
        elif len(parts) == 3:
            day, month = int(parts[0]), int(parts[1])
            year = int(parts[2])
            if year < 100:
                year += 2000
            candidate = date(year, month, day)
        else:
            return None
    except ValueError:
        return None
    return datetime.combine(candidate, time(23, 59), tzinfo=settings.tz).astimezone(UTC)
