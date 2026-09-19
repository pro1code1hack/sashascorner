"""Supplier order channels.

Sasha's suppliers do not share a mechanism: Tesco is a walk-in shop, CakeSmiths
is a wholesale account, Cups Direct is a web shop with no API. Rather than
special-casing each in `send_order`, every channel implements this one interface.

Operational rule 1 is structural here: `dispatch` is only reachable with an
already-CONFIRMED order carrying a human `confirmed_by`. A channel cannot
submit anything on its own initiative, and `PreparedOrder` is deliberately
inert -- it describes what *would* be submitted so a human can read it first.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from cafeops.domain.types import OrderChannel


@dataclass(frozen=True, slots=True)
class OrderItem:
    ingredient_name: str
    sku: str
    packs: int
    pack_size: Decimal
    unit_price_pence: int
    product_url: str | None = None

    @property
    def line_total_pence(self) -> int:
        return self.packs * self.unit_price_pence


@dataclass(frozen=True, slots=True)
class PreparedOrder:
    """What a channel intends to do, rendered for human eyes before it happens."""

    po_id: int
    supplier_name: str
    channel: OrderChannel
    items: tuple[OrderItem, ...]
    total_pence: int
    # Channel-specific payload: an email body, a portal request, or the
    # instruction script a browser agent will follow.
    instructions: str = ""
    target_url: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DispatchResult:
    po_id: int
    channel: OrderChannel
    succeeded: bool
    dispatched_at: datetime
    # What a human should be told. Never a bare boolean in the Telegram message.
    detail: str = ""
    # True when the channel got as far as a filled basket but a person must
    # still press the final button (the normal outcome for BROWSER_AGENT).
    requires_human_completion: bool = False
    reference: str | None = None


class OrderChannelAdapter(abc.ABC):
    """One way of getting an order to a supplier."""

    #: The enum member this adapter serves.
    channel: OrderChannel

    @abc.abstractmethod
    def prepare(self, po_id: int, supplier: object, items: tuple[OrderItem, ...]) -> PreparedOrder:
        """Build the inert description of the order. Must not contact anyone."""

    @abc.abstractmethod
    def dispatch(self, prepared: PreparedOrder) -> DispatchResult:
        """Actually send it.

        Callers must have verified the PO is CONFIRMED with a recorded
        `confirmed_by` first. Implementations should assume that and fail loudly
        rather than re-deriving policy.
        """

    def describe(self, prepared: PreparedOrder) -> str:
        """Human-readable summary, for the confirmation message."""
        lines = [f"{prepared.supplier_name} - {prepared.channel.value}"]
        for item in prepared.items:
            lines.append(
                f"  {item.ingredient_name}: {item.packs} x {item.pack_size} "
                f"= {item.line_total_pence / 100:.2f} GBP"
            )
        lines.append(f"  TOTAL {prepared.total_pence / 100:.2f} GBP")
        if prepared.target_url:
            lines.append(f"  {prepared.target_url}")
        return "\n".join(lines)


_REGISTRY: dict[OrderChannel, type[OrderChannelAdapter]] = {}


def register(adapter_cls: type[OrderChannelAdapter]) -> type[OrderChannelAdapter]:
    """Decorator: make an adapter discoverable by its channel."""
    _REGISTRY[adapter_cls.channel] = adapter_cls
    return adapter_cls


def adapter_for(channel: OrderChannel) -> OrderChannelAdapter:
    try:
        return _REGISTRY[channel]()
    except KeyError:
        raise LookupError(f"no adapter registered for channel {channel.value}") from None


def registered_channels() -> list[OrderChannel]:
    return sorted(_REGISTRY, key=lambda c: c.value)
