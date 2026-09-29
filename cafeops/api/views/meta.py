"""`/api/health` and `/api/meta`.

`/api/meta` exists so the frontend does not hard-code enum strings or guess at the
encoding rules. A TypeScript union copied by hand from a Python enum is a thing that
goes stale silently; one fetched at boot cannot.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.api.schemas import Health, Meta
from cafeops.config import settings
from cafeops.db.base import get_engine
from cafeops.db.models import DrinkTemplate, Ingredient, MenuItem, StockMovement
from cafeops.domain.drift import DriftCause
from cafeops.domain.types import (
    ComponentRole,
    DriftVerdict,
    MovementType,
    OrderChannel,
    PriceSource,
    SalesChannelName,
    SizeCode,
    Storage,
    Tier,
    Unit,
)
from cafeops.services.auth import auth_is_configured

__all__ = ["health_view", "meta_view"]


def _count(session: Session, model: type) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def health_view(session: Session, *, authenticated: bool = False) -> Health:
    """Liveness, plus row counts only for a caller who proved they belong here.

    `authenticated=False` is the safe default: a new caller of this view gets the
    non-disclosing answer unless it deliberately asks otherwise.
    """
    base = Health(
        status="ok",
        database_dialect=get_engine().dialect.name,
        # True for either source: a stored credential (set from Settings) or the env
        # bootstrap. services/auth.py owns the precedence.
        auth_configured=auth_is_configured(session),
    )
    if not authenticated:
        return base
    return base.model_copy(
        update={
            "ingredients": _count(session, Ingredient),
            "menu_items": _count(session, MenuItem),
            "templates": _count(session, DrinkTemplate),
            "movements": _count(session, StockMovement),
        }
    )


def meta_view(_session: Session, *, authenticated: bool = False) -> Meta:
    """Enums and conventions. `/api/meta` is open (the frontend reads it before sign-in),
    so the one business figure in it -- the loaded labour rate -- is null for a caller
    who has not proved they belong here, like `health_view`'s row counts."""
    return Meta(
        money_encoding=(
            "Integer pence where the database stores an integer (price_pence, "
            "min_order_pence, pack price, order total). An exact decimal STRING of pence "
            "where the figure is derived and fractional (cost_pence, labour_cost_pence, "
            "premium_pence). Never a float -- invariant 11. A missing cost is null, never 0."
        ),
        quantity_encoding=(
            "Exact decimal strings, always -- including qty_by_size on the way in. JSON has "
            "only doubles and a recipe editor that loses 0.1 + 0.2 is unacceptable "
            "(ARCHITECTURE 7.4)."
        ),
        timestamp_encoding=(
            "ISO-8601, tz-aware, UTC. Local days are Europe/London (ARCHITECTURE 6.4)."
        ),
        units=tuple(u.value for u in Unit),
        tiers=tuple(t.value for t in Tier),
        size_codes=tuple(s.value for s in SizeCode),
        component_roles=tuple(r.value for r in ComponentRole),
        price_sources=tuple(p.value for p in PriceSource),
        storage=tuple(s.value for s in Storage),
        movement_types=tuple(m.value for m in MovementType),
        drift_verdicts=tuple(v.value for v in DriftVerdict),
        drift_causes=tuple(c.value for c in DriftCause),
        order_channels=tuple(c.value for c in OrderChannel),
        sales_channels=tuple(c.value for c in SalesChannelName),
        loaded_hourly_rate_pence=settings.loaded_hourly_rate_pence if authenticated else None,
        local_timezone=settings.local_timezone,
        invariant_notes=(
            "6: every stock figure carries is_theoretical and has_count_basis. A figure "
            "with has_count_basis=false is a bare movement sum with no physical anchor.",
            "8: an estimated cost stays flagged through every rollup and aggregate; a "
            "MISSING cost is null. Items with missing costs are returned, flagged, and "
            "excluded from totals -- excluded_from_aggregates says so on the figure itself.",
            "9: a low-confidence forecast has qty=null and reasons populated. Render the "
            "reasons IN PLACE OF the number -- the number is not in the payload.",
            "4: a shelf-life or season cap travels on the order line as cap_reason, with "
            "the arithmetic in cap_detail.",
            "1: nothing is ordered without a named person. GET /api/orders/draft computes "
            "and persists nothing; POST /api/orders/from-draft writes a DRAFT, and only "
            "POST /api/orders/{id}/confirm with the confirming person's name moves it on. "
            "No endpoint sends an order to a supplier.",
        ),
    )
