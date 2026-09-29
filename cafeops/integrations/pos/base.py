"""The POS-sink protocol (CONTRACT §3b.3). Sync: a sink is called from a worker thread."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from cafeops.db.models import ShopOrder

__all__ = ["PosOrderSink", "PosPushResult"]


@dataclass(frozen=True, slots=True)
class PosPushResult:
    ok: bool
    #: The POS's reference for the order (`shop_order.pos_ref`), when it gave one.
    external_ref: str | None
    detail: str


@runtime_checkable
class PosOrderSink(Protocol):
    key: str
    display_name: str

    def configured(self) -> bool: ...

    def push_order(self, order: ShopOrder) -> PosPushResult: ...

    def mark_paid(self, order: ShopOrder) -> PosPushResult: ...

    def cancel(self, order: ShopOrder) -> PosPushResult: ...
