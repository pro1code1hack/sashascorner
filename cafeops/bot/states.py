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

__all__ = ["AdhocFlow", "ChecklistFlow", "CountFlow", "DeliveryFlow"]


class CountFlow(StatesGroup):
    awaiting_qty = State()


class ChecklistFlow(StatesGroup):
    awaiting_answer = State()


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
