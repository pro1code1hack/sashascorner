"""The routes. Thin on purpose: parse, delegate to a view on a thread, return.

Everything except `/api/health` and `/api/meta` requires the shared password. Everything
except the two composition-editor POSTs is a GET, and `POST /api/templates/{id}/preview`
writes nothing either -- so `POST /api/templates/{id}/apply` is the only route in this
module that can change the database.

There is deliberately **no route that creates, confirms or sends a purchase order.**
Invariant 1: nothing is ordered without human confirmation, and in v1 that confirmation
happens in Telegram. `GET /api/orders/draft` computes a full ordering run and persists
none of it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from cafeops.api import views
from cafeops.api.runtime import in_session
from cafeops.api.schemas import (
    ApplyResponse,
    ChannelsResponse,
    ComponentQtyApply,
    ComponentQtyChange,
    DraftOrdersResponse,
    Health,
    MarginResponse,
    Meta,
    PreviewResponse,
    StockDetail,
    StockResponse,
    SupplierOut,
    TemplateDetail,
    TemplateSummary,
    TodayResponse,
)
from cafeops.api.security import ApiAuth
from cafeops.config import settings
from cafeops.domain.types import Tier

__all__ = ["open_router", "router"]

#: See `app.HTTP_422` -- Starlette deprecated the named constant, the number did not move.
HTTP_422 = 422

#: Unauthenticated. Health must answer before anyone has the password, or "is it up?"
#: and "is my password right?" become the same question.
open_router = APIRouter(tags=["meta"])

#: Everything else.
router = APIRouter(dependencies=[ApiAuth])


# --------------------------------------------------------------------------
# shared query parsing
# --------------------------------------------------------------------------


def _as_of(raw: str | None) -> datetime | None:
    """`now` | `today` | `yesterday` | YYYY-MM-DD, the same words the CLI accepts.

    A bare date means END of that local day, because "stock as of today" means after
    today's trade, not at midnight before it. Lifted from `cli._parse_as_of` so the two
    surfaces cannot drift on what "today" means.
    """
    if raw is None:
        return None
    tz = settings.tz
    lowered = raw.strip().lower()
    if lowered == "now":
        return datetime.now(UTC)
    if lowered in {"today", "yesterday"}:
        local = datetime.now(tz)
        if lowered == "yesterday":
            local -= timedelta(days=1)
        return local.replace(hour=23, minute=59, second=59, microsecond=0).astimezone(UTC)
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=HTTP_422,
            detail=f"{raw!r}: expected 'now', 'today', 'yesterday' or YYYY-MM-DD",
        ) from exc
    return datetime.combine(parsed, time.max, tzinfo=tz).astimezone(UTC)


def _tiers(raw: str | None) -> tuple[Tier, ...] | None:
    if raw is None:
        return None
    out: list[Tier] = []
    for part in raw.split(","):
        token = part.strip().upper()
        if not token:
            continue
        try:
            out.append(Tier(token))
        except ValueError as exc:
            raise HTTPException(
                status_code=HTTP_422,
                detail=f"{part!r}: expected A, B or C",
            ) from exc
    return tuple(out) or None


# --------------------------------------------------------------------------
# meta
# --------------------------------------------------------------------------


@open_router.get("/api/health", response_model=Health, summary="Liveness and row counts")
async def health() -> Health:
    return await in_session(views.health_view)


@open_router.get("/api/meta", response_model=Meta, summary="Enums and encoding conventions")
async def meta() -> Meta:
    return await in_session(views.meta_view)


# --------------------------------------------------------------------------
# templates and the composition editor
# --------------------------------------------------------------------------


@router.get(
    "/api/templates",
    response_model=tuple[TemplateSummary, ...],
    tags=["composition"],
    summary="Materialised drink templates",
)
async def templates(
    as_of: Annotated[str | None, Query(description="now | today | yesterday | YYYY-MM-DD")] = None,
) -> tuple[TemplateSummary, ...]:
    at = _as_of(as_of)
    return await in_session(lambda session: views.templates_view(session, at=at))


@router.get(
    "/api/templates/{template_id}",
    response_model=TemplateDetail,
    tags=["composition"],
    summary="One template: slots, axes, modifiers and the items that fall out",
)
async def template_detail(
    template_id: int,
    as_of: Annotated[str | None, Query(description="Which day's recipe to show.")] = None,
) -> TemplateDetail:
    at = _as_of(as_of)
    return await in_session(
        lambda session: views.template_detail_view(session, template_id=template_id, at=at)
    )


@router.post(
    "/api/templates/{template_id}/preview",
    response_model=PreviewResponse,
    tags=["composition"],
    summary="What a per-size quantity change would do. WRITES NOTHING.",
)
async def preview_edit(template_id: int, body: ComponentQtyChange) -> PreviewResponse:
    return await in_session(
        lambda session: views.preview_edit_view(session, template_id=template_id, body=body)
    )


@router.post(
    "/api/templates/{template_id}/apply",
    response_model=ApplyResponse,
    status_code=status.HTTP_200_OK,
    tags=["composition"],
    summary="Apply the change from today. The only write in this API.",
)
async def apply_edit(template_id: int, body: ComponentQtyApply) -> ApplyResponse:
    return await in_session(
        lambda session: views.apply_edit_view(session, template_id=template_id, body=body)
    )


# --------------------------------------------------------------------------
# stock
# --------------------------------------------------------------------------


@router.get(
    "/api/stock",
    response_model=StockResponse,
    tags=["stock"],
    summary="Theoretical on-hand, batches, drift attribution and projected run-out",
)
async def stock(
    as_of: Annotated[str | None, Query(description="now | today | yesterday | YYYY-MM-DD")] = None,
    tier: Annotated[str | None, Query(description="Comma-separated: A, B, C.")] = None,
    include_untracked: Annotated[bool, Query()] = False,
    run_out: Annotated[
        bool,
        Query(
            description=(
                "Compute the projected run-out date. One forecast per ingredient, so it is "
                "the slow part of this endpoint."
            )
        ),
    ] = True,
) -> StockResponse:
    at = _as_of(as_of)
    tiers = _tiers(tier)
    return await in_session(
        lambda session: views.stock_view(
            session,
            as_of=at,
            tiers=tiers,
            include_untracked=include_untracked,
            with_run_out=run_out,
        )
    )


@router.get(
    "/api/stock/{ingredient_id}",
    response_model=StockDetail,
    tags=["stock"],
    summary="One ingredient, with its drift history and waste-factor proposal",
)
async def stock_detail(
    ingredient_id: int,
    as_of: Annotated[str | None, Query()] = None,
    history: Annotated[int, Query(ge=1, le=100)] = 12,
) -> StockDetail:
    at = _as_of(as_of)
    return await in_session(
        lambda session: views.stock_detail_view(
            session, ingredient_id=ingredient_id, as_of=at, history=history
        )
    )


# --------------------------------------------------------------------------
# orders and suppliers
# --------------------------------------------------------------------------


@router.get(
    "/api/suppliers",
    response_model=tuple[SupplierOut, ...],
    tags=["orders"],
    summary="Supplier terms, and which of them are invented placeholders",
)
async def suppliers() -> tuple[SupplierOut, ...]:
    return await in_session(views.suppliers_view)


@router.get(
    "/api/orders/draft",
    response_model=DraftOrdersResponse,
    tags=["orders"],
    summary="One ordering run: N drafts by supplier, cap reasons, the Tesco list. WRITES NOTHING.",
)
async def draft_orders(
    order_date: Annotated[date | None, Query(description="Defaults to today, local.")] = None,
    tier: Annotated[str | None, Query(description="Comma-separated. Default A,B.")] = None,
    reorder_cadence_days: Annotated[
        int | None,
        Query(
            ge=1,
            le=90,
            description=(
                "The real reordering interval. Omit for 'the next slot the supplier offers'. "
                "Spec 5.4's middle term: a weekly order sized against a one-day gap "
                "under-orders sevenfold."
            ),
        ),
    ] = None,
    require_auto_order: Annotated[
        bool,
        Query(
            description=(
                "Gate on par_level.auto_order_enabled (invariant 2). False by default because "
                "every order here is a DRAFT a human must confirm anyway; true is the "
                "unattended path."
            )
        ),
    ] = False,
    supplier_id: Annotated[list[int] | None, Query(description="Repeatable.")] = None,
    min_order_pence: Annotated[
        int | None,
        Query(ge=0, description="Override every supplier's minimum, for a what-if."),
    ] = None,
) -> DraftOrdersResponse:
    tiers = _tiers(tier) or (Tier.A, Tier.B)
    ids = tuple(supplier_id) if supplier_id else None
    return await in_session(
        lambda session: views.draft_orders_view(
            session,
            order_date=order_date,
            tiers=tiers,
            reorder_cadence_days=reorder_cadence_days,
            require_auto_order=require_auto_order,
            supplier_ids=ids,
            min_order_pence=min_order_pence,
        )
    )


# --------------------------------------------------------------------------
# margin
# --------------------------------------------------------------------------


@router.get(
    "/api/margin",
    response_model=MarginResponse,
    tags=["margin"],
    summary=(
        "The menu ranked by margin % AND by margin-per-minute. The disagreement is the finding."
    ),
)
async def margin(
    template: Annotated[str | None, Query(description="Restrict to one template by name.")] = None,
    window_days: Annotated[int, Query(ge=1, le=365)] = 30,
    until: Annotated[date | None, Query()] = None,
    loaded_hourly_rate_pence: Annotated[
        int | None,
        Query(
            ge=0,
            description=(
                "What-if rate for the RANKING only. Each row's cached labour keeps the rate "
                "that was in force when it was computed -- that is the auditable figure."
            ),
        ),
    ] = None,
) -> MarginResponse:
    return await in_session(
        lambda session: views.margin_view(
            session,
            template=template,
            window_days=window_days,
            until=until,
            loaded_hourly_rate_pence=loaded_hourly_rate_pence,
        )
    )


# --------------------------------------------------------------------------
# channels
# --------------------------------------------------------------------------


@router.get(
    "/api/channels",
    response_model=ChannelsResponse,
    tags=["channels"],
    summary="ROAS, contribution after commission and ad spend, ranks-well-converts-badly",
)
async def channels(
    since: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
    days: Annotated[int, Query(ge=1, le=400)] = 28,
    min_views: Annotated[int, Query(ge=1)] = 100,
) -> ChannelsResponse:
    return await in_session(
        lambda session: views.channels_view(
            session, since=since, until=until, days=days, min_views=min_views
        )
    )


# --------------------------------------------------------------------------
# today
# --------------------------------------------------------------------------


@router.get(
    "/api/today",
    response_model=TodayResponse,
    tags=["today"],
    summary="What would otherwise take four screens to find out",
)
async def today(
    as_of: Annotated[str | None, Query()] = None,
    with_orders: Annotated[
        bool, Query(description="Also compute the draft-order figures. Several seconds.")
    ] = False,
) -> TodayResponse:
    at = _as_of(as_of)
    return await in_session(
        lambda session: views.today_view(session, as_of=at, with_orders=with_orders)
    )
