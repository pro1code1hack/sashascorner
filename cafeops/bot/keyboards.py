"""Inline keyboards. Every label is imported from `formatters` -- none is written here.

A button caption is user-facing text, so it belongs in the one Russian module like any
other sentence (agent brief). The only strings this file composes are ingredient names,
which come from the database, and the pack counts, which are numbers.
"""

from __future__ import annotations

from collections.abc import Sequence

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from cafeops.bot import formatters as f
from cafeops.bot.callbacks import (
    CashCB,
    ChecklistCB,
    CountCB,
    DeliveryCB,
    ExportCB,
    ImportCB,
    OrderCB,
    SaleCB,
)
from cafeops.bot.viewmodels import (
    BasketView,
    ChecklistItemView,
    MenuPageView,
    MenuPickView,
    OrderView,
    RecordedSaleView,
)
from cafeops.db.models.enums import MANUAL_SALE_CHANNELS, SaleChannel

__all__ = [
    "cash_day_kb",
    "checklist_kb",
    "checklist_order_kb",
    "count_kb",
    "delivery_expiry_kb",
    "delivery_qty_kb",
    "export_kb",
    "import_confirm_kb",
    "import_platform_kb",
    "order_kb",
    "sale_basket_kb",
    "sale_categories_kb",
    "sale_channel_kb",
    "sale_items_kb",
    "sale_qty_kb",
    "sale_recorded_kb",
    "sale_search_kb",
]


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


def checklist_order_kb(ingredient_id: int) -> InlineKeyboardMarkup:
    """The escape from the quantity question.

    Present because «заканчивается» must stay answerable without committing to a number:
    the point of asking is that the system has none, and forcing one would make the
    checklist expensive to fill in -- which is how it stops being filled in at all.
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f.BTN_CHECKLIST_NO_ORDER,
            callback_data=ChecklistCB(action="no_order", ingredient_id=ingredient_id).pack(),
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


# --------------------------------------------------------------------------
# Hand-typed sale, cash, files (DECISIONS 28)
# --------------------------------------------------------------------------


def _b(text: str, cb: CallbackData) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb.pack())


def sale_channel_kb() -> InlineKeyboardMarkup:
    """One button per hand-typeable channel, by index into `MANUAL_SALE_CHANNELS`."""
    labels = {
        SaleChannel.CASH: f.BTN_SALE_CASH,
        SaleChannel.DELIVEROO: f.BTN_SALE_DELIVEROO,
        SaleChannel.JUST_EAT: f.BTN_SALE_JUST_EAT,
        SaleChannel.OTHER: f.BTN_SALE_OTHER,
    }
    builder = InlineKeyboardBuilder()
    for index, channel in enumerate(MANUAL_SALE_CHANNELS):
        builder.row(_b(labels[channel], SaleCB(action="chan", value=index)))
    builder.row(_b(f.BTN_SALE_CANCEL, SaleCB(action="cancel")))
    return builder.as_markup()


def sale_categories_kb(page: MenuPageView, *, has_lines: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, name in enumerate(page.categories):
        builder.button(text=name, callback_data=SaleCB(action="cat", value=index).pack())
    builder.adjust(2)
    if has_lines:
        builder.row(_b(f.BTN_SALE_SAVE, SaleCB(action="save")))
    builder.row(_b(f.BTN_SALE_CANCEL, SaleCB(action="cancel")))
    return builder.as_markup()


def _item_label(item: MenuPickView) -> str:
    return f"{item.label} · £{item.price_pence // 100}.{item.price_pence % 100:02d}"


def sale_items_kb(page: MenuPageView) -> InlineKeyboardMarkup:
    """The items of one category page, then paging, then back to the categories."""
    builder = InlineKeyboardBuilder()
    for item in page.items:
        builder.row(_b(_item_label(item), SaleCB(action="pick", value=item.menu_item_id)))
    paging: list[InlineKeyboardButton] = []
    index = page.category_index or 0
    if page.page > 0:
        paging.append(_b(f.BTN_SALE_PREV, SaleCB(action="cat", value=index, page=page.page - 1)))
    if page.page + 1 < page.pages:
        paging.append(_b(f.BTN_SALE_NEXT, SaleCB(action="cat", value=index, page=page.page + 1)))
    if paging:
        builder.row(*paging)
    builder.row(_b(f.BTN_SALE_BACK, SaleCB(action="cats")))
    return builder.as_markup()


def sale_search_kb(items: Sequence[MenuPickView]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in items:
        builder.row(_b(_item_label(item), SaleCB(action="pick", value=item.menu_item_id)))
    builder.row(_b(f.BTN_SALE_BACK, SaleCB(action="cats")))
    return builder.as_markup()


def sale_qty_kb() -> InlineKeyboardMarkup:
    """1 to 4 on buttons; anything else is typed."""
    builder = InlineKeyboardBuilder()
    builder.row(*[_b(str(n), SaleCB(action="qty", value=n)) for n in (1, 2, 3, 4)])
    builder.row(_b(f.BTN_SALE_BACK, SaleCB(action="cats")))
    return builder.as_markup()


def sale_basket_kb(basket: BasketView) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(_b(f.BTN_SALE_ADD, SaleCB(action="more")))
    if basket.lines:
        builder.row(
            _b(f.BTN_SALE_PRICE, SaleCB(action="price")),
            _b(f.BTN_SALE_DROP, SaleCB(action="drop")),
        )
    builder.row(_b(f.BTN_SALE_DATE, SaleCB(action="date")))
    if basket.lines:
        builder.row(_b(f.BTN_SALE_SAVE, SaleCB(action="save")))
    builder.row(_b(f.BTN_SALE_CANCEL, SaleCB(action="cancel")))
    return builder.as_markup()


def sale_recorded_kb(view: RecordedSaleView) -> InlineKeyboardMarkup:
    """The undo. Carries a `sale` row id, which is all a 64-byte payload can hold."""
    builder = InlineKeyboardBuilder()
    builder.row(_b(f.BTN_SALE_VOID, SaleCB(action="void", value=view.first_sale_id)))
    return builder.as_markup()


def cash_day_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _b(f.BTN_CASH_TODAY, CashCB(action="today")),
        _b(f.BTN_CASH_YESTERDAY, CashCB(action="yesterday")),
    )
    builder.row(
        _b(f.BTN_CASH_OTHER, CashCB(action="other")),
        _b(f.BTN_CASH_CANCEL, CashCB(action="cancel")),
    )
    return builder.as_markup()


def export_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _b(f.BTN_EXPORT_TODAY, ExportCB(action="today")),
        _b(f.BTN_EXPORT_WEEK, ExportCB(action="week")),
    )
    builder.row(
        _b(f.BTN_EXPORT_MONTH, ExportCB(action="month")),
        _b(f.BTN_EXPORT_ALL, ExportCB(action="all")),
    )
    return builder.as_markup()


def import_confirm_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _b(f.BTN_IMPORT_WRITE, ImportCB(action="write")),
        _b(f.BTN_IMPORT_CANCEL, ImportCB(action="cancel")),
    )
    return builder.as_markup()


def import_platform_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        _b(f.BTN_IMPORT_DELIVEROO, ImportCB(action="platform", value="DELIVEROO")),
        _b(f.BTN_IMPORT_JUST_EAT, ImportCB(action="platform", value="JUST_EAT")),
    )
    builder.row(_b(f.BTN_IMPORT_CANCEL, ImportCB(action="cancel")))
    return builder.as_markup()
