"""Order online's scheduled work (docs/shop/CONTRACT.md §3.6), as the scheduler calls it.

| job | when | idempotency key |
|---|---|---|
| `shop_expire_pending` | every 5 min | `shop_order.status` (PENDING_PAYMENT -> CANCELLED once) |

A Stripe Checkout the customer abandoned would otherwise hold a collection slot and
a free drink forever.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session, sessionmaker

from cafeops.db.base import SessionFactory, session_scope
from cafeops.services.shop.orders import expire_pending_payments

__all__ = ["job_shop_expire_pending"]

log = logging.getLogger("cafeops.jobs.shop")


def _expire(factory: sessionmaker[Session] | None) -> int:
    with session_scope(factory or SessionFactory) as session:
        return expire_pending_payments(session)


async def job_shop_expire_pending(factory: sessionmaker[Session] | None = None) -> None:
    expired = await asyncio.to_thread(_expire, factory)
    if expired:
        log.info("shop_expire_pending: cancelled %s unpaid order(s)", expired)
