"""Sync assembly: services in, finished response models out.

Every function here takes a `Session` and returns a Pydantic model. They run inside
`asyncio.to_thread` (see `api/runtime.py`), which means two rules:

1. **Nothing here is async.** Spec 3: SQLite has one writer and repositories stay sync.
2. **Nothing here returns an ORM object.** The session closes when the function does, so
   a lazy attribute touched by the router would raise. Returning finished models makes
   that impossible rather than unlikely.

Nothing here writes, with one exception the spec requires: `templates.apply_edit`, the
composition editor's commit. Every other function reads. In particular
`orders.draft_orders` computes a full ordering run and persists none of it -- invariant
1 says a purchase order needs a human in Telegram, and there is no endpoint in this
API that creates, confirms or sends one.
"""

from __future__ import annotations

from cafeops.api.views.channels import channels_view
from cafeops.api.views.confirm import confirm_shelf_life_view, confirm_supplier_terms_view
from cafeops.api.views.margin import margin_view
from cafeops.api.views.meta import health_view, meta_view
from cafeops.api.views.orders import draft_orders_view, suppliers_view
from cafeops.api.views.payments import takings_view
from cafeops.api.views.proposals import materialise_proposal_view, proposals_view
from cafeops.api.views.stock import stock_detail_view, stock_view
from cafeops.api.views.templates import (
    apply_edit_view,
    preview_edit_view,
    template_detail_view,
    templates_view,
)
from cafeops.api.views.today import today_view

__all__ = [
    "apply_edit_view",
    "channels_view",
    "confirm_shelf_life_view",
    "confirm_supplier_terms_view",
    "draft_orders_view",
    "health_view",
    "margin_view",
    "materialise_proposal_view",
    "meta_view",
    "preview_edit_view",
    "proposals_view",
    "stock_detail_view",
    "stock_view",
    "suppliers_view",
    "takings_view",
    "template_detail_view",
    "templates_view",
    "today_view",
]
