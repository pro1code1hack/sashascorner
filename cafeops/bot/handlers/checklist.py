"""Tier C: yes or no, never a number. Spec 4.7.

Tier C is fifty-nine items -- cake, sundries, things with no par level and no forecast.
Asking for quantities here would produce numbers nothing consumes and a walk nobody
finishes. The two buttons are the whole interface, and «заканчивается» is an item for the
next order's human review rather than an order.

Answers are written one at a time for the same reason counts are: a checklist abandoned
halfway has still recorded what was answered.
"""

from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from cafeops.bot import formatters as fmt
from cafeops.bot.callbacks import ChecklistCB
from cafeops.bot.deps import owner_name
from cafeops.bot.keyboards import checklist_kb
from cafeops.bot.states import ChecklistFlow
from cafeops.bot.viewmodels import ChecklistItemView
from cafeops.bot.views import build_checklist, submit_checklist

router = Router(name="checklist")


async def _ask(message: Message, state: FSMContext, run_sync: Any) -> None:
    data = await state.get_data()
    queue: list[int] = data["queue"]
    index: int = data["index"]
    if index >= len(queue):
        await state.clear()
        await message.answer(
            fmt.checklist_done(answered=data["answered"], low=data["low"], total=len(queue))
        )
        return
    items: list[ChecklistItemView] = await run_sync(build_checklist, only_stale=False)
    by_id = {item.ingredient_id: item for item in items}
    item = by_id.get(queue[index])
    if item is None:  # pragma: no cover
        await state.update_data(index=index + 1)
        await _ask(message, state, run_sync)
        return
    await message.answer(
        fmt.checklist_prompt(item, index=index + 1, total=len(queue)),
        reply_markup=checklist_kb(item),
    )


@router.message(Command("checklist"))
async def checklist(message: Message, state: FSMContext, run_sync: Any) -> None:
    items: list[ChecklistItemView] = await run_sync(build_checklist)
    if not items:
        await state.clear()
        await message.answer(fmt.checklist_intro(items))
        return
    await state.set_state(ChecklistFlow.awaiting_answer)
    await state.update_data(
        queue=[item.ingredient_id for item in items], index=0, answered=0, low=0
    )
    await message.answer(fmt.checklist_intro(items))
    await _ask(message, state, run_sync)


@router.callback_query(
    ChecklistCB.filter(F.action.in_({"ok", "low"})), ChecklistFlow.awaiting_answer
)
async def answer(
    query: CallbackQuery, callback_data: ChecklistCB, state: FSMContext, run_sync: Any
) -> None:
    who = owner_name(
        query.from_user.id if query.from_user else None,
        query.from_user.username if query.from_user else None,
    )
    is_low = callback_data.action == "low"
    item = await run_sync(
        submit_checklist,
        ingredient_id=callback_data.ingredient_id,
        is_low=is_low,
        responded_by=who,
    )
    data = await state.get_data()
    await state.update_data(
        index=data["index"] + 1,
        answered=data["answered"] + 1,
        low=data["low"] + (1 if is_low else 0),
    )
    await query.answer()
    if query.message is not None:
        await query.message.answer(fmt.checklist_result(item))
        await _ask(query.message, state, run_sync)


@router.callback_query(ChecklistCB.filter(F.action == "skip"), ChecklistFlow.awaiting_answer)
async def skip(query: CallbackQuery, state: FSMContext, run_sync: Any) -> None:
    data = await state.get_data()
    await state.update_data(index=data["index"] + 1)
    await query.answer()
    if query.message is not None:
        await _ask(query.message, state, run_sync)
