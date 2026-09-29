"""Hand a CONFIRMED purchase order to its supplier's channel. Invariant 1 throughout.

Nothing here SENDS an order. There are three outcomes, and each says plainly whether a
person still has to act:

* **A basket is staged on the supplier's website.** A supplier with a browser portal
  adapter (`integrations/suppliers/portals/`) is handed to `browser_jobs.enqueue_stage_
  basket` -- the same path as the web's "Stage basket" button. Tier 0 builds a cart link
  immediately; otherwise a STAGE_BASKET job is queued for `cafeops browser-worker`. The
  worker stops at the basket, so the order stays CONFIRMED: a person checks out, then
  records "Mark sent". This replaces the old "Browser agent not wired yet" stub.
* **The channel adapter answers.** MANUAL (a shopping list), and any channel staging
  refused for a stated reason (no adapter, the worker is off, the sign-in has lapsed):
  the adapter prepares instructions, and only an adapter that reports success -- today,
  MANUAL -- moves the order to SENT, signed by `sent_by`.
* **Refused.** An order that is not CONFIRMED is refused before anything happens.

Services flush; the caller commits (ARCHITECTURE 8Y).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from cafeops.db.models import (
    BrowserJob,
    Ingredient,
    POStatus,
    PurchaseOrder,
    Supplier,
    SupplierProduct,
)
from cafeops.db.repositories.purchase_order import SqlPurchaseOrderRepository
from cafeops.domain.enums import BrowserJobStatus, OrderChannel
from cafeops.integrations.suppliers import OrderItem, adapter_for
from cafeops.integrations.suppliers.portals.base import portal_for_supplier
from cafeops.services.actor import require_actor
from cafeops.services.browser_jobs import BrowserJobRefused, enqueue_stage_basket
from cafeops.services.order_actions import OrderActionRefused, allowed_actions, mark_order_sent

__all__ = ["DispatchOutcome", "StagedBasket", "dispatch_confirmed_order", "stages_basket"]

#: Channels whose supplier is reached through its website. EMAIL and EDI are not.
_WEB_CHANNELS = frozenset({OrderChannel.PORTAL, OrderChannel.BROWSER_AGENT})


@dataclass(frozen=True, slots=True)
class StagedBasket:
    """A basket staging that was started for this order."""

    job_id: int
    #: QUEUED (the worker will fill it) or SUCCEEDED (a cart link, ready now).
    status: BrowserJobStatus
    #: The link to open when the basket was built by cart link; None while queued.
    basket_url: str | None


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    po_id: int
    supplier_name: str
    channel: OrderChannel
    #: True only when the order is now SENT (a MANUAL shopping list today).
    sent: bool
    #: Always True: no channel completes an order without a person (ARCHITECTURE 4).
    requires_human_completion: bool
    target_url: str | None
    instruction_steps: int
    items: int
    total_pence: int
    staged: StagedBasket | None = None
    #: Why a website supplier's basket could NOT be staged, verbatim from the service.
    staging_refused: str | None = None


def stages_basket(supplier: Supplier) -> bool:
    """Does confirming an order for this supplier stage a basket on its website?

    True for a PORTAL or BROWSER_AGENT supplier with a registered portal adapter. The
    bot's order card reads this BEFORE confirmation, so what it promises is what
    `dispatch_confirmed_order` then attempts."""
    return supplier.order_channel in _WEB_CHANNELS and portal_for_supplier(supplier) is not None


def _items(session: Session, po_id: int) -> tuple[OrderItem, ...]:
    out: list[OrderItem] = []
    for line in SqlPurchaseOrderRepository(session).get_lines(po_id):
        if line.final_packs <= 0:
            continue
        product = session.get(SupplierProduct, line.supplier_product_id)
        ingredient = session.get(Ingredient, line.ingredient_id)
        if product is None or ingredient is None:
            continue
        out.append(
            OrderItem(
                ingredient_name=ingredient.name,
                sku=product.sku,
                packs=line.final_packs,
                pack_size=product.pack_size,
                unit_price_pence=line.unit_price_pence,
                product_url=product.product_url,
            )
        )
    return tuple(out)


def _staged(job: BrowserJob) -> StagedBasket:
    url = None
    if job.status is BrowserJobStatus.SUCCEEDED and isinstance(job.result, dict):
        raw = job.result.get("basket_url")
        url = str(raw) if raw else None
    return StagedBasket(job_id=job.id, status=job.status, basket_url=url)


def dispatch_confirmed_order(
    session: Session, *, po_id: int, sent_by: str, via: str = "telegram"
) -> DispatchOutcome:
    """Stage, list or refuse -- never send. See the module docstring for the rules."""
    who = require_actor(sent_by, error=OrderActionRefused)
    order = session.get(PurchaseOrder, po_id)
    if order is None:
        raise LookupError(f"purchase order {po_id} not found")
    if "mark_sent" not in allowed_actions(order.status):
        raise OrderActionRefused(
            f"purchase order {po_id} is {order.status.value}; only a CONFIRMED order can "
            "be dispatched (invariant 1)"
        )

    supplier = order.supplier
    channel = supplier.order_channel
    items = _items(session, po_id)
    adapter = adapter_for(channel)
    prepared = adapter.prepare(po_id, supplier, items)
    steps = sum(1 for row in prepared.instructions.splitlines() if row.strip())

    def outcome(
        *, sent: bool, staged: StagedBasket | None = None, refused: str | None = None
    ) -> DispatchOutcome:
        return DispatchOutcome(
            po_id=po_id,
            supplier_name=supplier.name,
            channel=channel,
            sent=sent,
            requires_human_completion=True,
            target_url=prepared.target_url,
            instruction_steps=steps,
            items=len(items),
            total_pence=prepared.total_pence,
            staged=staged,
            staging_refused=refused,
        )

    staging_refused: str | None = None
    if stages_basket(supplier):
        try:
            job = enqueue_stage_basket(session, po_id=po_id, requested_by=who, via=via)
        except BrowserJobRefused as exc:
            staging_refused = str(exc)
        else:
            # Staged, not sent: the order stays CONFIRMED until a person checks out.
            return outcome(sent=False, staged=_staged(job))

    result = adapter.dispatch(prepared)
    sent = bool(result.succeeded and order.status is POStatus.CONFIRMED)
    if sent:
        mark_order_sent(session, po_id=po_id, sent_by=who, at=result.dispatched_at)
    return outcome(sent=sent, refused=staging_refused)
