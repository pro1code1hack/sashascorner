"""Placing and confirming orders from the back office (owner, 2026-09-26).

Until now an order became real only in Telegram: the pre-delivery job wrote a DRAFT and
the bot asked a human to confirm it. The owner is not using the bot for now ("no
telegram interaction will be there for now"), so the web does the same two steps:

1. `create_order_from_draft` turns one supplier's basket from the live ordering run
   into a DRAFT purchase order -- exactly what `jobs/pre_delivery_orders.py` writes,
   through the same `create_draft_po`, with the same one-open-order-per-delivery-date
   guard. A DRAFT is a proposal; nothing is ordered.
2. `confirm_order` is the human decision. INVARIANT 1 is unchanged: the confirmation
   carries a name (`SqlPurchaseOrderRepository.confirm` refuses an empty one, and
   `ck_po_confirmed_requires_human` refuses the row underneath), and it records the
   packs the person settled on, line by line.

Nothing here sends anything to a supplier. "Mark sent" stays a separate, signed step.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import PurchaseOrder
from cafeops.db.repositories.purchase_order import SqlPurchaseOrderRepository
from cafeops.services.actor import require_actor
from cafeops.services.build_order import build_split, create_draft_po
from cafeops.services.order_actions import CONFIRMABLE, OPEN_STATUSES, OrderActionRefused

__all__ = ["confirm_order", "create_order_from_draft"]


def _existing_open(session: Session, *, supplier_id: int, target: date) -> int | None:
    return session.scalar(
        select(PurchaseOrder.id)
        .where(PurchaseOrder.supplier_id == supplier_id)
        .where(PurchaseOrder.target_delivery_date == target)
        .where(PurchaseOrder.status.in_(list(OPEN_STATUSES)))
        .limit(1)
    )


def create_order_from_draft(session: Session, *, supplier_id: int, created_by: str) -> int:
    """Write this supplier's basket from today's run as a DRAFT order; return its id.

    The run is recomputed here rather than trusted from the page: the basket must be the
    one the ordering rules produce now (shelf-life and season caps, invariant 4; no
    perishable top-ups, invariant 5), not whatever a browser tab remembered.
    """
    if not created_by.strip():
        raise OrderActionRefused("created_by is required: this is somebody's decision")
    order_date = datetime.now(UTC).astimezone(settings.tz).date()
    result = build_split(session, order_date=order_date)
    plan = next((p for p in result.plans if p.suggestion.supplier.id == supplier_id), None)
    if plan is None or not plan.suggestion.lines:
        # An open draft already counts against the need, so say that rather than "nothing".
        pending = session.scalar(
            select(PurchaseOrder.id)
            .where(PurchaseOrder.supplier_id == supplier_id)
            .where(PurchaseOrder.status.in_(list(CONFIRMABLE)))
            .limit(1)
        )
        if pending is not None:
            raise OrderActionRefused(f"order {pending} is already waiting to be confirmed; open it")
        raise OrderActionRefused("nothing needs ordering from this supplier right now")
    target = plan.suggestion.target_delivery_date
    existing = _existing_open(session, supplier_id=supplier_id, target=target)
    if existing is not None:
        raise OrderActionRefused(
            f"order {existing} is already open for this supplier's {target:%a %d %b} delivery; "
            "open it instead of making a second one"
        )
    po_id = create_draft_po(
        session, plan, routing_reason=f"Created in the back office by {created_by.strip()[:80]}"
    )
    if po_id is None:  # pragma: no cover - guarded by `plan.suggestion.lines` above
        raise OrderActionRefused("nothing needs ordering from this supplier right now")
    session.flush()
    return po_id


def confirm_order(
    session: Session, *, po_id: int, confirmed_by: str, final_packs: Mapping[int, int]
) -> PurchaseOrder:
    """INVARIANT 1: a named human confirms, with the packs they settled on.

    The one confirmation path: the web's Confirm and the bot's «Подтвердить» both land
    here, so both get the all-lines-at-zero refusal and the same name rule.
    """
    who = require_actor(confirmed_by, error=OrderActionRefused)
    po = session.get(PurchaseOrder, po_id)
    if po is None:
        raise LookupError(f"purchase order {po_id} not found")
    if po.status not in CONFIRMABLE:
        raise OrderActionRefused(
            f"order {po_id} is {po.status.value.lower()}; only a draft can be confirmed"
        )
    if (
        final_packs
        and all(packs == 0 for packs in final_packs.values())
        and len(final_packs) == len(po.lines)
    ):
        raise OrderActionRefused(
            "every line is at zero: cancel the order instead of confirming nothing"
        )
    SqlPurchaseOrderRepository(session).confirm(
        po_id,
        confirmed_by=who,
        at=datetime.now(UTC),
        final_packs=final_packs,
    )
    session.flush()
    return po
