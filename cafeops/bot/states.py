"""FSM states for the three multi-step flows.

Only the *position* in a walk lives in FSM state -- which item is being asked about, and
which have been answered. Every consequence is written through a service the moment it is
known:

* a count is written by `record_count` as soon as the number arrives, so an abandoned
  session leaves the counts already taken, not nothing;
* a checklist answer likewise;
* a pack adjustment goes straight to `po_line.final_packs`, so a restart between the tap
  and the confirm cannot silently revert a decision.

The one piece of real state is the delivery flow's pending pack count, held only between
"how many packs" and "what is the expiry", because the batch cannot be created until both
are known -- a batch with no date is the thing this flow exists to prevent.
"""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup

__all__ = [
    "AdhocFlow",
    "CashFlow",
    "ChecklistFlow",
    "CountFlow",
    "DeliveryFlow",
    "ImportFlow",
    "SaleFlow",
]


class CountFlow(StatesGroup):
    awaiting_qty = State()


class ChecklistFlow(StatesGroup):
    awaiting_answer = State()
    #: After «заканчивается»: how many packs to put on the draft. The answer is written
    #: the moment it arrives, like every other consequence here -- an abandoned checklist
    #: leaves the lines already requested, not nothing.
    #:
    #: Tier C carries no forecast (spec 4.7), so this is the one quantity in the whole bot
    #: that the system cannot supply and must not guess. It lives in FSM state only
    #: between "running low" and the number, because until both are known there is no
    #: honest line to write.
    awaiting_order_packs = State()


class DeliveryFlow(StatesGroup):
    awaiting_packs = State()
    awaiting_expiry = State()


class AdhocFlow(StatesGroup):
    """The walk to Tesco: no order behind it, so its own states.

    Separate from `DeliveryFlow` rather than a flag inside it. Two flows sharing one
    state means the handler for an ordered delivery fires on an ad-hoc message and
    indexes into an empty queue -- and the failure lands in the middle of somebody
    entering real stock.
    """

    awaiting_qty = State()
    awaiting_expiry = State()


class SaleFlow(StatesGroup):
    """A hand-typed sale (DECISIONS 28). The one flow here that holds a basket.

    The basket -- channel, lines, an optional date -- lives in FSM state until
    «Записать», which is a deliberate exception to "write every consequence the moment
    it is known". A count of four cartons is a fact the moment it is typed; a half-built
    receipt is not a sale yet, and writing it line by line would put a `sale` row into
    the ledger for a customer who then changed their mind. A restart mid-basket loses
    the basket, not a sale.
    """

    #: Choosing items: category buttons on screen, or a typed name to search.
    picking = State()
    #: An item is chosen; how many?
    awaiting_qty = State()
    #: «Другая цена»: the unit price for the last line (delivery apps charge their own).
    awaiting_price = State()
    #: «Другая дата»: a day for the whole receipt, typed as DD.MM.
    awaiting_date = State()


class CashFlow(StatesGroup):
    """The day's one cash figure (DECISIONS 26), written when the amount arrives."""

    awaiting_date = State()
    awaiting_amount = State()


class ImportFlow(StatesGroup):
    """A CSV was sent; the dry run is on screen and the file waits on disk for «Записать».

    State holds the temp path and what the header row said the file is. The write
    happens only on the button, because an import is the one action here that can
    put hundreds of rows in at once and should be read before it is real.
    """

    awaiting_confirm = State()
