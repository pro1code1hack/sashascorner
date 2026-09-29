"""The buttons under an online-order message (docs/shop/CONTRACT.md §10.F).

`services/shop/notify.py` sends the owner "🛍 Новый онлайн-заказ …" over the Bot API
with an inline keyboard whose callback data is `shop:<order_id>:<action>`. This router
answers a tap: one sync unit of work through `run_sync` -- `notify.telegram_action`,
which is `orders.transition` by `telegram:<first name>` (the same write path the web
admin uses, so the customer gets told and COLLECTED writes the sale) -- then the
message is edited to the new state with only the still-allowed buttons.

A refusal (`ShopError`) is a toast on the phone, nothing else: the usual case is a
stale keyboard, because the order was moved on from the back office while the message
sat in the chat. The message is left as it was; the next tap that succeeds fixes it.

Every button label and status word lives in `services/shop/notify.py`, next to the
message they belong to, so the sent card and the edited card cannot drift apart.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from cafeops.bot.deps import RunSync
from cafeops.services.shop.errors import ShopError
from cafeops.services.shop.notify import TelegramReply, telegram_action

__all__ = ["ShopOrderCB", "router"]

router = Router(name="shop")


class ShopOrderCB(CallbackData, prefix="shop"):
    """`shop:<order_id>:<action>` -- `action` is a key of `notify.TELEGRAM_ACTIONS`."""

    order_id: int
    action: str


def _actor(query: CallbackQuery) -> str:
    """ "telegram:<first name>" -- the owner's name as `orders.transition`'s actor."""
    user = query.from_user
    name = (user.first_name or user.username or str(user.id)).strip() if user else ""
    return f"telegram:{name or 'owner'}"


@router.callback_query(ShopOrderCB.filter())
async def act(query: CallbackQuery, callback_data: ShopOrderCB, run_sync: RunSync) -> None:
    try:
        reply: TelegramReply = await run_sync(
            telegram_action, callback_data.order_id, callback_data.action, actor=_actor(query)
        )
    except ShopError as exc:
        await query.answer(exc.detail[:200])
        return
    await query.answer(reply.toast[:200])
    if not isinstance(query.message, Message):
        return  # too old to edit (Telegram's InaccessibleMessage); the toast said it
    markup = InlineKeyboardMarkup.model_validate(reply.keyboard) if reply.keyboard else None
    await query.message.edit_text(reply.text, reply_markup=markup)
