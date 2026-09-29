"""`/sale`: a sale the till never saw -- cash at the counter, Deliveroo, Just Eat.

DECISIONS 28. Channel first, then items by category button or by typed name, a
quantity, and a basket that is written on «Записать» and not before (see
`states.SaleFlow` for why this flow, alone, holds a basket in state).

Every button carries an id and the handler re-reads the item from the database: the
name and menu price on the screen are never trusted from the button, and the only two
things the person can *override* are the quantity and, for a delivery app, the unit
price. Voiding is by the receipt's first `sale` row id, for the same 64-byte reason.

The till (`EPOS`) is not on the channel keyboard, and `record_sale` refuses it anyway:
a typed till sale would be counted twice when the Lightspeed sync lands.
"""

from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from cafeops.bot import formatters as fmt
from cafeops.bot import money_views as mv
from cafeops.bot.callbacks import SaleCB
from cafeops.bot.deps import RunSync, owner_name, parse_money, parse_past_day, parse_qty
from cafeops.bot.keyboards import (
    sale_basket_kb,
    sale_categories_kb,
    sale_channel_kb,
    sale_items_kb,
    sale_qty_kb,
    sale_recorded_kb,
    sale_search_kb,
)
from cafeops.bot.states import SaleFlow
from cafeops.bot.viewmodels import BasketView
from cafeops.domain.enums import MANUAL_SALE_CHANNELS
from cafeops.services.record_sale import SaleRefused

router = Router(name="sale")


def _who(event: Message | CallbackQuery) -> str:
    user = event.from_user
    return owner_name(user.id if user else None, user.username if user else None)


async def _basket(state: FSMContext, run_sync: RunSync) -> BasketView:
    data = await state.get_data()
    basket: BasketView = await run_sync(
        mv.basket_view,
        channel=data["channel"],
        sold_on=data.get("sold_on"),
        raw_lines=data.get("lines", []),
    )
    return basket


async def _show_categories(message: Message, state: FSMContext, run_sync: RunSync) -> None:
    await state.set_state(SaleFlow.picking)
    basket = await _basket(state, run_sync)
    page = await run_sync(mv.menu_page, category_index=None)
    await message.answer(
        fmt.sale_pick_prompt(basket),
        reply_markup=sale_categories_kb(page, has_lines=bool(basket.lines)),
    )


async def _show_basket(message: Message, state: FSMContext, run_sync: RunSync) -> None:
    await state.set_state(SaleFlow.picking)
    basket = await _basket(state, run_sync)
    await message.answer(fmt.sale_basket(basket), reply_markup=sale_basket_kb(basket))


# --- start ---------------------------------------------------------------


