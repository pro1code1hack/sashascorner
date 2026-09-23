"""Physical counts: the weekly full walk and the twice-weekly tier A express walk.

Two commands, one flow. `/count` walks every tracked ingredient; `/count_a` walks tier A
only -- twelve items, twice a week, because tier A is what the ordering path leans on and
the drift gate only grants auto-ordering on two consecutive clean counts (invariant 2).

**Each count is written the moment the number arrives.** An abandoned session therefore
leaves the counts already taken rather than nothing: a person interrupted at item four of
forty has still re-anchored four ingredients, and `record_count` has already measured
their drift and run the gate. Batching them until the end would make the common case --
a count interrupted by a customer -- lose the work.

The prompt shows the theoretical figure and says what is behind it (invariant 6). That is
not an invitation to copy it: the reason it is there is so a person who sees 29 L on the
screen and 12 L on the shelf knows there is something to report, and the wording says
plainly which of the two is the source of truth.
"""

from __future__ import annotations

from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from cafeops.bot import formatters as fmt
from cafeops.bot.callbacks import CountCB
from cafeops.bot.deps import owner_name, parse_qty
from cafeops.bot.keyboards import count_kb
from cafeops.bot.states import CountFlow
from cafeops.bot.viewmodels import CountItemView, CountSessionKind
from cafeops.bot.views import build_count_session, submit_count
from cafeops.domain.types import Unit

router = Router(name="count")


async def _start(
    message: Message, state: FSMContext, run_sync: Any, kind: CountSessionKind
) -> None:
    items: list[CountItemView] = await run_sync(build_count_session, kind=kind)
    if not items:
        await state.clear()
        await message.answer(fmt.count_nothing_due(kind))
        return
    await state.set_state(CountFlow.awaiting_qty)
    await state.update_data(
        kind=kind.value,
        queue=[item.ingredient_id for item in items],
        index=0,
        counted=0,
        skipped=0,
    )
    await message.answer(fmt.count_intro(kind, items))
    await _ask(message, state, run_sync)


async def _ask(message: Message, state: FSMContext, run_sync: Any) -> None:
    """Re-read the item from the database each time rather than caching the list.

    A count written thirty seconds ago changes the next item's basis if the two share a
    delivery, and a cached view would show the figure as it was when the walk started.
    """
    data = await state.get_data()
    queue: list[int] = data["queue"]
    index: int = data["index"]
    if index >= len(queue):
        await state.clear()
        await message.answer(
            fmt.count_done(counted=data["counted"], skipped=data["skipped"], total=len(queue))
        )
        return
    kind = CountSessionKind(data["kind"])
    items: list[CountItemView] = await run_sync(build_count_session, kind=kind)
    by_id = {item.ingredient_id: item for item in items}
    item = by_id.get(queue[index])
    if item is None:  # pragma: no cover - the roster does not change mid-walk
        await state.update_data(index=index + 1)
        await _ask(message, state, run_sync)
        return
    await message.answer(
        fmt.count_prompt(item, index=index + 1, total=len(queue)),
        reply_markup=count_kb(item.ingredient_id),
    )


@router.message(Command("count"))
async def count_full(message: Message, state: FSMContext, run_sync: Any) -> None:
    await _start(message, state, run_sync, CountSessionKind.FULL)


@router.message(Command("count_a"))
async def count_express(message: Message, state: FSMContext, run_sync: Any) -> None:
    await _start(message, state, run_sync, CountSessionKind.EXPRESS_A)


@router.message(CountFlow.awaiting_qty)
async def counted(message: Message, state: FSMContext, run_sync: Any) -> None:
    data = await state.get_data()
    queue: list[int] = data["queue"]
    index: int = data["index"]
    kind = CountSessionKind(data["kind"])
    items: list[CountItemView] = await run_sync(build_count_session, kind=kind)
    by_id = {item.ingredient_id: item for item in items}
    item = by_id.get(queue[index])
    if item is None:  # pragma: no cover
        await state.update_data(index=index + 1)
        await _ask(message, state, run_sync)
        return

    qty = parse_qty(message.text or "")
    if qty is None:
        await message.answer(fmt.err_bad_number(item.unit))
        return
    if item.unit is Unit.EACH and qty != qty.to_integral_value():
        # Refused rather than rounded. See `formatters.err_fractional_count`: rounding
        # would write a number to an append-only ledger that nobody saw on the shelf.
        await message.answer(fmt.err_fractional_count(item.name))
        return

    who = owner_name(
        message.from_user.id if message.from_user else None,
        message.from_user.username if message.from_user else None,
    )
    result = await run_sync(
        submit_count, ingredient_id=item.ingredient_id, counted_qty=qty, counted_by=who
    )
    await message.answer(fmt.count_result(result))
    await state.update_data(index=index + 1, counted=data["counted"] + 1)
    await _ask(message, state, run_sync)


@router.callback_query(CountCB.filter(F.action == "skip"), CountFlow.awaiting_qty)
async def skip(query: CallbackQuery, state: FSMContext, run_sync: Any) -> None:
    data = await state.get_data()
    await state.update_data(index=data["index"] + 1, skipped=data["skipped"] + 1)
    await query.answer()
    if query.message is not None:
        await _ask(query.message, state, run_sync)


@router.callback_query(CountCB.filter(F.action == "stop"), CountFlow.awaiting_qty)
async def stop(query: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    queue: list[int] = data["queue"]
    remaining = len(queue) - data["index"]
    await state.clear()
    await query.answer()
    if query.message is not None:
        await query.message.answer(
            fmt.count_done(
                counted=data["counted"],
                skipped=data["skipped"] + remaining,
                total=len(queue),
            )
        )
