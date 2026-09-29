"""`/cash`: the day's one cash figure (DECISIONS 26), from the phone.

Pick the day, type the amount, done. The figure is written the moment it arrives --
the same rule as a count -- because "how much cash today" is a fact once it is typed,
and the Sales tab shows it under the same name the moment the transaction commits.

A day whose cash came from an export is refused, exactly as the Sales tab refuses it:
the export wins by `PAYMENT_SOURCE_PRECEDENCE`, so a typed figure would sit shadowed
and silent, which is worse than a refusal that says where to fix it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from cafeops.bot import formatters as fmt
from cafeops.bot import money_views as mv
from cafeops.bot.callbacks import CashCB
from cafeops.bot.deps import RunSync, owner_name, parse_money, parse_past_day
from cafeops.bot.keyboards import cash_day_kb
from cafeops.bot.states import CashFlow
from cafeops.config import settings
from cafeops.services.finance.common import FinanceConflict

router = Router(name="cash")


@router.message(Command("cash"))
async def cash(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(CashFlow.awaiting_date)
    await message.answer(fmt.cash_day_prompt(), reply_markup=cash_day_kb())


async def _ask_amount(message: Message, state: FSMContext, run_sync: RunSync, day: str) -> None:
    from datetime import date

    view = await run_sync(mv.cash_day, day=date.fromisoformat(day))
    if view.locked_by is not None:
        await state.clear()
        await message.answer(fmt.cash_locked(view))
        return
    await state.update_data(day=day)
    await state.set_state(CashFlow.awaiting_amount)
    await message.answer(fmt.cash_amount_prompt(view))


@router.callback_query(CashCB.filter(F.action.in_({"today", "yesterday"})), CashFlow.awaiting_date)
async def quick_day(
    query: CallbackQuery, callback_data: CashCB, state: FSMContext, run_sync: RunSync
) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    today = datetime.now(UTC).astimezone(settings.tz).date()
    day = today if callback_data.action == "today" else today - timedelta(days=1)
    await _ask_amount(query.message, state, run_sync, day.isoformat())


@router.callback_query(CashCB.filter(F.action == "other"), CashFlow.awaiting_date)
async def other_day(query: CallbackQuery) -> None:
    await query.answer()
    if query.message is not None:
        await query.message.answer(fmt.cash_other_day_prompt())


@router.message(CashFlow.awaiting_date, F.text, ~F.text.startswith("/"))
async def day_typed(message: Message, state: FSMContext, run_sync: RunSync) -> None:
    day = parse_past_day(message.text or "")
    if day is None:
        await message.answer(fmt.err_bad_past_day())
        return
    await _ask_amount(message, state, run_sync, day.isoformat())


@router.message(CashFlow.awaiting_amount, F.text, ~F.text.startswith("/"))
async def amount_typed(message: Message, state: FSMContext, run_sync: RunSync) -> None:
    from datetime import date

    pence = parse_money(message.text or "")
    if pence is None:
        await message.answer(fmt.err_bad_money())
        return
    data = await state.get_data()
    who = owner_name(
        message.from_user.id if message.from_user else None,
        message.from_user.username if message.from_user else None,
    )
    try:
        view = await run_sync(
            mv.set_cash, day=date.fromisoformat(data["day"]), pence=pence, operator=who
        )
    except FinanceConflict:
        locked = await run_sync(mv.cash_day, day=date.fromisoformat(data["day"]))
        await state.clear()
        await message.answer(fmt.cash_locked(locked))
        return
    await state.clear()
    await message.answer(fmt.cash_saved(view))


@router.callback_query(CashCB.filter(F.action == "cancel"))
async def cancel(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    await state.clear()
    if query.message is not None:
        await query.message.answer(fmt.cash_cancelled())
