"""`/start`, `/help`, and the owner-only gate.

The gate is a router-level filter rather than a check in each handler, because the set of
handlers grows and "the one that forgot the check" is the one that takes a count from a
stranger. Registering it on the router means a new handler is protected by existing.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import BaseFilter, Command
from aiogram.types import CallbackQuery, ErrorEvent, Message

from cafeops.bot import formatters as f
from cafeops.bot.deps import is_owner

router = Router(name="common")

log = logging.getLogger("cafeops.bot")


class OwnerFilter(BaseFilter):
    """Answers only the configured owner. See `deps.is_owner` for the unset case.

    `BaseFilter`, not a bare callable class. aiogram wraps a `BaseFilter` so its async
    `__call__` is awaited; a plain object with an async `__call__` gets called and the
    resulting coroutine evaluated for truthiness, which is always True -- so the gate
    would pass everybody while looking exactly like a gate. Python says so out loud
    ("coroutine was never awaited"), which is how this was caught.
    """

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return is_owner(event.from_user.id if event.from_user else None)


#: The routers are module-level singletons, so attaching the gate twice would stack two
#: identical filters on every observer. Harmless in behaviour and wasteful in fact, and
#: `bot-preview all` builds several dispatchers in one process.
_gated = False


def owner_only(*routers: Router) -> None:
    """Apply the gate to every router. Idempotent -- see `_gated`."""
    global _gated
    if _gated:
        return
    _gated = True
    gate = OwnerFilter()
    for router_ in routers:
        router_.message.filter(gate)
        router_.callback_query.filter(gate)


@router.message(Command("start"))
async def start(message: Message) -> None:
    await message.answer(f.start_text())


@router.message(Command("help"))
async def help_(message: Message) -> None:
    await message.answer(f.help_text())


@router.errors()
async def on_error(event: ErrorEvent) -> bool:
    """Say something, and say that nothing was written.

    Without this a service refusal mid-flow -- `ReceiveRefused`, a `LookupError` on a
    stale keyboard, the `CHECK` constraint doing its job -- leaves the person looking at
    silence and unable to tell whether their count went in. The reply is deliberately
    vague about the cause and precise about the consequence: every write in this bot is
    one transaction per step (`deps.run_sync`), so a failed step wrote nothing, and that
    is the fact somebody standing at a fridge needs.

    The exception itself goes to the log, in English, where it belongs.
    """
    log.exception("handler failed: %s", event.exception)
    message = event.update.message or (
        event.update.callback_query.message if event.update.callback_query else None
    )
    if message is not None and hasattr(message, "answer"):
        await message.answer(f.err_unknown())
    # True: handled. Returning False would re-raise into the poller and, in aiogram's
    # long-polling loop, take the bot down over one bad tap.
    return True
