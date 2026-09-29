"""Delivery receipt, and the expiry date that makes everything downstream possible.

This flow is the only place a real expiry date enters the system. Without it there are no
batches with honest dates, so FIFO by expiry has nothing to sort on and the expiry sweep
has nothing to find -- which means the café's only honest waste figure does not exist
(`ARCHITECTURE.md` 8F.1, 8F.2). Everything else the bot does is reporting; this is the one
flow that *creates* the data.

So the date is asked for explicitly, per line, as its own question, and the fallback is
loud. `receive_po_line` will derive a date from the seeded ESTIMATE shelf life when none
is given -- refusing would lose a real delivery, which is worse -- but it stamps the batch
`EXPIRY ASSUMED` and the receipt message says so. An assumed date decides a write-off, and
a guess that looks like a fact is how the waste figure stops meaning anything.

Two packs counts are kept apart on purpose. `awaiting_packs` holds nothing; the pack count
is only stored between that question and the expiry question, because the batch cannot be
created until both are known. Writing the movement first and the date later would create
exactly the dateless batch this flow exists to prevent.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from cafeops.bot import formatters as fmt
from cafeops.bot.callbacks import DeliveryCB
from cafeops.bot.deps import RunSync, owner_name, parse_expiry, parse_qty
from cafeops.bot.handlers.common import accessible
from cafeops.bot.keyboards import delivery_expiry_kb, delivery_qty_kb
from cafeops.bot.states import AdhocFlow, DeliveryFlow
from cafeops.bot.viewmodels import DeliveryLineView, DeliveryOrderView
from cafeops.bot.views import (
    build_delivery_orders,
    lookup_ingredient,
    receive_adhoc_delivery,
    receive_line,
)
from cafeops.domain.types import Unit

router = Router(name="delivery")


async def _lines(run_sync: RunSync) -> dict[int, DeliveryLineView]:
    orders: list[DeliveryOrderView] = await run_sync(build_delivery_orders)
    return {line.po_line_id: line for order in orders for line in order.outstanding_lines}


async def _ask(message: Message, state: FSMContext, run_sync: RunSync) -> None:
    data = await state.get_data()
    queue: list[int] = data["queue"]
    index: int = data["index"]
    if index >= len(queue):
        await state.clear()
        return
    by_id = await _lines(run_sync)
    line = by_id.get(queue[index])
    if line is None:
        # Already received in full by another route. Skipping silently is right: the
        # stock is on the shelf and the ledger knows, which is the outcome asked for.
        await state.update_data(index=index + 1)
        await _ask(message, state, run_sync)
        return
    await state.set_state(DeliveryFlow.awaiting_packs)
    await message.answer(
        fmt.delivery_qty_prompt(line, index=index + 1, total=len(queue)),
        reply_markup=delivery_qty_kb(line.po_line_id),
    )


@router.message(Command("delivery"))
async def delivery(message: Message, state: FSMContext, run_sync: RunSync) -> None:
    orders: list[DeliveryOrderView] = await run_sync(build_delivery_orders)
    pending = [order for order in orders if order.outstanding_lines]
    if not pending:
        await state.clear()
        await message.answer(fmt.delivery_nothing_expected())
        return
    queue = [line.po_line_id for order in pending for line in order.outstanding_lines]
    await state.update_data(queue=queue, index=0, received=0, packs=None)
    await message.answer(fmt.delivery_intro(pending))
    await _ask(message, state, run_sync)


@router.message(DeliveryFlow.awaiting_packs)
async def packs(message: Message, state: FSMContext, run_sync: RunSync) -> None:
    data = await state.get_data()
    by_id = await _lines(run_sync)
    line = by_id.get(data["queue"][data["index"]])
    if line is None:  # pragma: no cover
        await state.update_data(index=data["index"] + 1)
        await _ask(message, state, run_sync)
        return
    value = parse_qty(message.text or "")
    if value is None or value <= 0:
        await message.answer(fmt.err_bad_number(line.pack_unit))
        return
    count = int(value)
    await state.update_data(packs=count)
    await state.set_state(DeliveryFlow.awaiting_expiry)
    await message.answer(
        fmt.delivery_expiry_prompt(line, packs=count),
        reply_markup=delivery_expiry_kb(line.po_line_id),
    )


async def _receive(
    message: Message, state: FSMContext, run_sync: RunSync, *, expires_at: Any
) -> None:
    data = await state.get_data()
    po_line_id: int = data["queue"][data["index"]]
    who = owner_name(
        message.from_user.id if message.from_user else None,
        message.from_user.username if message.from_user else None,
    )
    receipt = await run_sync(
        receive_line,
        po_line_id=po_line_id,
        packs=data["packs"],
        expires_at=expires_at,
        received_by=who,
    )
    await message.answer(fmt.delivery_receipt(receipt))
    await state.update_data(index=data["index"] + 1, received=data["received"] + 1, packs=None)
    await _ask(message, state, run_sync)


@router.message(DeliveryFlow.awaiting_expiry)
async def expiry(message: Message, state: FSMContext, run_sync: RunSync) -> None:
    when = parse_expiry(message.text or "")
    if when is None:
        await message.answer(fmt.err_bad_date())
        return
    await _receive(message, state, run_sync, expires_at=when)


@router.callback_query(DeliveryCB.filter(F.action == "no_date"), DeliveryFlow.awaiting_expiry)
async def no_date(query: CallbackQuery, state: FSMContext, run_sync: RunSync) -> None:
    """No date on the pack. The service assumes one and stamps the batch as assumed."""
    await query.answer()
    if (message := accessible(query.message)) is not None:
        await _receive(message, state, run_sync, expires_at=None)


@router.callback_query(DeliveryCB.filter(F.action == "skip"))
async def skip(query: CallbackQuery, state: FSMContext, run_sync: RunSync) -> None:
    data = await state.get_data()
    if "queue" not in data:
        await query.answer()
        return
    await state.update_data(index=data["index"] + 1, packs=None)
    await query.answer()
    if (message := accessible(query.message)) is not None:
        await _ask(message, state, run_sync)


# --------------------------------------------------------------------------
# The walk to Tesco: stock that arrived with no order behind it (spec 4.4)
# --------------------------------------------------------------------------


@router.message(Command("adhoc"))
async def adhoc(message: Message, state: FSMContext, run_sync: RunSync) -> None:
    """`/adhoc <ingredient>` -- a panic buy, entered so it can still expire.

    Without this the only way to record a shop run is an `ADJUSTMENT`, which no batch
    owns, so the stock could never expire and never be counted as waste -- the exact hole
    batches exist to close.
    """
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(fmt.adhoc_usage())
        return
    found = await run_sync(lookup_ingredient, name=parts[1].strip())
    if found is None:
        await message.answer(fmt.adhoc_not_found(parts[1].strip()))
        return
    await state.set_state(AdhocFlow.awaiting_qty)
    await state.update_data(ingredient_id=found.ingredient_id, unit=found.unit.value)
    await message.answer(fmt.adhoc_qty_prompt(found))


@router.message(AdhocFlow.awaiting_qty)
async def adhoc_qty(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    unit = Unit(data["unit"])
    qty = parse_qty(message.text or "")
    if qty is None or qty <= 0:
        await message.answer(fmt.err_bad_number(unit))
        return
    await state.update_data(qty=str(qty))
    await state.set_state(AdhocFlow.awaiting_expiry)
    await message.answer(fmt.adhoc_expiry_prompt())


@router.message(AdhocFlow.awaiting_expiry)
async def adhoc_expiry(message: Message, state: FSMContext, run_sync: RunSync) -> None:
    """A retail buy has a date on it too, and this is the only chance to record it."""
    when = parse_expiry(message.text or "")
    if when is None:
        await message.answer(fmt.err_bad_date())
        return
    data = await state.get_data()
    who = owner_name(
        message.from_user.id if message.from_user else None,
        message.from_user.username if message.from_user else None,
    )
    receipt = await run_sync(
        receive_adhoc_delivery,
        ingredient_id=data["ingredient_id"],
        qty=Decimal(data["qty"]),
        expires_at=when,
        received_by=who,
    )
    await state.clear()
    await message.answer(fmt.delivery_receipt(receipt))
