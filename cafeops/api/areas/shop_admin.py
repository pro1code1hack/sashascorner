"""Routes for the back office's Online orders area (docs/shop/CONTRACT.md §5).

Behind `ApiAuth` like every other area, under `/api/shop-admin`. Thin on purpose: parse,
hand the work to a view on a worker thread (`runtime.in_session`), return. Refusals come
back as `{"detail": str}`: 404 for a missing thing, 409 when the world moved (a status
transition §3.8 does not allow, a cancel after collection, paying twice), 422 for a
request that cannot be honoured as asked (hours that close before they open, a
non-image upload, an unknown allergen).

Photo uploads take the raw image body (`Content-Type` is a claim; the bytes decide,
see `services/media_store`), exactly like the menu's `/photo` routes; `/photo/clear`
detaches without deleting the file.

Static paths (`orders/…`, `categories/order`, `products/order`, `products/bulk`,
`option-groups/order`, `banners/order`) are declared before their `/{id}` siblings:
FastAPI matches in order, and "order" failing the int conversion would be a 422.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request, status

from cafeops.api.areas import shop_admin_views as views
from cafeops.api.areas.shop_admin_schemas import (
    BannerAdminOut,
    BannerIn,
    BulkOut,
    ByIn,
    CatalogueAdminOut,
    CategoryAdminOut,
    CategoryIn,
    DeletedOut,
    ModifierOut,
    NoteIn,
    OptionGroupAdminOut,
    OptionGroupIn,
    OrderAdminOut,
    OrderIdsIn,
    OrdersPageOut,
    PhotoOut,
    ProductAdminOut,
    ProductByMenuItemOut,
    ProductIn,
    ProductsBulkIn,
    ProductsOrderIn,
    RefundIn,
    ShopInsightsOut,
    ShopSettingsIn,
    ShopSettingsOut,
    StatusIn,
    SummaryOut,
    UpsellAdminOut,
    UpsellIn,
)
from cafeops.api.runtime import in_session
from cafeops.api.security import ApiAuth
from cafeops.services.media_store import MAX_BYTES

router = APIRouter(prefix="/api/shop-admin", tags=["shop-admin"], dependencies=[ApiAuth])

PageQ = Annotated[int, Query(ge=1)]
PageSizeQ = Annotated[int, Query(ge=1, le=200)]
OperatorH = Annotated[str | None, Header()]
LengthH = Annotated[int | None, Header()]


async def _image_body(request: Request, content_length: int | None) -> bytes:
    if content_length is not None and content_length > MAX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="that photo is over 2 MB; resize it (1200px wide is plenty) and try again",
        )
    return await request.body()


# --------------------------------------------------------------------------
# Summary and orders
# --------------------------------------------------------------------------


@router.get("/summary", response_model=SummaryOut, summary="The board's header: counts, next due")
async def summary() -> SummaryOut:
    return await in_session(views.summary_view)


@router.get(
    "/orders",
    response_model=OrdersPageOut,
    summary="Orders: live (placed, not yet collected), today, all, or one status name",
)
async def orders(
    status_: Annotated[
        str,
        Query(
            alias="status",
            max_length=20,
            description="live | today | all | NEW | ACCEPTED | … (a status name)",
        ),
    ] = "live",
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
    q: Annotated[str | None, Query(max_length=80)] = None,
    page: PageQ = 1,
    page_size: PageSizeQ = 50,
) -> OrdersPageOut:
    return await in_session(
        lambda s: views.orders_view(
            s, status=status_, since=from_, until=to, q=q, page=page, page_size=page_size
        )
    )


@router.get("/orders/{order_id}", response_model=OrderAdminOut, summary="One order, whole")
async def order(order_id: int) -> OrderAdminOut:
    return await in_session(lambda s: views.order_view(s, order_id))


@router.post(
    "/orders/{order_id}/status",
    response_model=OrderAdminOut,
    summary="Move an order along (§3.8). COLLECTED writes the sale, stamps, reward. 409 otherwise.",
)
async def order_status(order_id: int, body: StatusIn) -> OrderAdminOut:
    return await in_session(
        lambda s: views.status_view(s, order_id, status=body.status, reason=body.reason, by=body.by)
    )


@router.post("/orders/{order_id}/note", response_model=OrderAdminOut, summary="Staff note")
async def order_note(order_id: int, body: NoteIn) -> OrderAdminOut:
    return await in_session(
        lambda s: views.note_view(s, order_id, staff_note=body.staff_note, by=body.by)
    )


@router.post(
    "/orders/{order_id}/paid",
    response_model=OrderAdminOut,
    summary="A counter order paid before collection (COLLECTED implies paid anyway)",
)
async def order_paid(order_id: int, body: ByIn) -> OrderAdminOut:
    return await in_session(lambda s: views.paid_view(s, order_id, by=body.by))


@router.post(
    "/orders/{order_id}/refund",
    response_model=OrderAdminOut,
    summary="Refund a PAID online order through the payment provider. 409 with the "
    "provider's sentence when it refuses; a counter payment is refunded from the till.",
)
async def order_refund(order_id: int, body: RefundIn) -> OrderAdminOut:
    return await in_session(
        lambda s: views.refund_view(s, order_id, by=body.by, reason=body.reason)
    )


@router.get(
    "/insights",
    response_model=ShopInsightsOut,
    summary="Online-order statistics: last `days` days (or from/to), vs the window before",
)
async def insights(
    days: Annotated[int, Query(ge=1, le=366)] = 30,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
) -> ShopInsightsOut:
    return await in_session(lambda s: views.insights_view(s, since=from_, until=to, days=days))


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


@router.get("/settings", response_model=ShopSettingsOut, summary="Every shop setting")
async def settings_get() -> ShopSettingsOut:
    return await in_session(views.settings_view)


@router.put(
    "/settings",
    response_model=ShopSettingsOut,
    summary="Change any subset; hours and closures are validated and replaced whole",
)
async def settings_put(body: ShopSettingsIn) -> ShopSettingsOut:
    return await in_session(lambda s: views.settings_update_view(s, body))


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------


@router.get(
    "/catalogue",
    response_model=CatalogueAdminOut,
    summary="The shop's catalogue overlay, synced to the ops menu first",
)
async def catalogue() -> CatalogueAdminOut:
    return await in_session(views.catalogue_view)


@router.get("/modifiers", response_model=tuple[ModifierOut, ...], summary="Ops modifiers to link")
async def modifiers() -> tuple[ModifierOut, ...]:
    return await in_session(views.modifiers_view)


# --- categories -------------------------------------------------------------


@router.post("/categories/order", response_model=CatalogueAdminOut, summary="Reorder categories")
async def categories_order(body: OrderIdsIn) -> CatalogueAdminOut:
    return await in_session(lambda s: views.categories_order_view(s, body.ids))


@router.put("/categories/{category_id}", response_model=CategoryAdminOut, summary="Edit a category")
async def category_update(category_id: int, body: CategoryIn) -> CategoryAdminOut:
    return await in_session(lambda s: views.category_update_view(s, category_id, body))


@router.post(
    "/categories/{category_id}/photo",
    response_model=PhotoOut,
    summary="Upload the category tile photo (raw body, <= 2 MB, PNG/JPEG/WebP)",
)
async def category_photo(
    category_id: int,
    request: Request,
    x_operator: OperatorH = None,
    content_length: LengthH = None,
) -> PhotoOut:
    data = await _image_body(request, content_length)
    return await in_session(lambda s: views.category_photo_view(s, category_id, data, x_operator))


@router.post("/categories/{category_id}/photo/clear", response_model=PhotoOut, summary="Remove it")
async def category_photo_clear(category_id: int) -> PhotoOut:
    return await in_session(lambda s: views.category_photo_view(s, category_id, None, None))


# --- products ---------------------------------------------------------------


@router.post(
    "/products/order", response_model=CatalogueAdminOut, summary="Reorder products in a category"
)
async def products_order(body: ProductsOrderIn) -> CatalogueAdminOut:
    return await in_session(lambda s: views.products_order_view(s, body.category_slug, body.ids))


@router.post("/products/bulk", response_model=BulkOut, summary='"86" several at once')
async def products_bulk(body: ProductsBulkIn) -> BulkOut:
    return await in_session(lambda s: views.products_bulk_view(s, body))


@router.get(
    "/products/by-menu-item/{menu_item_id}",
    response_model=ProductByMenuItemOut,
    summary="The shop product behind an ops menu item (synced into being if missing)",
)
async def product_by_menu_item(menu_item_id: int) -> ProductByMenuItemOut:
    return await in_session(lambda s: views.product_by_menu_item_view(s, menu_item_id))


@router.put(
    "/products/by-menu-item/{menu_item_id}",
    response_model=ProductByMenuItemOut,
    summary="Edit the shop product behind an ops menu item (same body as PUT /products/{id})",
)
async def product_update_by_menu_item(menu_item_id: int, body: ProductIn) -> ProductByMenuItemOut:
    return await in_session(lambda s: views.product_update_by_menu_item_view(s, menu_item_id, body))


@router.put("/products/{product_id}", response_model=ProductAdminOut, summary="Edit a product")
async def product_update(product_id: int, body: ProductIn) -> ProductAdminOut:
    return await in_session(lambda s: views.product_update_view(s, product_id, body))


@router.post(
    "/products/{product_id}/photo",
    response_model=PhotoOut,
    summary="Upload the shop photo (overrides the ops menu photo)",
)
async def product_photo(
    product_id: int,
    request: Request,
    x_operator: OperatorH = None,
    content_length: LengthH = None,
) -> PhotoOut:
    data = await _image_body(request, content_length)
    return await in_session(lambda s: views.product_photo_view(s, product_id, data, x_operator))


@router.post(
    "/products/{product_id}/photo/clear",
    response_model=PhotoOut,
    summary="Remove the shop photo (falls back to the ops menu photo)",
)
async def product_photo_clear(product_id: int) -> PhotoOut:
    return await in_session(lambda s: views.product_photo_view(s, product_id, None, None))


# --- option groups ------------------------------------------------------------


@router.post("/option-groups/order", response_model=CatalogueAdminOut, summary="Reorder groups")
async def option_groups_order(body: OrderIdsIn) -> CatalogueAdminOut:
    return await in_session(lambda s: views.option_groups_order_view(s, body.ids))


@router.post("/option-groups", response_model=OptionGroupAdminOut, summary="Create a group")
async def option_group_create(body: OptionGroupIn) -> OptionGroupAdminOut:
    return await in_session(lambda s: views.option_group_create_view(s, body))


@router.put(
    "/option-groups/{group_id}",
    response_model=OptionGroupAdminOut,
    summary="Replace a group; options with `id` are updated, the rest created, unmentioned deleted",
)
async def option_group_update(group_id: int, body: OptionGroupIn) -> OptionGroupAdminOut:
    return await in_session(lambda s: views.option_group_update_view(s, group_id, body))


@router.delete("/option-groups/{group_id}", response_model=DeletedOut, summary="Delete a group")
async def option_group_delete(group_id: int) -> DeletedOut:
    return await in_session(lambda s: views.option_group_delete_view(s, group_id))


@router.post(
    "/options/{option_id}/photo",
    response_model=PhotoOut,
    summary="Upload a PHOTO_TILES option image",
)
async def option_photo(
    option_id: int,
    request: Request,
    x_operator: OperatorH = None,
    content_length: LengthH = None,
) -> PhotoOut:
    data = await _image_body(request, content_length)
    return await in_session(lambda s: views.option_photo_view(s, option_id, data, x_operator))


@router.post("/options/{option_id}/photo/clear", response_model=PhotoOut, summary="Remove it")
async def option_photo_clear(option_id: int) -> PhotoOut:
    return await in_session(lambda s: views.option_photo_view(s, option_id, None, None))


# --- upsells ------------------------------------------------------------------


@router.post("/upsells", response_model=UpsellAdminOut, summary="Create an upsell row")
async def upsell_create(body: UpsellIn) -> UpsellAdminOut:
    return await in_session(lambda s: views.upsell_create_view(s, body))


@router.put("/upsells/{upsell_id}", response_model=UpsellAdminOut, summary="Replace an upsell row")
async def upsell_update(upsell_id: int, body: UpsellIn) -> UpsellAdminOut:
    return await in_session(lambda s: views.upsell_update_view(s, upsell_id, body))


@router.delete("/upsells/{upsell_id}", response_model=DeletedOut, summary="Delete an upsell row")
async def upsell_delete(upsell_id: int) -> DeletedOut:
    return await in_session(lambda s: views.upsell_delete_view(s, upsell_id))


# --- banners ------------------------------------------------------------------


@router.post("/banners/order", response_model=CatalogueAdminOut, summary="Reorder banners")
async def banners_order(body: OrderIdsIn) -> CatalogueAdminOut:
    return await in_session(lambda s: views.banners_order_view(s, body.ids))


@router.post("/banners", response_model=BannerAdminOut, summary="Create a banner")
async def banner_create(body: BannerIn) -> BannerAdminOut:
    return await in_session(lambda s: views.banner_create_view(s, body))


@router.put("/banners/{banner_id}", response_model=BannerAdminOut, summary="Replace a banner")
async def banner_update(banner_id: int, body: BannerIn) -> BannerAdminOut:
    return await in_session(lambda s: views.banner_update_view(s, banner_id, body))


@router.delete("/banners/{banner_id}", response_model=DeletedOut, summary="Delete a banner")
async def banner_delete(banner_id: int) -> DeletedOut:
    return await in_session(lambda s: views.banner_delete_view(s, banner_id))


@router.post(
    "/banners/{banner_id}/photo",
    response_model=PhotoOut,
    summary="Upload the banner image (raw body, <= 2 MB, PNG/JPEG/WebP)",
)
async def banner_photo(
    banner_id: int,
    request: Request,
    x_operator: OperatorH = None,
    content_length: LengthH = None,
) -> PhotoOut:
    data = await _image_body(request, content_length)
    return await in_session(lambda s: views.banner_photo_view(s, banner_id, data, x_operator))


@router.post("/banners/{banner_id}/photo/clear", response_model=PhotoOut, summary="Remove it")
async def banner_photo_clear(banner_id: int) -> PhotoOut:
    return await in_session(lambda s: views.banner_photo_view(s, banner_id, None, None))


__all__ = ["router"]
