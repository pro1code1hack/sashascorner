"""Drive the real bot locally, with no Telegram and no token. `cafeops bot-preview`.

There is no bot token and none is coming in this phase, so "it works" cannot mean "a
message arrived". This is what it means instead: the **real** `Dispatcher` with the
**real** routers, filters, FSM storage, callback factories and keyboards, fed real
`Update` objects, with the HTTP session replaced by one that records outgoing calls rather
than making them. Nothing is stubbed except the wire.

That matters more here than usual. With no test suite (`ARCHITECTURE.md` 1) the only proof
that a Russian sentence says the right thing is somebody reading it, and the only proof
that the +/- buttons adjust the line they claim to is watching the card change. Both are
this command.

`RecordingSession` cannot send: `make_request` never touches the network and returns a
constructed reply. So there is no configuration of this module, or mistake in it, that can
message a real café.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.methods.answer_callback_query import AnswerCallbackQuery
from aiogram.methods.edit_message_text import EditMessageText
from aiogram.methods.send_message import SendMessage
from aiogram.types import CallbackQuery, Chat, InlineKeyboardMarkup, Message, Update, User
from sqlalchemy.orm import Session, sessionmaker

from cafeops.config import settings

from cafeops.bot.app import build_dispatcher
from cafeops.bot.deps import run_sync_factory

__all__ = ["Preview", "Sent", "run_flow"]

#: A syntactically valid token that is not a real one. aiogram validates the SHAPE of a
#: token when a `Bot` is constructed, and the recording session means the value is never
#: used for anything. Kept obviously fake so nobody mistakes it for a secret.
_FAKE_TOKEN = "123456789:LOCALPREVIEWNOTAREALTELEGRAMTOKEN00"

_PREVIEW_CHAT_ID = 777000777


@dataclass(frozen=True, slots=True)
class Sent:
    """One outgoing call the bot made, captured instead of sent."""

    kind: str
    text: str
    buttons: tuple[tuple[tuple[str, str], ...], ...] = ()

    @property
    def flat_buttons(self) -> tuple[tuple[str, str], ...]:
        return tuple(button for row in self.buttons for button in row)


def _buttons(markup: Any) -> tuple[tuple[tuple[str, str], ...], ...]:
    if not isinstance(markup, InlineKeyboardMarkup):
        return ()
    return tuple(
        tuple((button.text, button.callback_data or "") for button in row)
        for row in markup.inline_keyboard
    )


class RecordingSession(BaseSession):
    """A `BaseSession` that records instead of requesting. The whole safety story."""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[Sent] = []

    async def close(self) -> None:
        return None

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        # Never used by these flows; present because BaseSession declares it abstract.
        yield b""

    async def make_request(
        self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None
    ) -> Any:
        if isinstance(method, SendMessage):
            self.sent.append(
                Sent(kind="send", text=method.text, buttons=_buttons(method.reply_markup))
            )
            return Message(
                message_id=len(self.sent),
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                text=method.text,
            )
        if isinstance(method, EditMessageText):
            self.sent.append(
                Sent(kind="edit", text=method.text, buttons=_buttons(method.reply_markup))
            )
            return Message(
                message_id=len(self.sent),
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id or _PREVIEW_CHAT_ID), type="private"),
                text=method.text,
            )
        if isinstance(method, AnswerCallbackQuery):
            return True
        return True


@contextmanager
def _owner(chat_id: int) -> Any:
    """Point the owner gate at the preview chat for the duration.

    The gate is exercised rather than bypassed: `common.OwnerFilter` runs for real, and
    `--as-stranger` shows what a different id gets. Restored afterwards so nothing leaks
    into a later command in the same process.
    """
    previous = settings.telegram_owner_chat_id
    settings.telegram_owner_chat_id = chat_id
    try:
        yield
    finally:
        settings.telegram_owner_chat_id = previous


@dataclass
class Preview:
    """A conversation with the real dispatcher."""

    factory: sessionmaker[Session] | None = None
    chat_id: int = _PREVIEW_CHAT_ID
    user_id: int = _PREVIEW_CHAT_ID
    username: str = "sasha"
    _update_id: int = field(default=0, init=False)
    session: RecordingSession = field(default_factory=RecordingSession, init=False)
    bot: Bot = field(init=False)
    dispatcher: Dispatcher = field(init=False)

    def __post_init__(self) -> None:
        self.bot = Bot(token=_FAKE_TOKEN, session=self.session)
        self.dispatcher = build_dispatcher(run=run_sync_factory(self.factory))

    def _next(self) -> int:
        self._update_id += 1
        return self._update_id

    def _user(self) -> User:
        return User(id=self.user_id, is_bot=False, first_name="Sasha", username=self.username)

    def _message(self, text: str) -> Message:
        return Message(
            message_id=self._next(),
            date=datetime.now(UTC),
            chat=Chat(id=self.chat_id, type="private"),
            from_user=self._user(),
            text=text,
        )

    async def say(self, text: str) -> list[Sent]:
        """Send a text message as the owner and return what came back."""
        before = len(self.session.sent)
        with _owner(self.chat_id):
            await self.dispatcher.feed_update(self.bot, Update(update_id=self._next(), message=self._message(text)))
        return self.session.sent[before:]

    async def tap(self, callback_data: str) -> list[Sent]:
        """Press an inline button by its callback payload."""
        before = len(self.session.sent)
        query = CallbackQuery(
            id=f"preview-{self._next()}",
            from_user=self._user(),
            chat_instance="preview",
            data=callback_data,
            message=Message(
                message_id=self._next(),
                date=datetime.now(UTC),
                chat=Chat(id=self.chat_id, type="private"),
                from_user=User(id=1, is_bot=True, first_name="bot"),
                text="(предыдущее сообщение)",
            ),
        )
        with _owner(self.chat_id):
            await self.dispatcher.feed_update(
                self.bot, Update(update_id=self._next(), callback_query=query)
            )
        return self.session.sent[before:]

    def find_button(self, sent: Sequence[Sent], label_contains: str) -> str | None:
        """The callback payload of the first button whose label contains `label_contains`."""
        for item in reversed(sent):
            for label, data in item.flat_buttons:
                if label_contains.lower() in label.lower():
                    return data
        return None

    async def close(self) -> None:
        await self.bot.session.close()


def render(sent: Sequence[Sent], *, show_buttons: bool = True) -> str:
    """The captured conversation, as it would look on the phone."""
    blocks: list[str] = []
    for item in sent:
        head = "--- сообщение ---" if item.kind == "send" else "--- сообщение изменено ---"
        block = [head, item.text]
        if show_buttons and item.buttons:
            block.append("")
            for row in item.buttons:
                block.append("  " + "   ".join(f"[ {label} ]" for label, _ in row))
        blocks.append("\n".join(block))
    return "\n\n".join(blocks)


def run_flow(coro: Any) -> Any:
    return asyncio.run(coro)
