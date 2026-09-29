"""The original routes. Thin on purpose: parse, delegate to a view on a thread, return.

Everything except `/api/health` and `/api/meta` requires auth (`/api/meta` withholds the
labour rate from a caller without it). In THIS module everything except the
composition-editor, import-review and confirm POSTs is a GET. The redesign's write routes
live in `cafeops/api/areas/` (stock, menu, finance, shell), one module per area.

No route in THIS module creates, confirms or sends a purchase order, and
`GET /api/orders/draft` computes a full ordering run and persists none of it. Since
2026-09-26 (owner: the bot is not in use) a named person creates and confirms orders
from the back office, in `api/areas/stock.py` via `services/web_orders.py`; invariant 1
stands, because confirmation still needs a name. Nothing is ever *sent* by the API.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, status

from cafeops.api import views
from cafeops.api.params import as_of as as_of_param
from cafeops.api.params import enum_list
from cafeops.api.runtime import in_session
from cafeops.api.schemas import (
    ApplyResponse,
    ChannelsResponse,
    ComponentQtyApply,
    ComponentQtyChange,
    DraftOrdersResponse,
    Health,
    MarginResponse,
    MaterialiseIn,
    MaterialiseResponse,
    Meta,
    PreviewResponse,
    ProposalsResponse,
    ShelfLifeIn,
    ShelfLifeResponse,
    StockDetail,
    StockResponse,
    SupplierOut,
    SupplierTermsIn,
    SupplierTermsResponse,
    TakingsResponse,
    TemplateDetail,
    TemplateSummary,
    TodayResponse,
)
from cafeops.api.security import ApiAuth, OptionalApiAuth
from cafeops.domain.types import Tier

__all__ = ["open_router", "router"]

#: Unauthenticated. Health must answer before anyone has the password, or "is it up?"
#: and "is my password right?" become the same question.
open_router = APIRouter(tags=["meta"])

#: Everything else.
router = APIRouter(dependencies=[ApiAuth])


def _tiers(raw: str | None) -> tuple[Tier, ...] | None:
    return enum_list(raw, Tier, detail=lambda part: f"{part!r}: expected A, B or C")


# --------------------------------------------------------------------------
# meta
# --------------------------------------------------------------------------


@open_router.get(
    "/api/health",
    response_model=Health,
    summary="Liveness. Row counts only for an authenticated caller.",
)
async def health(authenticated: Annotated[bool, OptionalApiAuth]) -> Health:
    return await in_session(lambda session: views.health_view(session, authenticated=authenticated))


@open_router.get("/api/meta", response_model=Meta, summary="Enums and encoding conventions")
async def meta(authenticated: Annotated[bool, OptionalApiAuth]) -> Meta:
    return await in_session(lambda session: views.meta_view(session, authenticated=authenticated))


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
    at = as_of_param(as_of)
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
    at = as_of_param(as_of)
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
    summary="Apply the change from today (effective-dated; history is not rewritten).",
)
async def apply_edit(template_id: int, body: ComponentQtyApply) -> ApplyResponse:
    return await in_session(
        lambda session: views.apply_edit_view(session, template_id=template_id, body=body)
    )


# --------------------------------------------------------------------------
# import review -- spec 6: detection proposes, a human confirms
# --------------------------------------------------------------------------


@router.get(
    "/api/proposals",
    response_model=ProposalsResponse,
    tags=["composition"],
    summary="Template proposals waiting for a human. WRITES NOTHING.",
)
async def proposals() -> ProposalsResponse:
    return await in_session(views.proposals_view)


@router.post(
    "/api/proposals/{key}/materialise",
    response_model=MaterialiseResponse,
    tags=["composition"],
    summary="Confirm one proposal (by proposal_id) into real composition rows.",
)
async def materialise(key: str, body: MaterialiseIn) -> MaterialiseResponse:
    """`key` is a `proposal_id`. A name is accepted too, but names are not unique
    and an ambiguous one is refused rather than resolved to the first match."""
    return await in_session(
        lambda session: views.materialise_proposal_view(session, key=key, body=body)
    )


@router.get(
    "/api/takings",
    response_model=TakingsResponse,
    tags=["money"],
    summary="What the cafe took, by day and method, and what cannot be summed.",
)
async def takings(
    days: Annotated[int, Query(ge=1, le=365, description="Window length in days.")] = 30,
) -> TakingsResponse:
    return await in_session(lambda session: views.takings_view(session, days=days))


# --------------------------------------------------------------------------
# confirmations -- the operator path for the doctor's two standing warnings
# --------------------------------------------------------------------------


@router.post(
    "/api/suppliers/{supplier_id}/confirm",
    response_model=SupplierTermsResponse,
    tags=["suppliers"],
    summary="Record terms confirmed WITH the supplier, clearing the invented-terms flag.",
)
async def confirm_supplier(supplier_id: int, body: SupplierTermsIn) -> SupplierTermsResponse:
    return await in_session(
        lambda session: views.confirm_supplier_terms_view(
            session, supplier_id=supplier_id, body=body
        )
    )


@router.post(
    "/api/ingredients/{ingredient_id}/shelf-life",
    response_model=ShelfLifeResponse,
    tags=["stock"],
    summary="Record a shelf life somebody checked. Changes order size from the next run.",
)
async def confirm_shelf_life_route(ingredient_id: int, body: ShelfLifeIn) -> ShelfLifeResponse:
    return await in_session(
        lambda session: views.confirm_shelf_life_view(
            session, ingredient_id=ingredient_id, body=body
        )
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
    at = as_of_param(as_of)
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
    at = as_of_param(as_of)
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
    at = as_of_param(as_of)
    return await in_session(
        lambda session: views.today_view(session, as_of=at, with_orders=with_orders)
    )
