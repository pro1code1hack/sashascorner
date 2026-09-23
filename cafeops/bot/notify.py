"""How a scheduled job reaches the owner. One protocol, two implementations.

The scheduler is its own process (spec 3: bot and scheduler as separate systemd units),
so it cannot answer a `Message` -- it has to push. This is that push, behind a protocol so
the choice of transport is a configuration fact rather than an `if` inside every job.

**`ConsoleNotifier` is chosen automatically whenever there is no token or no owner chat
id, which is the state today.** That is deliberate: `TelegramNotifier` refuses to be
constructed without both, so a misconfigured deployment prints to the log instead of
silently dropping the morning digest, and a development box cannot accidentally message a
real café.
"""

from __future__ import annotations

import logging
from typing import Protocol

from cafeops.config import settings

__all__ = ["ConsoleNotifier", "Notifier", "TelegramNotifier", "notifier_for_settings"]

log = logging.getLogger("cafeops.notify")


class Notifier(Protocol):
    async def send(self, text: str) -> None: ...

    @property
    def is_live(self) -> bool:
        """True only when a message actually leaves the machine."""
        ...


class ConsoleNotifier:
    """Writes to the log. The default, and the only thing that runs without a token."""

    def __init__(self, *, sink: list[str] | None = None) -> None:
        #: When given, messages are collected instead of logged. The preview uses this.
        self.sink = sink

    async def send(self, text: str) -> None:
        if self.sink is not None:
            self.sink.append(text)
            return
        log.info("notify (no telegram token configured):\n%s", text)

    @property
    def is_live(self) -> bool:
        return False


class TelegramNotifier:
    """The real thing. Refuses to exist without both a token and a destination."""

    def __init__(self, token: str, chat_id: int) -> None:
        if not token:
            raise ValueError("telegram_bot_token is required to send a Telegram message")
        if not chat_id:
            raise ValueError(
                "telegram_owner_chat_id is required: a notifier with no destination would "
                "drop every message and look like it worked"
            )
        self.token = token
        self.chat_id = chat_id

    async def send(self, text: str) -> None:
        # Imported here so the scheduler can run, and the preview can import this module,
        # on a box where aiogram is present but no token ever will be.
        from aiogram import Bot

        bot = Bot(token=self.token)
        try:
            # Telegram caps a message at 4096 characters and the morning digest can pass
            # it on a bad day. Chunked on line boundaries rather than truncated: the tail
            # of the digest is the count list and the tier A figures, and dropping those
            # silently would make a short message look like a quiet morning.
            for chunk in _chunks(text):
                await bot.send_message(self.chat_id, chunk)
        finally:
            await bot.session.close()

    @property
    def is_live(self) -> bool:
        return True


#: Telegram's hard limit, less room for the continuation marker.
_MAX_CHARS = 3900


def _chunks(text: str, limit: int = _MAX_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines():
        if size + len(line) + 1 > limit and current:
            out.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        out.append("\n".join(current))
    return out


def notifier_for_settings() -> Notifier:
    """`TelegramNotifier` when configured, `ConsoleNotifier` otherwise. Never raises."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_owner_chat_id
    if token and chat_id:
        return TelegramNotifier(token, chat_id)
    return ConsoleNotifier()
