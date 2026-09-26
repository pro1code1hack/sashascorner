"""Routes for the Stock, Orders and Suppliers screens of the back-office redesign.

docs/design/specs/stock-orders-suppliers.md, reconciled with DECISIONS.md. Thin on
purpose, like `api/routers.py`: parse, hand the work to a view on a worker thread, return.

The reads that already existed stay where they were (`GET /api/stock`, `/api/stock/{id}`,
`/api/orders/draft`, `/api/suppliers`, and the two confirmations) and were extended in
place; this module adds the writes and two reads.

**There is still no route that creates, confirms or sends a purchase order** (DECISIONS 1,
invariant 1). Orders here can only be listed, cancelled, marked sent once a human has
confirmed them in Telegram, and received. A shop run records stock and a routing, never
an order.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from cafeops.api.areas import stock_views as views
from cafeops.api.areas.stock_schemas import (
    CancelIn,
    ChecklistIn,
    ChecklistOut,
    CountIn,
    CountOut,
    DeliveryIn,
    DeliveryOut,
    MarkSentIn,
    OrdersListResponse,
    ParChangeOut,
    ParIn,
    ProductActorIn,
    ProductArchiveIn,
    ProductLinkIn,
    ProductPatchIn,
    ProductWriteOut,
    PurchaseOrderOut,
    ReceiveIn,
    ReceiveOut,
    ShopRunIn,
    ShopRunOut,
    ShopRunsResponse,
    SupplierArchiveIn,
    SupplierCreateIn,
    SupplierPatchIn,
    SupplierProductsResponse,
    SupplierWriteOut,
    TierIn,
    TierOut,
    WriteOffIn,
    WriteOffOut,
)
from cafeops.api.runtime import in_session
from cafeops.api.security import ApiAuth
from cafeops.db.models import POStatus

router = APIRouter(dependencies=[ApiAuth])


# --------------------------------------------------------------------------
# stock
# --------------------------------------------------------------------------


@router.post(
    "/api/stock/{ingredient_id}/counts",
    response_model=CountOut,
    tags=["stock"],
    summary="Record a physical count: re-anchor on-hand, measure drift, run the gate.",
)
async def record_count(ingredient_id: int, body: CountIn) -> CountOut:
    return await in_session(
        lambda session: views.count_view(session, ingredient_id=ingredient_id, body=body)
    )


@router.post(
    "/api/stock/{ingredient_id}/deliveries",
    response_model=DeliveryOut,
    tags=["stock"],
    summary="A delivery that came with no order (walk-in). Opens a batch.",
)
async def record_delivery(ingredient_id: int, body: DeliveryIn) -> DeliveryOut:
    return await in_session(
        lambda session: views.delivery_view(session, ingredient_id=ingredient_id, body=body)
    )


@router.post(
    "/api/stock/{ingredient_id}/write-offs",
    response_model=WriteOffOut,
    tags=["stock"],
    summary="Write stock off (went off, spilled, staff, other), oldest expiry first.",
)
async def record_write_off(ingredient_id: int, body: WriteOffIn) -> WriteOffOut:
    return await in_session(
        lambda session: views.write_off_view(session, ingredient_id=ingredient_id, body=body)
    )


@router.post(
    "/api/stock/{ingredient_id}/checklist",
    response_model=ChecklistOut,
    tags=["stock"],
    summary="A tier C answer: plenty (OK) or running low (LOW). Never a number.",
)
async def record_checklist(ingredient_id: int, body: ChecklistIn) -> ChecklistOut:
    return await in_session(
        lambda session: views.checklist_view(session, ingredient_id=ingredient_id, body=body)
    )


@router.put(
    "/api/stock/{ingredient_id}/par",
    response_model=ParChangeOut,
    tags=["stock"],
    summary="Set 'Reorder at' (par floor). Never touches auto-ordering (invariant 2).",
)
async def set_par(ingredient_id: int, body: ParIn) -> ParChangeOut:
    return await in_session(
        lambda session: views.par_view(session, ingredient_id=ingredient_id, body=body)
    )


@router.post(
    "/api/ingredients/{ingredient_id}/tier",
    response_model=TierOut,
    tags=["stock"],
    summary="Move an ingredient between tiers. Up to A only on its count history.",
)
async def change_tier(ingredient_id: int, body: TierIn) -> TierOut:
    return await in_session(
        lambda session: views.tier_view(session, ingredient_id=ingredient_id, body=body)
    )


# --------------------------------------------------------------------------
# orders
# --------------------------------------------------------------------------


def _statuses(raw: str | None) -> tuple[POStatus, ...] | None:
    if raw is None or not raw.strip():
        return None
    out: list[POStatus] = []
    for part in raw.split(","):
        token = part.strip().upper()
        try:
            out.append(POStatus[token])
        except KeyError:
            raise HTTPException(
                status_code=422,
                detail=f"{part!r}: expected one of {', '.join(s.name for s in POStatus)}",
            ) from None
    return tuple(out)


@router.get(
    "/api/orders",
    response_model=OrdersListResponse,
    tags=["orders"],
    summary="Stored purchase orders, newest first, with what the web may do to each.",
)
async def list_orders(
    status: Annotated[str | None, Query(description="Comma-separated POStatus names.")] = None,
    supplier_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> OrdersListResponse:
    statuses = _statuses(status)
    return await in_session(
        lambda session: views.orders_view(
            session, statuses=statuses, supplier_id=supplier_id, limit=limit
        )
    )


@router.get(
    "/api/orders/shop-runs",
    response_model=ShopRunsResponse,
    tags=["orders"],
    summary="Every shop run: logged here, and found in the expenses log.",
)
async def shop_runs(
    months: Annotated[int, Query(ge=1, le=36)] = 8,
) -> ShopRunsResponse:
    return await in_session(lambda session: views.shop_runs_view(session, months=months))


@router.post(
    "/api/orders/shop-run",
    response_model=ShopRunOut,
    tags=["orders"],
    summary="Log stock bought at a shop: a batch and a routing per line. Not an order.",
)
async def log_shop_run(body: ShopRunIn) -> ShopRunOut:
    return await in_session(lambda session: views.shop_run_view(session, body=body))


@router.post(
    "/api/orders/{po_id}/cancel",
    response_model=PurchaseOrderOut,
    tags=["orders"],
    summary="Cancel an order not yet sent. Signed.",
)
async def cancel_order(po_id: int, body: CancelIn) -> PurchaseOrderOut:
    return await in_session(
        lambda session: views.cancel_order_view(
            session, po_id=po_id, cancelled_by=body.cancelled_by, reason=body.reason
        )
    )


@router.post(
    "/api/orders/{po_id}/mark-sent",
    response_model=PurchaseOrderOut,
    tags=["orders"],
    summary="Record that an order confirmed in Telegram went to the supplier.",
)
async def mark_sent(po_id: int, body: MarkSentIn) -> PurchaseOrderOut:
    return await in_session(
        lambda session: views.mark_sent_view(session, po_id=po_id, sent_by=body.sent_by)
    )


@router.post(
    "/api/orders/{po_id}/receive",
    response_model=ReceiveOut,
    tags=["orders"],
    summary="Receive lines of a confirmed order into stock, with their use-by dates.",
)
async def receive_order(po_id: int, body: ReceiveIn) -> ReceiveOut:
    return await in_session(lambda session: views.receive_view(session, po_id=po_id, body=body))


# --------------------------------------------------------------------------
# suppliers
# --------------------------------------------------------------------------


@router.post(
    "/api/suppliers",
    response_model=SupplierWriteOut,
    tags=["suppliers"],
    summary="Add a supplier. Its terms are placeholders until confirmed with them.",
)
async def create_supplier(body: SupplierCreateIn) -> SupplierWriteOut:
    return await in_session(lambda session: views.supplier_create_view(session, body=body))


@router.patch(
    "/api/suppliers/{supplier_id}",
    response_model=SupplierWriteOut,
    tags=["suppliers"],
    summary="Edit a supplier's profile. Terms are confirmed all together at /confirm.",
)
async def update_supplier(supplier_id: int, body: SupplierPatchIn) -> SupplierWriteOut:
    return await in_session(
        lambda session: views.supplier_patch_view(session, supplier_id=supplier_id, body=body)
    )


@router.post(
    "/api/suppliers/{supplier_id}/archive",
    response_model=SupplierWriteOut,
    tags=["suppliers"],
    summary="'Delete' a supplier: archive it. Refused while an order is open.",
)
async def archive_supplier(supplier_id: int, body: SupplierArchiveIn) -> SupplierWriteOut:
    return await in_session(
        lambda session: views.supplier_archive_view(session, supplier_id=supplier_id, body=body)
    )


@router.get(
    "/api/suppliers/{supplier_id}/products",
    response_model=SupplierProductsResponse,
    tags=["suppliers"],
    summary="What we buy from this supplier, per unit, against the other suppliers.",
)
async def supplier_products(supplier_id: int) -> SupplierProductsResponse:
    return await in_session(
        lambda session: views.supplier_products_view(session, supplier_id=supplier_id)
    )


@router.post(
    "/api/suppliers/{supplier_id}/products",
    response_model=ProductWriteOut,
    tags=["suppliers"],
    summary="Link an ingredient to this supplier.",
)
async def link_product(supplier_id: int, body: ProductLinkIn) -> ProductWriteOut:
    return await in_session(
        lambda session: views.product_link_view(session, supplier_id=supplier_id, body=body)
    )


@router.patch(
    "/api/supplier-products/{product_id}",
    response_model=ProductWriteOut,
    tags=["suppliers"],
    summary="Edit a link. On the preferred link, a price or pack change re-costs the menu.",
)
async def edit_product(product_id: int, body: ProductPatchIn) -> ProductWriteOut:
    return await in_session(
        lambda session: views.product_patch_view(session, product_id=product_id, body=body)
    )


@router.post(
    "/api/supplier-products/{product_id}/prefer",
    response_model=ProductWriteOut,
    tags=["suppliers"],
    summary="Make this link the one recipe costs use (the star).",
)
async def prefer_product(product_id: int, body: ProductActorIn) -> ProductWriteOut:
    return await in_session(
        lambda session: views.product_prefer_view(session, product_id=product_id, body=body)
    )


@router.post(
    "/api/supplier-products/{product_id}/archive",
    response_model=ProductWriteOut,
    tags=["suppliers"],
    summary="Unlink: archive the link. Refused while an open order uses it.",
)
async def archive_product(product_id: int, body: ProductArchiveIn) -> ProductWriteOut:
    return await in_session(
        lambda session: views.product_archive_view(session, product_id=product_id, body=body)
    )
