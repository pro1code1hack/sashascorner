"""Router registry. Order matters.

`common` is first so `/start` and `/help` always answer even mid-flow. The walking
flows come next, and each owns its own FSM state so they cannot match each other's
messages; `sale`, `cash` and `files` (DECISIONS 28) are the same shape. `digest` and
`orders` are stateless commands and go last.
"""

from __future__ import annotations

from aiogram import Router

from cafeops.bot import shop_handlers
from cafeops.bot.handlers import (
    cash,
    checklist,
    common,
    count,
    delivery,
    digest,
    files,
    member,
    orders,
    sale,
)

__all__ = ["ALL_ROUTERS", "build_root_router"]

ALL_ROUTERS: tuple[Router, ...] = (
    common.router,
    count.router,
    checklist.router,
    delivery.router,
    sale.router,
    cash.router,
    files.router,
    digest.router,
    orders.router,
    member.router,
    shop_handlers.router,
)


def build_root_router(*, owner_only: bool = True) -> Router:
    """One router with everything attached, gated to the owner unless told otherwise.

    `owner_only=False` exists for the local preview, which has no Telegram account to be
    the owner of. It is never False in `app.main`.
    """
    root = Router(name="root")
    if owner_only:
        common.owner_only(*ALL_ROUTERS)
    root.include_routers(*ALL_ROUTERS)
    return root