@router.message(Command("sale"))
async def sale(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(fmt.sale_channel_prompt(), reply_markup=sale_channel_kb())


@router.callback_query(SaleCB.filter(F.action == "chan"))
async def choose_channel(
    query: CallbackQuery, callback_data: SaleCB, state: FSMContext, run_sync: RunSync
) -> None:
    await query.answer()
    if callback_data.value >= len(MANUAL_SALE_CHANNELS) or not isinstance(query.message, Message):
        return
    channel = MANUAL_SALE_CHANNELS[callback_data.value]
    await state.set_state(SaleFlow.picking)
    await state.set_data({"channel": channel.value, "lines": [], "sold_on": None})
    await _show_categories(query.message, state, run_sync)


# --- picking -------------------------------------------------------------


@router.callback_query(SaleCB.filter(F.action == "cats"), SaleFlow.picking)
@router.callback_query(SaleCB.filter(F.action == "cats"), SaleFlow.awaiting_qty)
async def back_to_categories(query: CallbackQuery, state: FSMContext, run_sync: RunSync) -> None:
    await query.answer()
    if isinstance(query.message, Message):
        await _show_categories(query.message, state, run_sync)


@router.callback_query(SaleCB.filter(F.action == "more"), SaleFlow.picking)
async def add_more(query: CallbackQuery, state: FSMContext, run_sync: RunSync) -> None:
    await query.answer()
    if isinstance(query.message, Message):
        await _show_categories(query.message, state, run_sync)


@router.callback_query(SaleCB.filter(F.action == "cat"), SaleFlow.picking)
async def open_category(
    query: CallbackQuery, callback_data: SaleCB, state: FSMContext, run_sync: RunSync
) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    page = await run_sync(mv.menu_page, category_index=callback_data.value, page=callback_data.page)
    if page.category is None:
        await _show_categories(query.message, state, run_sync)
        return
    await query.message.answer(fmt.sale_items_prompt(page), reply_markup=sale_items_kb(page))


@router.message(SaleFlow.picking, F.text, ~F.text.startswith("/"))
async def search(message: Message, run_sync: RunSync) -> None:
    q = (message.text or "").strip()
    found = await run_sync(mv.menu_search, q=q)
    text = fmt.sale_search_results(q, len(found))
    if found:
        await message.answer(text, reply_markup=sale_search_kb(found))
    else:
        await message.answer(text)


@router.callback_query(SaleCB.filter(F.action == "pick"), SaleFlow.picking)
async def pick(
    query: CallbackQuery, callback_data: SaleCB, state: FSMContext, run_sync: RunSync
) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    item = await run_sync(mv.pick_item, menu_item_id=callback_data.value)
    if item is None:
        await _show_categories(query.message, state, run_sync)
        return
    await state.update_data(pending_item=item.menu_item_id)
    await state.set_state(SaleFlow.awaiting_qty)
    await query.message.answer(fmt.sale_qty_prompt(item), reply_markup=sale_qty_kb())


async def _add_line(message: Message, state: FSMContext, run_sync: RunSync, qty: str) -> None:
    data = await state.get_data()
    item_id = data.get("pending_item")
    if item_id is None:
        await _show_categories(message, state, run_sync)
        return
    lines: list[dict[str, Any]] = list(data.get("lines", []))
    lines.append({"item_id": int(item_id), "qty": qty, "price": None})
    await state.update_data(lines=lines, pending_item=None)
    await _show_basket(message, state, run_sync)


@router.callback_query(SaleCB.filter(F.action == "qty"), SaleFlow.awaiting_qty)
async def qty_button(
    query: CallbackQuery, callback_data: SaleCB, state: FSMContext, run_sync: RunSync
) -> None:
    await query.answer()
    if isinstance(query.message, Message) and callback_data.value > 0:
        await _add_line(query.message, state, run_sync, str(callback_data.value))


@router.message(SaleFlow.awaiting_qty, F.text, ~F.text.startswith("/"))
async def qty_typed(message: Message, state: FSMContext, run_sync: RunSync) -> None:
    qty = parse_qty(message.text or "")
    if qty is None or qty <= 0 or qty != qty.to_integral_value():
        await message.answer(fmt.err_bad_count())
        return
    await _add_line(message, state, run_sync, str(int(qty)))


# --- basket edits --------------------------------------------------------


@router.callback_query(SaleCB.filter(F.action == "drop"), SaleFlow.picking)
async def drop_last(query: CallbackQuery, state: FSMContext, run_sync: RunSync) -> None:
    await query.answer()
    data = await state.get_data()
    lines: list[dict[str, Any]] = list(data.get("lines", []))
    if lines:
        lines.pop()
    await state.update_data(lines=lines)
    if isinstance(query.message, Message):
        await _show_basket(query.message, state, run_sync)


@router.callback_query(SaleCB.filter(F.action == "price"), SaleFlow.picking)
async def change_price(query: CallbackQuery, state: FSMContext, run_sync: RunSync) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    basket = await _basket(state, run_sync)
    if not basket.lines:
        await _show_basket(query.message, state, run_sync)
        return
    await state.set_state(SaleFlow.awaiting_price)
    await query.message.answer(fmt.sale_price_prompt(basket.lines[-1]))


@router.message(SaleFlow.awaiting_price, F.text, ~F.text.startswith("/"))
async def price_typed(message: Message, state: FSMContext, run_sync: RunSync) -> None:
    pence = parse_money(message.text or "")
    if pence is None:
        await message.answer(fmt.err_bad_money())
        return
    data = await state.get_data()
    lines: list[dict[str, Any]] = list(data.get("lines", []))
    if lines:
        lines[-1] = {**lines[-1], "price": pence}
    await state.update_data(lines=lines)
    await _show_basket(message, state, run_sync)


@router.callback_query(SaleCB.filter(F.action == "date"), SaleFlow.picking)
async def change_date(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    await state.set_state(SaleFlow.awaiting_date)
    if query.message is not None:
        await query.message.answer(fmt.sale_date_prompt())


@router.message(SaleFlow.awaiting_date, F.text, ~F.text.startswith("/"))
async def date_typed(message: Message, state: FSMContext, run_sync: RunSync) -> None:
    day = parse_past_day(message.text or "")
    if day is None:
        await message.answer(fmt.err_bad_past_day())
        return
    await state.update_data(sold_on=day.isoformat())
    await _show_basket(message, state, run_sync)


# --- decision ------------------------------------------------------------


@router.callback_query(SaleCB.filter(F.action == "save"), SaleFlow.picking)
async def save(query: CallbackQuery, state: FSMContext, run_sync: RunSync) -> None:
    await query.answer()
    if query.message is None:
        return
    data = await state.get_data()
    lines = data.get("lines", [])
    if not lines:
        await query.message.answer(fmt.sale_nothing_to_save())
        return
    try:
        view = await run_sync(
            mv.write_sale,
            channel=data["channel"],
            sold_on=data.get("sold_on"),
            raw_lines=lines,
            recorded_by=_who(query),
        )
    except SaleRefused as exc:
        await query.message.answer(fmt.sale_refused(str(exc)))
        return
    await state.clear()
    await query.message.answer(fmt.sale_recorded(view), reply_markup=sale_recorded_kb(view))


@router.callback_query(SaleCB.filter(F.action == "cancel"))
async def cancel(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    await state.clear()
    if query.message is not None:
        await query.message.answer(fmt.sale_cancelled())


@router.callback_query(SaleCB.filter(F.action == "void"))
async def void(query: CallbackQuery, callback_data: SaleCB, run_sync: RunSync) -> None:
    """Stateless on purpose: the button sits in the chat and may be pressed later."""
    await query.answer()
    if query.message is None:
        return
    try:
        view = await run_sync(
            mv.void_recorded_sale, sale_id=callback_data.value, voided_by=_who(query)
        )
    except SaleRefused as exc:
        await query.message.answer(fmt.sale_refused(str(exc)))
        return
    await query.message.answer(fmt.sale_voided(view))
