"""The default sink: the till is not told. Orders live on the back office's board."""

from __future__ import annotations

from cafeops.db.models import ShopOrder
from cafeops.integrations.pos.base import PosPushResult

__all__ = ["NullSink"]


class NullSink:
    key = "none"
    display_name = "None (back office only)"

    def configured(self) -> bool:
        return True

    def push_order(self, order: ShopOrder) -> PosPushResult:
        return PosPushResult(True, None, "no POS sink configured; nothing pushed")

    def mark_paid(self, order: ShopOrder) -> PosPushResult:
        return PosPushResult(True, None, "no POS sink configured")

    def cancel(self, order: ShopOrder) -> PosPushResult:
        return PosPushResult(True, None, "no POS sink configured")
