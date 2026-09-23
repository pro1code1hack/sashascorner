"""Order confirmation. INVARIANT 1 lives here: nothing is ordered without a human.

One message per supplier, because one ordering run produces N orders rather than one
basket (spec 5.5) and the minimum, the cover window and the channel are all per supplier.
A single combined card would have to average those away.

Three things this flow must not do, each of which is a way to break an invariant that the
data already got right:

* **Never say "ordered" when a person still has to act.** Cups Direct is
  `BROWSER_AGENT`: the adapter fills a basket and stops before checkout, and every
  adapter returns `requires_human_completion=True` (`ARCHITECTURE.md` 4). The formatter
  says «корзина готова».
* **Never hide a cap or a top-up.** `views` classifies both onto the line and the
  formatter prints them under the quantity they explain, not in a footnote.
* **Never confirm without a name.** `confirm_order` passes `confirmed_by` to the
  repository, which refuses a blank one, and `ck_po_confirmed_requires_human` refuses the
  row underneath. That refusal is the intended failure and nothing here catches it to
  press on.
"""

from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from cafeops.bot import formatters as fmt
from cafeops.bot.callbacks import OrderCB
from cafeops.bot.deps import owner_name
from cafeops.bot.keyboards import order_kb
from cafeops.bot.views import (
    OrderNotAdjustable,
    adjust_packs,
    build_order_view,
    confirm_order,
    dispatch_order,
    list_draft_orders,
)

router = Router(name="orders")


@router.message(Command("orders"))
async def orders(message: Message, run_sync: Any) -> None:
    drafts = await run_sync(list_draft_orders)
    if not drafts:
        await message.answer(fmt.order_nothing_to_confirm())
        return
    for view in drafts:
        await message.answer(fmt.order_card(view), reply_markup=order_kb(view))


@router.callback_query(OrderCB.filter(F.action.in_({"inc", "dec", "reset"})))
async def adjust(query: CallbackQuery, callback_data: OrderCB, run_sync: Any) -> None:
    """The +/- buttons. `reset` taps the line's own label and returns the suggestion."""
    try:
        if callback_data.action == "reset":
            current = await run_sync(build_order_view, callback_data.po_id)
            line = next(
                (row for row in current.lines if row.po_line_id == callback_data.line_id), None
            )
            delta = 0 if line is None else line.suggested_packs - line.packs
        else:
            delta = 1 if callback_data.action == "inc" else -1
        view = await run_sync(adjust_packs, po_line_id=callback_data.line_id, delta=delta)
    except OrderNotAdjustable:
        await query.answer()
        if query.message is not None:
            await query.message.answer(fmt.err_not_adjustable(callback_data.po_id))
        return
    await query.answer()
    if query.message is not None:
        await query.message.edit_text(fmt.order_card(view), reply_markup=order_kb(view))


@router.callback_query(OrderCB.filter(F.action == "cancel"))
async def cancel(query: CallbackQuery, callback_data: OrderCB, run_sync: Any) -> None:
    """Leaves the order a DRAFT. Declining is not cancelling.

    The order stays in the queue and reappears in tomorrow's digest. A tap that deleted
    the order would lose the forecast that produced it and the reasons attached to it,
    and «не сейчас» is not «никогда».
    """
    view = await run_sync(build_order_view, callback_data.po_id)
    await query.answer()
    if query.message is not None:
        await query.message.edit_text(fmt.order_card(view))


@router.callback_query(OrderCB.filter(F.action == "confirm"))
async def confirm(query: CallbackQuery, callback_data: OrderCB, run_sync: Any) -> None:
    who = owner_name(
        query.from_user.id if query.from_user else None,
        query.from_user.username if query.from_user else None,
    )
    view = await run_sync(confirm_order, po_id=callback_data.po_id, confirmed_by=who)
    await query.answer()
    if query.message is None:  # pragma: no cover - inline messages have no message
        return
    await query.message.edit_text(fmt.order_card(view))
    await query.message.answer(fmt.order_confirmed(view))
    dispatch = await run_sync(dispatch_order, po_id=callback_data.po_id)
    await query.message.answer(fmt.order_dispatched(dispatch))
