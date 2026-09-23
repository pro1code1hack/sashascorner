"""Tier C: yes or no, and then -- only after a «no» -- a number the PERSON chooses.

Spec 4.7. Tier C is fifty-nine items: cake, sundries, things with no par level and no
forecast. The walk itself is still two buttons, because asking for a quantity on every
item is how a fifty-nine-item checklist stops being answered.

What changed is what a `LOW` answer does. It used to do nothing at all: the digest listed
the items and the trail ended there, so the weekly walk produced a list nobody could act
on without retyping it elsewhere. A checklist nobody acts on is a checklist people stop
filling in, and then the one signal tier C has is gone too.

So «заканчивается» is now followed by one question -- how many packs -- and the answer
goes onto the supplier's DRAFT order marked as a human's number
(`services/record_checklist.request_checklist_order`). Three things that question is
careful about:

* **No suggested figure.** Not "last time you ordered 3", not a par floor. Tier C has
  neither, and either would be a guess wearing a calculation's clothes.
* **Skippable.** `BTN_CHECKLIST_NO_ORDER` records the answer and orders nothing, so
  marking an item low stays cheap.
* **Still a DRAFT.** Invariant 1 is untouched -- she confirms it in /orders, with the line
  labelled as coming from the checklist.

Answers are written one at a time for the same reason counts are: a checklist abandoned
halfway has still recorded what was answered, and requested what was requested.
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
from cafeops.bot.keyboards import checklist_kb, checklist_order_kb
from cafeops.bot.states import ChecklistFlow
from cafeops.bot.viewmodels import ChecklistItemView
from cafeops.bot.views import build_checklist, request_checklist_line, submit_checklist
from cafeops.services.record_checklist import ChecklistRequestRefused

router = Router(name="checklist")


def _who(query: CallbackQuery | Message) -> str:
    return owner_name(
        query.from_user.id if query.from_user else None,
        query.from_user.username if query.from_user else None,
    )


async def _ask(message: Message, state: FSMContext, run_sync: Any) -> None:
    data = await state.get_data()
    queue: list[int] = data["queue"]
    index: int = data["index"]
    if index >= len(queue):
        await state.clear()
        await message.answer(
            fmt.checklist_done(
                answered=data["answered"],
                low=data["low"],
                total=len(queue),
                ordered=data.get("ordered", 0),
            )
        )
        return
    await state.set_state(ChecklistFlow.awaiting_answer)
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
        queue=[item.ingredient_id for item in items], index=0, answered=0, low=0, ordered=0
    )
    await message.answer(fmt.checklist_intro(items))
    await _ask(message, state, run_sync)


@router.callback_query(
    ChecklistCB.filter(F.action.in_({"ok", "low"})), ChecklistFlow.awaiting_answer
)
async def answer(
    query: CallbackQuery, callback_data: ChecklistCB, state: FSMContext, run_sync: Any
) -> None:
    """The answer is written first, then -- only for a LOW -- the quantity is asked.

    Order matters. `record_checklist_answer` runs before the question, so abandoning the
    conversation at the quantity prompt still leaves the shelf observation recorded. The
    observation is the evidence; the order is a separate decision.
    """
    is_low = callback_data.action == "low"
    item = await run_sync(
        submit_checklist,
        ingredient_id=callback_data.ingredient_id,
        is_low=is_low,
        responded_by=_who(query),
    )
    data = await state.get_data()
    await state.update_data(
        answered=data["answered"] + 1,
        low=data["low"] + (1 if is_low else 0),
    )
    await query.answer()
    if query.message is None:  # pragma: no cover - Telegram always sends one
        return
    await query.message.answer(fmt.checklist_result(item))
    if not is_low:
        await state.update_data(index=data["index"] + 1)
        await _ask(query.message, state, run_sync)
        return
    await state.set_state(ChecklistFlow.awaiting_order_packs)
    await state.update_data(pending_ingredient_id=callback_data.ingredient_id)
    await query.message.answer(
        fmt.checklist_order_prompt(item),
        reply_markup=checklist_order_kb(callback_data.ingredient_id),
    )


@router.message(ChecklistFlow.awaiting_order_packs)
async def order_packs(message: Message, state: FSMContext, run_sync: Any) -> None:
    """A whole number of packs, or a refusal. Never a rounded one.

    `9.5 packs` is a typo and the right answer to a typo is to ask again -- rounding it
    would put a number in a draft order that nobody typed, which is the same failure
    `err_fractional_count` exists to prevent one flow over.
    """
    data = await state.get_data()
    ingredient_id: int = data["pending_ingredient_id"]
    raw = (message.text or "").strip().replace(",", ".")
    try:
        packs = int(raw)
    except ValueError:
        await message.answer(fmt.err_bad_packs())
        return
    if packs <= 0:
        await message.answer(fmt.err_bad_packs())
        return

    try:
        request = await run_sync(
            request_checklist_line,
            ingredient_id=ingredient_id,
            packs=packs,
            requested_by=_who(message),
        )
    except ChecklistRequestRefused:
        # The English sentence on the exception is for the log. The owner gets the Russian
        # one, written from the fact rather than translated from the message.
        item = await _item(run_sync, ingredient_id)
        await message.answer(fmt.checklist_order_refused(item.name if item else str(ingredient_id)))
    else:
        await state.update_data(ordered=data.get("ordered", 0) + 1)
        await message.answer(fmt.checklist_ordered(request))

    await state.update_data(index=data["index"] + 1)
    await _ask(message, state, run_sync)


@router.callback_query(
    ChecklistCB.filter(F.action == "no_order"), ChecklistFlow.awaiting_order_packs
)
async def no_order(query: CallbackQuery, state: FSMContext, run_sync: Any) -> None:
    """Marked low, nothing ordered. The answer is already written and stays written."""
    data = await state.get_data()
    await state.update_data(index=data["index"] + 1)
    await query.answer()
    if query.message is not None:
        await _ask(query.message, state, run_sync)


async def _item(run_sync: Any, ingredient_id: int) -> ChecklistItemView | None:
    items: list[ChecklistItemView] = await run_sync(build_checklist, only_stale=False)
    return next((i for i in items if i.ingredient_id == ingredient_id), None)


@router.callback_query(ChecklistCB.filter(F.action == "skip"), ChecklistFlow.awaiting_answer)
async def skip(query: CallbackQuery, state: FSMContext, run_sync: Any) -> None:
    data = await state.get_data()
    await state.update_data(index=data["index"] + 1)
    await query.answer()
    if query.message is not None:
        await _ask(query.message, state, run_sync)
