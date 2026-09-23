"""Inline keyboards. Every label is imported from `formatters` -- none is written here.

A button caption is user-facing text, so it belongs in the one Russian module like any
other sentence (agent brief). The only strings this file composes are ingredient names,
which come from the database, and the pack counts, which are numbers.
"""

from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from cafeops.bot import formatters as f
from cafeops.bot.callbacks import ChecklistCB, CountCB, DeliveryCB, OrderCB
from cafeops.bot.viewmodels import ChecklistItemView, OrderView

__all__ = ["checklist_kb", "count_kb", "delivery_expiry_kb", "delivery_qty_kb", "order_kb"]


def order_kb(view: OrderView) -> InlineKeyboardMarkup:
    """One +/- row per line, then the decision row.

    The line's own name is on the row, because a bare `+`/`−` pair under a multi-line
    order is a guessing game -- and the guess costs money.
    """
    builder = InlineKeyboardBuilder()
    for line in view.lines:
        builder.row(
            InlineKeyboardButton(
                text=f"{line.ingredient_name}: {line.packs}",
                callback_data=OrderCB(
                    action="reset", po_id=view.po_id, line_id=line.po_line_id
                ).pack(),
            )
        )
        builder.row(
            InlineKeyboardButton(
                text=f.BTN_ORDER_MINUS,
                callback_data=OrderCB(
                    action="dec", po_id=view.po_id, line_id=line.po_line_id
                ).pack(),
            ),
            InlineKeyboardButton(
                text=f.BTN_ORDER_PLUS,
                callback_data=OrderCB(
                    action="inc", po_id=view.po_id, line_id=line.po_line_id
                ).pack(),
            ),
        )
    builder.row(
        InlineKeyboardButton(
            text=f.BTN_ORDER_CONFIRM,
            callback_data=OrderCB(action="confirm", po_id=view.po_id).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=f.BTN_ORDER_CANCEL,
            callback_data=OrderCB(action="cancel", po_id=view.po_id).pack(),
        )
    )
    return builder.as_markup()


def count_kb(ingredient_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f.BTN_COUNT_SKIP,
            callback_data=CountCB(action="skip", ingredient_id=ingredient_id).pack(),
        ),
        InlineKeyboardButton(
            text=f.BTN_COUNT_STOP,
            callback_data=CountCB(action="stop", ingredient_id=ingredient_id).pack(),
        ),
    )
    return builder.as_markup()


def checklist_kb(item: ChecklistItemView) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f.BTN_CHECKLIST_OK,
            callback_data=ChecklistCB(action="ok", ingredient_id=item.ingredient_id).pack(),
        ),
        InlineKeyboardButton(
            text=f.BTN_CHECKLIST_LOW,
            callback_data=ChecklistCB(action="low", ingredient_id=item.ingredient_id).pack(),
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=f.BTN_CHECKLIST_SKIP,
            callback_data=ChecklistCB(action="skip", ingredient_id=item.ingredient_id).pack(),
        )
    )
    return builder.as_markup()


def delivery_qty_kb(po_line_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f.BTN_DELIVERY_SKIP,
            callback_data=DeliveryCB(action="skip", po_line_id=po_line_id).pack(),
        )
    )
    return builder.as_markup()


def delivery_expiry_kb(po_line_id: int) -> InlineKeyboardMarkup:
    """The «no date on the pack» escape.

    Present but deliberately the second choice: an assumed expiry is a guess about the
    one number that decides a write-off (`ARCHITECTURE.md` 8F.1), so the typed date is
    the default path and this is the exception.
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f.BTN_DELIVERY_NO_DATE,
            callback_data=DeliveryCB(action="no_date", po_line_id=po_line_id).pack(),
        )
    )
    builder.row(
        InlineKeyboardButton(
            text=f.BTN_DELIVERY_SKIP,
            callback_data=DeliveryCB(action="skip", po_line_id=po_line_id).pack(),
        )
    )
    return builder.as_markup()


def button_labels(markup: InlineKeyboardMarkup | None) -> list[list[str]]:
    """Every caption, row by row. Used by the local preview to show what is on screen."""
    if markup is None:
        return []
    return [[button.text for button in row] for row in markup.inline_keyboard]


def flatten(rows: Sequence[Sequence[str]]) -> str:
    return "\n".join("  [" + "] [".join(row) + "]" for row in rows)
