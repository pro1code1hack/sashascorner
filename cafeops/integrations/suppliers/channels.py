"""Concrete order channels.

Phase 0 registers all four so `send_order` has a complete switch and no silent
gaps. MANUAL and BROWSER_AGENT are the two that matter for Sasha today; EMAIL
and PORTAL are wired but deliberately refuse to transmit until Phase 2 gives
them credentials, rather than pretending to succeed.
"""

from __future__ import annotations

from cafeops.clock import utcnow
from cafeops.domain.types import OrderChannel
from cafeops.integrations.suppliers.base import (
    DispatchResult,
    OrderChannelAdapter,
    OrderItem,
    PreparedOrder,
    register,
)


@register
class ManualChannel(OrderChannelAdapter):
    """Tesco and anything else the owner physically walks to.

    There is nothing to transmit. The deliverable is a shopping list, and
    "dispatched" means the list reached the owner's phone.
    """

    channel = OrderChannel.MANUAL

    def prepare(self, po_id: int, supplier: object, items: tuple[OrderItem, ...]) -> PreparedOrder:
        total = sum(i.line_total_pence for i in items)
        return PreparedOrder(
            po_id=po_id,
            supplier_name=getattr(supplier, "name", "?"),
            channel=self.channel,
            items=items,
            total_pence=total,
            instructions="Shopping list for a walk-in purchase.",
        )

    def dispatch(self, prepared: PreparedOrder) -> DispatchResult:
        return DispatchResult(
            po_id=prepared.po_id,
            channel=self.channel,
            succeeded=True,
            dispatched_at=utcnow(),
            detail="Shopping list issued; purchase happens in person.",
            requires_human_completion=True,
        )


@register
class BrowserAgentChannel(OrderChannelAdapter):
    """Cups Direct and similar: a web shop with a basket and no API.

    `prepare` builds an instruction script and stops. The real basket is staged by
    `services/order_dispatch`, which hands a supplier with a portal adapter to the
    browser worker (`services/browser_jobs.enqueue_stage_basket`); the worker halts
    *before* checkout. `dispatch` here is only the fallback when staging was refused
    (no adapter, worker off, sign-in lapsed). `requires_human_completion` is always
    True: nothing is purchased without a person pressing the last button, which is
    operational rule 1 surviving contact with automation.
    """

    channel = OrderChannel.BROWSER_AGENT

    def prepare(self, po_id: int, supplier: object, items: tuple[OrderItem, ...]) -> PreparedOrder:
        total = sum(i.line_total_pence for i in items)
        base_url = getattr(supplier, "order_url", None)
        extra = getattr(supplier, "agent_instructions", None)

        steps = [
            f"Open {base_url}" if base_url else "Open the supplier's web shop",
            "Sign in with the stored account if prompted.",
        ]
        for item in items:
            where = item.product_url or f"search for SKU {item.sku or item.ingredient_name}"
            steps.append(
                f"Add {item.packs} x {item.ingredient_name} ({item.pack_size} per pack) -- {where}"
            )
        steps += [
            f"Verify the basket subtotal is about {total / 100:.2f} GBP.",
            "STOP at the basket. Do not place the order. Report the basket back "
            "for human completion.",
        ]
        if extra:
            steps.append(f"Supplier-specific notes: {extra}")

        return PreparedOrder(
            po_id=po_id,
            supplier_name=getattr(supplier, "name", "?"),
            channel=self.channel,
            items=items,
            total_pence=total,
            instructions="\n".join(f"{n}. {s}" for n, s in enumerate(steps, 1)),
            target_url=base_url,
            metadata={"halt_before_checkout": True},
        )

    def dispatch(self, prepared: PreparedOrder) -> DispatchResult:
        # The fallback: staging was refused upstream, so hand the script back rather
        # than claim to have done anything.
        return DispatchResult(
            po_id=prepared.po_id,
            channel=self.channel,
            succeeded=False,
            dispatched_at=utcnow(),
            detail=(
                "Basket not staged (see the staging refusal). Instruction script "
                "prepared and ready:\n" + prepared.instructions
            ),
            requires_human_completion=True,
        )


class _NotYetConfigured(OrderChannelAdapter):
    """Shared behaviour for channels that exist but have no credentials."""

    def prepare(self, po_id: int, supplier: object, items: tuple[OrderItem, ...]) -> PreparedOrder:
        return PreparedOrder(
            po_id=po_id,
            supplier_name=getattr(supplier, "name", "?"),
            channel=self.channel,
            items=items,
            total_pence=sum(i.line_total_pence for i in items),
            instructions=f"{self.channel.value} channel is not configured yet.",
            target_url=getattr(supplier, "order_url", None),
        )

    def dispatch(self, prepared: PreparedOrder) -> DispatchResult:
        return DispatchResult(
            po_id=prepared.po_id,
            channel=self.channel,
            succeeded=False,
            dispatched_at=utcnow(),
            detail=(
                f"{self.channel.value} channel is not configured. "
                "Order left CONFIRMED; send it by hand or configure the channel."
            ),
            requires_human_completion=True,
        )


@register
class EmailChannel(_NotYetConfigured):
    """CakeSmiths-style wholesale ordering by email."""

    channel = OrderChannel.EMAIL


@register
class PortalChannel(_NotYetConfigured):
    """A supplier portal with a real API. None of Sasha's have one today."""

    channel = OrderChannel.PORTAL
