"""Drive the real bot locally, with no Telegram and no token. `cafeops bot-preview`.

Button labels are taken from `formatters.BTN_*`, never written here. A preview that
matched a hard-coded Russian caption would quietly stop pressing the button the day
somebody reworded it, and the flow would "pass" having done nothing.

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
import itertools
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
from aiogram.methods.get_file import GetFile
from aiogram.methods.send_document import SendDocument
from aiogram.methods.send_message import SendMessage
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    Chat,
    Document,
    File,
    InlineKeyboardMarkup,
    Message,
    Update,
    User,
)
from sqlalchemy.orm import Session, sessionmaker

from cafeops.bot import formatters as fmt
from cafeops.bot.app import build_dispatcher
from cafeops.bot.deps import run_sync_factory
from cafeops.config import settings

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
        #: Files "uploaded" by the preview, by file id. `GetFile` answers with a path
        #: ending in the id and `stream_content` serves the bytes -- so `bot.download`
        #: in the files handler runs unchanged, against nothing on the network.
        self.files: dict[str, bytes] = {}
        #: Documents the bot sent, as (filename, bytes), for the flow to inspect.
        self.documents: list[tuple[str, bytes]] = []

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
        file_id = url.rsplit("/", 1)[-1]
        yield self.files.get(file_id, b"")

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
        if isinstance(method, GetFile):
            return File(
                file_id=method.file_id,
                file_unique_id=method.file_id,
                file_path=f"preview/{method.file_id}",
            )
        if isinstance(method, SendDocument):
            name, data = "document", b""
            if isinstance(method.document, BufferedInputFile):
                name, data = method.document.filename or name, method.document.data
            self.documents.append((name, data))
            self.sent.append(
                Sent(kind="document", text=f"[{name}, {len(data)} bytes]\n{method.caption or ''}")
            )
            return Message(
                message_id=len(self.sent),
                date=datetime.now(UTC),
                chat=Chat(id=int(method.chat_id), type="private"),
                caption=method.caption,
            )
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


#: One dispatcher per process, reused. aiogram refuses to attach a `Router` to a second
#: parent ("Router is already attached"), and the handler routers are module-level
#: singletons -- the idiomatic aiogram layout. So `bot-preview all` builds the dispatcher
#: once and each flow gets its own chat id instead, which also keeps each flow's FSM state
#: separate: the storage key is (bot id, chat id, user id).
_DISPATCHER: Dispatcher | None = None
_CHATS = itertools.count()


def _shared_dispatcher(factory: sessionmaker[Session] | None) -> Dispatcher:
    global _DISPATCHER
    if _DISPATCHER is None:
        _DISPATCHER = build_dispatcher(run=run_sync_factory(factory))
    return _DISPATCHER


@dataclass
class Preview:
    """A conversation with the real dispatcher."""

    factory: sessionmaker[Session] | None = None
    chat_id: int = field(default_factory=lambda: _PREVIEW_CHAT_ID + next(_CHATS))
    user_id: int = 0
    username: str = "sasha"
    _update_id: int = field(default=0, init=False)
    session: RecordingSession = field(default_factory=RecordingSession, init=False)
    bot: Bot = field(init=False)
    dispatcher: Dispatcher = field(init=False)

    def __post_init__(self) -> None:
        if not self.user_id:
            self.user_id = self.chat_id
        self.bot = Bot(token=_FAKE_TOKEN, session=self.session)
        self.dispatcher = _shared_dispatcher(self.factory)

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
            await self.dispatcher.feed_update(
                self.bot, Update(update_id=self._next(), message=self._message(text))
            )
        return self.session.sent[before:]

    async def send_document(self, filename: str, data: bytes) -> list[Sent]:
        """Send a file as the owner, the way the phone would, and return what came back."""
        file_id = f"preview-file-{self._next()}"
        self.session.files[file_id] = data
        before = len(self.session.sent)
        message = Message(
            message_id=self._next(),
            date=datetime.now(UTC),
            chat=Chat(id=self.chat_id, type="private"),
            from_user=self._user(),
            document=Document(
                file_id=file_id,
                file_unique_id=file_id,
                file_name=filename,
                file_size=len(data),
                mime_type="text/csv",
            ),
        )
        with _owner(self.chat_id):
            await self.dispatcher.feed_update(
                self.bot, Update(update_id=self._next(), message=message)
            )
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
                text="(the message this button is attached to)",
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
        head = {"send": "--- sent ---", "document": "--- sent a file ---"}.get(
            item.kind, "--- edited in place ---"
        )
        block = [head, item.text]
        if show_buttons and item.buttons:
            block.append("")
            for row in item.buttons:
                block.append("  " + "   ".join(f"[ {label} ]" for label, _ in row))
        blocks.append("\n".join(block))
    return "\n\n".join(blocks)


def run_flow(coro: Any) -> Any:
    return asyncio.run(coro)


# ==========================================================================
# Scripted flows -- what `cafeops bot-preview <flow>` runs
# ==========================================================================


async def flow_digest(preview: Preview) -> list[Sent]:
    return await preview.say("/digest")


async def flow_start(preview: Preview) -> list[Sent]:
    return [*await preview.say("/start"), *await preview.say("/help")]


async def flow_orders(
    preview: Preview,
    *,
    supplier: str | None = None,
    adjust: bool = True,
    confirm: bool = True,
) -> list[Sent]:
    """Show the cards, press +1 on the first line, then Confirm.

    The adjustment and the confirmation are pressed by their real callback payloads, taken
    off the keyboard the handler built -- so the preview exercises the callback factory and
    the router filter, not a shortcut into the view.
    """
    out = await preview.say("/orders")
    cards = (
        [item for item in out if item.buttons]
        if supplier is None
        else [item for item in out if item.buttons and supplier.lower() in item.text.lower()]
    )
    if not cards:
        return out
    card = cards[0]
    shown: list[Sent] = [card]
    if adjust:
        plus = next(
            (data for label, data in card.flat_buttons if label == fmt.BTN_ORDER_PLUS), None
        )
        if plus:
            shown += await preview.tap(plus)
    if confirm:
        latest = shown[-1]
        approve = preview.find_button([latest, card], fmt.BTN_ORDER_CONFIRM)
        if approve:
            shown += await preview.tap(approve)
    return shown


async def flow_count(preview: Preview, *, express: bool = True, answer: str = "12") -> list[Sent]:
    out = await preview.say("/count_a" if express else "/count")
    out += await preview.say(answer)
    stop = preview.find_button(out, fmt.BTN_COUNT_STOP)
    if stop:
        out += await preview.tap(stop)
    return out


async def flow_checklist(
    preview: Preview, *, low: bool = True, packs: str | None = "2"
) -> list[Sent]:
    """Answer the first item, and -- for a `LOW` -- carry on to the quantity.

    `packs=None` presses «Не заказывать» instead, which is the other half of the decision
    and the one that has to stay cheap: marking an item low must not cost a number the
    person does not have. Both paths are driven here because a `LOW` that orders nothing is
    exactly what this flow used to do by default, and the difference has to be visible.
    """
    out = await preview.say("/checklist")
    label = fmt.BTN_CHECKLIST_LOW if low else fmt.BTN_CHECKLIST_OK
    button = preview.find_button(out, label)
    if button:
        out += await preview.tap(button)
    if not low:
        return out
    if packs is None:
        skip = preview.find_button(out, fmt.BTN_CHECKLIST_NO_ORDER)
        if skip:
            out += await preview.tap(skip)
    else:
        out += await preview.say(packs)
    return out


async def flow_delivery(
    preview: Preview, *, packs: str = "2", expiry: str | None = "30.09"
) -> list[Sent]:
    """Receive one line. `expiry=None` presses the "no date on the pack" button instead.

    Both paths matter: the typed date is the one that makes the expiry sweep honest, and
    the button is the one that produces an `EXPIRY ASSUMED` batch -- and the message has to
    say which happened.
    """
    out = await preview.say("/delivery")
    out += await preview.say(packs)
    if expiry is None:
        button = preview.find_button(out, fmt.BTN_DELIVERY_NO_DATE)
        if button:
            out += await preview.tap(button)
    else:
        out += await preview.say(expiry)
    return out


async def flow_stranger(preview: Preview) -> list[Sent]:
    """The owner gate, exercised. A different id gets nothing at all."""
    preview.user_id = preview.chat_id + 1
    try:
        return await preview.say("/digest")
    finally:
        preview.user_id = preview.chat_id


async def flow_sale(
    preview: Preview,
    *,
    search: str = "latte",
    qty: str = "2",
    price: str | None = None,
    void: bool = True,
) -> list[Sent]:
    """Cash sale: channel, search by name, first match, quantity, save, then void.

    Driven by the real payloads off the real keyboards, so a renamed button or a
    changed callback shape shows up here as a flow that stops early.
    """
    out = await preview.say("/sale")
    button = preview.find_button(out, fmt.BTN_SALE_CASH)
    if not button:
        return out
    out += await preview.tap(button)
    found = await preview.say(search)
    out += found
    pick = next(
        (data for label, data in found[-1].flat_buttons if data.startswith("sal:pick")), None
    )
    if not pick:
        return out
    out += await preview.tap(pick)
    out += await preview.say(qty)
    if price is not None:
        other = preview.find_button(out, fmt.BTN_SALE_PRICE)
        if other:
            out += await preview.tap(other)
            out += await preview.say(price)
    save = preview.find_button(out, fmt.BTN_SALE_SAVE)
    if save:
        out += await preview.tap(save)
    if void:
        undo = preview.find_button(out, fmt.BTN_SALE_VOID)
        if undo:
            out += await preview.tap(undo)
    return out


async def flow_cash(preview: Preview, *, amount: str = "85.50") -> list[Sent]:
    out = await preview.say("/cash")
    today = preview.find_button(out, fmt.BTN_CASH_TODAY)
    if today:
        out += await preview.tap(today)
    out += await preview.say(amount)
    return out


async def flow_export(preview: Preview) -> list[Sent]:
    out = await preview.say("/export")
    week = preview.find_button(out, fmt.BTN_EXPORT_WEEK)
    if week:
        out += await preview.tap(week)
    return out


async def flow_import(preview: Preview, *, csv_text: str | None = None) -> list[Sent]:
    """Send a small transactions CSV, read the dry run, press «Записать»."""
    if csv_text is None:
        today = datetime.now(UTC).astimezone(settings.tz).date().isoformat()
        csv_text = (
            "date,time,channel,item,size,qty,unit price,receipt\n"
            f"{today},10:15,deliveroo,Latte,M,2,4.50,A1\n"
            f"{today},10:15,deliveroo,Latte,S,1,,A1\n"
            f"{today},,cash,Not a real item,,1,,\n"
        )
    out = await preview.say("/import")
    out += await preview.send_document("orders.csv", csv_text.encode("utf-8"))
    write = preview.find_button(out, fmt.BTN_IMPORT_WRITE)
    if write:
        out += await preview.tap(write)
    return out


FLOWS = {
    "start": flow_start,
    "digest": flow_digest,
    "orders": flow_orders,
    "count": flow_count,
    "checklist": flow_checklist,
    "delivery": flow_delivery,
    "sale": flow_sale,
    "cash": flow_cash,
    "export": flow_export,
    "import": flow_import,
    "stranger": flow_stranger,
}
