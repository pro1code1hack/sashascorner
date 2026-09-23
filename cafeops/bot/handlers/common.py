"""`/start`, `/help`, and the owner-only gate.

The gate is a router-level filter rather than a check in each handler, because the set of
handlers grows and "the one that forgot the check" is the one that takes a count from a
stranger. Registering it on the router means a new handler is protected by existing.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from cafeops.bot import formatters as f
from cafeops.bot.deps import is_owner

router = Router(name="common")


class OwnerFilter:
    """Answers only the configured owner. See `deps.is_owner` for the unset case."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return is_owner(event.from_user.id if event.from_user else None)


def owner_only(*routers: Router) -> None:
    """Apply the gate to every router. Called once, in `app.build_dispatcher`."""
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
