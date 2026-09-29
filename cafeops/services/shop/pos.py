"""Pushing a NEW order to the POS sink (CONTRACT §3b.3). Best effort, after commit.

`push_order_to_pos(order_id)` runs as a background task (or from the scheduler):
its own session, the sink from `shop_settings.pos_sink`, one event row either way
(`pos_pushed` / `pos_failed`), `shop_order.pos_ref` on success. Idempotent: an order
that already has a `pos_ref` is not pushed twice.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from cafeops.db.base import session_scope
from cafeops.db.models import OrderStatus, ShopOrder, ShopSettings
from cafeops.integrations.pos import registry
from cafeops.services.shop.orders import record_event

__all__ = ["push_order_to_pos"]

log = logging.getLogger("cafeops.shop.pos")


def push_order_to_pos(order_id: int) -> None:
    try:
        with session_scope() as session:
            order = session.get(ShopOrder, order_id)
            shop = session.get(ShopSettings, 1)
            if order is None or shop is None:
                return
            chosen = registry.sink(shop.pos_sink)
            if chosen.key == "none" or order.pos_ref or order.status is not OrderStatus.NEW:
                return
            result = chosen.push_order(order)
            now = datetime.now(UTC)
            if result.ok:
                order.pos_ref = (result.external_ref or "")[:80] or None
                record_event(
                    session,
                    order,
                    "pos_pushed",
                    f"{chosen.display_name}: {result.detail}",
                    actor="system",
                    at=now,
                )
            else:
                record_event(
                    session,
                    order,
                    "pos_failed",
                    f"{chosen.display_name}: {result.detail}",
                    actor="system",
                    at=now,
                )
    except Exception as exc:
        log.warning("push_order_to_pos(%s) failed: %s", order_id, exc)
