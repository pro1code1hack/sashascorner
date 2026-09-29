"""Callback payloads. Typed factories rather than hand-split strings.

Telegram gives 64 bytes for callback data and returns it verbatim, so the temptation is
`f"ord:{po_id}:{line_id}:+"` and a `split(":")` at the other end. `CallbackData` is used
instead because a stale keyboard is normal here -- the morning digest sits in the chat
all day -- and an unparseable payload from yesterday's message must come back as a
typed mismatch the router simply does not match, not as an `IndexError` or, worse, an
off-by-one that adjusts the wrong line of the wrong order.
"""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData

__all__ = [
    "CashCB",
    "ChecklistCB",
    "CountCB",
    "DeliveryCB",
    "DigestCB",
    "ExportCB",
    "ImportCB",
    "OrderCB",
    "SaleCB",
]


class OrderCB(CallbackData, prefix="ord"):
    """+/- on one line, or a decision on the whole order.

    `line_id` is 0 for order-level actions. Both ids travel so the handler never has to
    trust FSM state to know WHICH order a tap belongs to: two suppliers' cards are in the
    chat at once by design (one order per supplier, spec 5.5), and inferring the order
    from "the last one shown" is how a confirmation lands on the wrong basket.
    """

    action: str  # "inc" | "dec" | "reset" | "confirm" | "cancel"
    po_id: int
    line_id: int = 0


class CountCB(CallbackData, prefix="cnt"):
    action: str  # "skip" | "stop"
    ingredient_id: int = 0


class ChecklistCB(CallbackData, prefix="chk"):
    action: str  # "ok" | "low" | "skip" | "no_order"
    ingredient_id: int


class DeliveryCB(CallbackData, prefix="dlv"):
    action: str  # "no_date" | "skip"
    po_line_id: int = 0


class DigestCB(CallbackData, prefix="dig"):
    action: str  # "orders" | "count" | "checklist" | "delivery"


class SaleCB(CallbackData, prefix="sal"):
    """The hand-typed sale (DECISIONS 28).

    `value` is overloaded by action and always an integer, because a menu item name
    does not fit in 64 bytes and a category name may not either: `chan` carries the
    index into `MANUAL_SALE_CHANNELS`, `cat` the index into the category list the
    view produced (with `page`), `pick` a menu item id, `qty` a count, `void` the id of
    the receipt's first `sale` row. Everything the handler needs to act is re-read
    from the database by that id, never trusted from the button.
    """

    #: chan | cats | cat | pick | qty | more | price | drop | date | save | cancel | void
    action: str
    value: int = 0
    page: int = 0


class CashCB(CallbackData, prefix="csh"):
    action: str  # today | yesterday | other | cancel


class ImportCB(CallbackData, prefix="imp"):
    action: str  # write | cancel | platform
    value: str = ""  # platform: DELIVEROO | JUST_EAT


class ExportCB(CallbackData, prefix="exp"):
    action: str  # today | week | month | all
