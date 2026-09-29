"""`/export`, `/import` and a sent CSV: transactions as a file, both ways.

DECISIONS 28. `/export` picks a window and sends `transactions_<from>_<to>.csv`, the
same shape `services/transactions_csv` reads back. A document message of any kind is
taken as an import attempt: the file is stashed on disk, classified by its header
row, and shown as a **dry run that writes nothing** with «Записать» / «Отмена». Only
the button writes.

The file waits in a temp directory of its own between the two steps (see
`money_views.stash_upload` for why its own). It is removed on either button, and on a
new upload replacing it. A bot restart in between loses the file, not data: the person
sends it again and sees the same dry run.
"""

from __future__ import annotations

import io
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from cafeops.bot import formatters as fmt
from cafeops.bot import money_views as mv
from cafeops.bot.callbacks import ExportCB, ImportCB
from cafeops.bot.deps import RunSync, owner_name
from cafeops.bot.keyboards import export_kb, import_confirm_kb, import_platform_kb
from cafeops.bot.states import ImportFlow
from cafeops.config import settings
from cafeops.services.transactions_csv import CsvKind

router = Router(name="files")

#: Telegram lets a bot download up to 20 MB; a season of receipts is a few hundred KB.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

#: Before the café opened there is nothing to export; "all" starts here rather than
#: at the epoch so the filename says something true.
OPENING_DAY = date(2025, 11, 1)


def _who(event: Message | CallbackQuery) -> str:
    user = event.from_user
    return owner_name(user.id if user else None, user.username if user else None)


# --- export --------------------------------------------------------------


@router.message(Command("export"))
async def export(message: Message) -> None:
    await message.answer(fmt.export_prompt(), reply_markup=export_kb())


@router.callback_query(ExportCB.filter())
async def export_period(query: CallbackQuery, callback_data: ExportCB, run_sync: RunSync) -> None:
    await query.answer()
    if query.message is None:
        return
    today = datetime.now(UTC).astimezone(settings.tz).date()
    since = {
        "today": today,
        "week": today - timedelta(days=6),
        "month": today - timedelta(days=29),
        "all": OPENING_DAY,
    }.get(callback_data.action, today)
    view = await run_sync(mv.build_export, since=since, until=today)
    if view.lines == 0:
        await query.message.answer(fmt.export_caption(view))
        return
    await query.message.answer_document(
        BufferedInputFile(view.data, filename=view.filename), caption=fmt.export_caption(view)
    )


# --- import --------------------------------------------------------------


@router.message(Command("import"))
async def import_help(message: Message) -> None:
    await message.answer(fmt.import_usage())


async def _discard_pending(state: FSMContext) -> None:
    data = await state.get_data()
    path = data.get("upload_path")
    if path:
        mv.discard_upload(Path(path))


async def _preview(message: Message, state: FSMContext, run_sync: RunSync, *, who: str) -> None:
    data = await state.get_data()
    path = Path(data["upload_path"])
    view = await run_sync(
        mv.preview_import, path=path, platform=data.get("platform"), recorded_by=who
    )
    if view.refused and view.kind is CsvKind.CHANNEL_REPORT and view.platform is None:
        await state.set_state(ImportFlow.awaiting_confirm)
        await message.answer(
            fmt.import_platform_prompt(path.name), reply_markup=import_platform_kb()
        )
        return
    if view.refused:
        await _discard_pending(state)
        await state.clear()
        await message.answer(fmt.import_refused(view))
        return
    await state.set_state(ImportFlow.awaiting_confirm)
    await message.answer(fmt.import_preview(view), reply_markup=import_confirm_kb())


@router.message(F.document)
async def document(message: Message, state: FSMContext, run_sync: RunSync) -> None:
    doc = message.document
    if doc is None:
        return
    name = doc.file_name or "upload.csv"
    if not name.lower().endswith(".csv") or (doc.file_size or 0) > MAX_UPLOAD_BYTES:
        await message.answer(fmt.err_not_csv())
        return
    buffer = io.BytesIO()
    await message.bot.download(doc, destination=buffer)  # type: ignore[union-attr]
    await _discard_pending(state)
    await state.clear()
    path = mv.stash_upload(name, buffer.getvalue())
    await state.set_data({"upload_path": str(path), "platform": None})
    await _preview(message, state, run_sync, who=_who(message))


@router.callback_query(ImportCB.filter(F.action == "platform"), ImportFlow.awaiting_confirm)
async def platform_chosen(
    query: CallbackQuery, callback_data: ImportCB, state: FSMContext, run_sync: RunSync
) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    await state.update_data(platform=callback_data.value or None)
    await _preview(query.message, state, run_sync, who=_who(query))


@router.callback_query(ImportCB.filter(F.action == "write"), ImportFlow.awaiting_confirm)
async def write(query: CallbackQuery, state: FSMContext, run_sync: RunSync) -> None:
    await query.answer()
    if query.message is None:
        return
    data = await state.get_data()
    path = Path(data["upload_path"])
    view = await run_sync(
        mv.commit_import, path=path, platform=data.get("platform"), recorded_by=_who(query)
    )
    mv.discard_upload(path)
    await state.clear()
    await query.message.answer(fmt.import_written(view))


@router.callback_query(ImportCB.filter(F.action == "cancel"))
async def cancel(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    await _discard_pending(state)
    await state.clear()
    if query.message is not None:
        await query.message.answer(fmt.import_cancelled())


@router.callback_query(ImportCB.filter(F.action == "write"))
async def write_without_pending(query: CallbackQuery) -> None:
    """«Записать» pressed on an old message after a restart: say so, write nothing."""
    await query.answer()
    if query.message is not None:
        await query.message.answer(fmt.import_nothing_pending())
