"""Wire the bot up and (only if a token exists) run it.

`build_dispatcher` is the seam the local preview uses: it builds the real `Dispatcher`
with the real routers, the real FSM storage and the real `run_sync`, and it needs no
token. `main` is the only function that constructs a `Bot` against Telegram, and it
refuses when the token is absent rather than failing somewhere inside long-polling.

No token exists today, so nothing in this file has ever sent a message. `cafeops
bot-preview` drives the same dispatcher against a recording session instead, which is how
the Russian output is read and checked.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from cafeops.bot.deps import run_sync
from cafeops.bot.handlers import build_root_router
from cafeops.config import settings
from cafeops.logging_setup import configure_logging

__all__ = ["build_dispatcher", "main", "run"]

log = logging.getLogger("cafeops.bot")


class BotNotConfigured(RuntimeError):
    """No token. Raised instead of starting a poller that will 401 in a loop."""


def build_dispatcher(*, owner_only: bool = True, run: object | None = None) -> Dispatcher:
    """The real dispatcher. `run` overrides the session factory bridge for the preview.

    FSM state is in memory, which is right for a single-operator bot on one box: the only
    thing state holds is the position in a walk, and every consequence is already written
    to the database as it happens (see `states`). A restart mid-count loses the place in
    the list, not a count.
    """
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["run_sync"] = run or run_sync
    dispatcher.include_router(build_root_router(owner_only=owner_only))
    return dispatcher


async def run() -> None:
    token = settings.telegram_bot_token
    if not token:
        raise BotNotConfigured(
            "CAFEOPS_TELEGRAM_BOT_TOKEN is not set. Nothing is sent without it. Use "
            "`cafeops bot-preview` to read the bot's output locally."
        )
    if not settings.telegram_owner_chat_id:
        raise BotNotConfigured(
            "CAFEOPS_TELEGRAM_OWNER_CHAT_ID is not set. The bot would answer nobody, "
            "because every handler is gated to the owner (spec 1: one shared operator)."
        )
    bot = Bot(token=token)
    dispatcher = build_dispatcher()
    log.info("bot starting, owner chat id %s", settings.telegram_owner_chat_id)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


def main() -> None:
    configure_logging()
    asyncio.run(run())
