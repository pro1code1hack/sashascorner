"""Routes for the menu area of the back-office redesign: Recipes, Menu items, Ingredients.

Thin on purpose, like `api/routers.py`: parse, hand the work to a view on a worker
thread (`runtime.in_session`), return. Every write is a `.../preview` (writes nothing)
and `.../apply` (applies from today) pair, or a plain edit of something that is not
recipe or price (a name, a category, a note, on/off).

Refusals come back as `{"detail": str}`: 404 for a missing thing, 409 for "the world
moved" (a stale recipe version, a retroactive date, an ingredient still in use, a
recipe item's lines edited here), 422 for a request that cannot be honoured as asked
(an incompatible unit, a non-image upload, a refused operation).

`open_router` serves uploaded photos at `/media/<sha256>.<ext>` WITHOUT auth, because
an `<img>` cannot send the API key. Production serves the same directory from Caddy;
this route is the fallback for development. It must be registered by the integrator
(`app.include_router(areas.menu.open_router)`).
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from cafeops.api.areas import menu_views as views
from cafeops.api.areas.menu_schemas import (
    ActorIn,
    CategoryIn,
    ChangesetAppliedOut,
    ChangesetApplyIn,
    ChangesetIn,
    ChangesetPreviewOut,
    IngredientAllergensIn,
    IngredientAllergensOut,
    IngredientCreateIn,
    IngredientDetailOut,
    IngredientMetaIn,
    IngredientPhotoOut,
    IngredientPriceAppliedOut,
    IngredientPriceApplyIn,
    IngredientPricePreviewIn,
    IngredientPricePreviewOut,
    IngredientsResponse,
    IngredientWriteOut,
    ItemSalesOut,
    LinesAppliedOut,
    LinesPreviewOut,
    ManualLinesApplyIn,
    ManualLinesIn,
    MenuCategoryOut,
    MenuGroupIn,
    MenuItemCreateIn,
    MenuItemDetailOut,
    MenuItemsResponse,
    MenuPricesApplyIn,
    MenuPricesIn,
    MenuSizeIn,
    MenuWriteOut,
    PhotoOut,
    PrepIn,
    PrepOut,
    PricesAppliedOut,
    PricesPreviewOut,
    ProposalConfirmIn,
    ProposalPreviewIn,
    ProposalPreviewOut,
    RecipeEditorOut,
    RecipeHistoryOut,
    RecipesRailResponse,
    SeasonOut,
    SwapAppliedOut,
    SwapApplyIn,
    SwapChangeIn,
    SwapOut,
    SwapPreviewOut,
)
from cafeops.api.runtime import in_session
from cafeops.api.schemas import MaterialiseResponse
from cafeops.api.security import ApiAuth
from cafeops.api.uploads import read_bounded_body
from cafeops.services.media_store import media_path

router = APIRouter(dependencies=[ApiAuth], tags=["menu"])

#: Unauthenticated: photos only, by content-addressed name. See the module docstring.
open_router = APIRouter(tags=["menu"])

_MEDIA_NAME = re.compile(r"^[0-9a-f]{64}\.(webp|jpg|png)$")
_MEDIA_TYPES = {"webp": "image/webp", "jpg": "image/jpeg", "png": "image/png"}


# --------------------------------------------------------------------------
# Recipes
# --------------------------------------------------------------------------


@router.get("/api/recipes", response_model=RecipesRailResponse, summary="The Recipes rail")
async def recipes_rail() -> RecipesRailResponse:
    return await in_session(views.recipes_rail_view)


@router.get(
    "/api/templates/{template_id}/editor",
    response_model=RecipeEditorOut,
    summary="Everything the recipe editor shows, with the version token to send back",
)
async def recipe_editor(template_id: int) -> RecipeEditorOut:
    return await in_session(lambda s: views.recipe_editor_view(s, template_id))


@router.get(
    "/api/templates/{template_id}/history",
    response_model=RecipeHistoryOut,
    summary="Who changed this recipe, when, and what",
)
async def recipe_history(template_id: int) -> RecipeHistoryOut:
    return await in_session(lambda s: views.recipe_history_view(s, template_id))


@router.post(
    "/api/templates/{template_id}/changeset/preview",
    response_model=ChangesetPreviewOut,
    summary="What a set of recipe edits would do. WRITES NOTHING.",
)
async def changeset_preview(template_id: int, body: ChangesetIn) -> ChangesetPreviewOut:
    return await in_session(lambda s: views.changeset_preview_view(s, template_id, body))


@router.post(
    "/api/templates/{template_id}/changeset/apply",
    response_model=ChangesetAppliedOut,
    summary="Apply a set of recipe edits from today, all or nothing.",
)
async def changeset_apply(template_id: int, body: ChangesetApplyIn) -> ChangesetAppliedOut:
    return await in_session(lambda s: views.changeset_apply_view(s, template_id, body))


@router.get("/api/seasons", response_model=tuple[SeasonOut, ...], summary="Seasons")
async def seasons() -> tuple[SeasonOut, ...]:
    return await in_session(views.seasons_view)


@router.get("/api/swaps", response_model=tuple[SwapOut, ...], summary="Swaps (modifiers)")
async def swaps() -> tuple[SwapOut, ...]:
    return await in_session(views.swaps_view)


@router.post(
    "/api/swaps/{modifier_id}/preview",
    response_model=SwapPreviewOut,
    summary="What editing a swap would do. WRITES NOTHING.",
)
async def swap_preview(modifier_id: int, body: SwapChangeIn) -> SwapPreviewOut:
    return await in_session(lambda s: views.swap_preview_view(s, modifier_id, body))


@router.post(
    "/api/swaps/{modifier_id}/apply",
    response_model=SwapAppliedOut,
    summary="Apply a swap edit from today (a new modifier_version).",
)
async def swap_apply(modifier_id: int, body: SwapApplyIn) -> SwapAppliedOut:
    return await in_session(lambda s: views.swap_apply_view(s, modifier_id, body))


@router.post(
    "/api/proposals/{proposal_id}/preview",
    response_model=ProposalPreviewOut,
    summary="What confirming a detected recipe would do. WRITES NOTHING (rolled back).",
)
async def proposal_preview(proposal_id: str, body: ProposalPreviewIn) -> ProposalPreviewOut:
    return await in_session(lambda s: views.proposal_preview_view(s, proposal_id, body))


@router.post(
    "/api/proposals/{proposal_id}/confirm",
    response_model=MaterialiseResponse,
    summary="Confirm a detected recipe BY ID (a name is refused).",
)
async def proposal_confirm(proposal_id: str, body: ProposalConfirmIn) -> MaterialiseResponse:
    return await in_session(lambda s: views.proposal_confirm_view(s, proposal_id, body))


# --------------------------------------------------------------------------
# Menu items
# --------------------------------------------------------------------------


@router.get("/api/menu-items", response_model=MenuItemsResponse, summary="Products, grouped")
async def menu_items() -> MenuItemsResponse:
    return await in_session(views.menu_items_view)


@router.post("/api/menu-items", response_model=MenuWriteOut, summary="Add a one-off product")
async def menu_create(body: MenuItemCreateIn) -> MenuWriteOut:
    return await in_session(lambda s: views.menu_create_view(s, body))


@router.post(
    "/api/menu-items/prices/preview",
    response_model=PricesPreviewOut,
    summary="What new sell prices would do. WRITES NOTHING.",
)
async def prices_preview(body: MenuPricesIn) -> PricesPreviewOut:
    return await in_session(lambda s: views.prices_preview_view(s, body))


@router.post(
    "/api/menu-items/prices/apply",
    response_model=PricesAppliedOut,
    summary="Set sell prices from today (dated rows).",
)
async def prices_apply(body: MenuPricesApplyIn) -> PricesAppliedOut:
    return await in_session(lambda s: views.prices_apply_view(s, body))


@router.post("/api/menu-categories", response_model=MenuCategoryOut, summary="Add a category")
async def category_create(body: CategoryIn) -> MenuCategoryOut:
    return await in_session(lambda s: views.category_create_view(s, body.name, body.kind))


@router.get(
    "/api/menu-items/{menu_item_id}",
    response_model=MenuItemDetailOut,
    summary="One size with its lines, prices and history",
)
async def menu_item_detail(menu_item_id: int) -> MenuItemDetailOut:
    return await in_session(lambda s: views.menu_item_detail_view(s, menu_item_id))


@router.get(
    "/api/menu-items/{menu_item_id}/sales",
    response_model=ItemSalesOut,
    summary="Till lines matched to this product (every size) or this size. Read-only.",
)
async def menu_item_sales(
    menu_item_id: int,
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    page_size: Annotated[int, Query(ge=5, le=200)] = 25,
    all_sizes: bool = True,
) -> ItemSalesOut:
    return await in_session(
        lambda s: views.menu_item_sales_view(
            s, menu_item_id, page=page, page_size=page_size, all_sizes=all_sizes
        )
    )


@router.post(
    "/api/menu-items/{menu_item_id}/prep",
    response_model=PrepOut,
    summary="Set or clear time to make per size (labour), recosting those sizes.",
)
async def menu_prep(menu_item_id: int, body: PrepIn) -> PrepOut:
    return await in_session(lambda s: views.menu_prep_view(s, menu_item_id, body))


@router.post(
    "/api/menu-items/{menu_item_id}/group",
    response_model=MenuWriteOut,
    summary="Name, category, note, on/off -- for every size of the product",
)
async def menu_group(menu_item_id: int, body: MenuGroupIn) -> MenuWriteOut:
    return await in_session(lambda s: views.menu_group_view(s, menu_item_id, body))


@router.post(
    "/api/menu-items/{menu_item_id}/sizes",
    response_model=MenuWriteOut,
    summary="Add (or bring back) a size of a one-off product",
)
async def menu_add_size(menu_item_id: int, body: MenuSizeIn) -> MenuWriteOut:
    return await in_session(lambda s: views.menu_add_size_view(s, menu_item_id, body))


@router.post(
    "/api/menu-items/{menu_item_id}/remove-size",
    response_model=MenuWriteOut,
    summary="Take this size off the menu (never deleted)",
)
async def menu_remove_size(menu_item_id: int, body: ActorIn) -> MenuWriteOut:
    return await in_session(lambda s: views.menu_remove_size_view(s, menu_item_id, body.actor))


@router.post(
    "/api/menu-items/{menu_item_id}/duplicate",
    response_model=MenuWriteOut,
    summary="Copy the product as a new one-off item",
)
async def menu_duplicate(menu_item_id: int, body: ActorIn) -> MenuWriteOut:
    return await in_session(lambda s: views.menu_duplicate_view(s, menu_item_id, body.actor))


@router.post(
    "/api/menu-items/{menu_item_id}/lines/preview",
    response_model=LinesPreviewOut,
    summary="What a one-off recipe edit would do. WRITES NOTHING.",
)
async def lines_preview(menu_item_id: int, body: ManualLinesIn) -> LinesPreviewOut:
    return await in_session(lambda s: views.lines_preview_view(s, menu_item_id, body))


@router.post(
    "/api/menu-items/{menu_item_id}/lines/apply",
    response_model=LinesAppliedOut,
    summary="Replace a one-off recipe from today.",
)
async def lines_apply(menu_item_id: int, body: ManualLinesApplyIn) -> LinesAppliedOut:
    return await in_session(lambda s: views.lines_apply_view(s, menu_item_id, body))


@router.post(
    "/api/menu-items/{menu_item_id}/photo",
    response_model=PhotoOut,
    summary="Upload the product photo (raw body, <= 2 MB, PNG/JPEG/WebP by content)",
)
async def photo_upload(
    menu_item_id: int,
    request: Request,
    x_operator: Annotated[str | None, Header()] = None,
) -> PhotoOut:
    data = await read_bounded_body(request)
    return await in_session(lambda s: views.photo_upload_view(s, menu_item_id, data, x_operator))


@router.post(
    "/api/menu-items/{menu_item_id}/photo/clear",
    response_model=PhotoOut,
    summary="Remove the photo from the product (the file is kept)",
)
async def photo_clear(menu_item_id: int) -> PhotoOut:
    return await in_session(lambda s: views.photo_clear_view(s, menu_item_id))


@open_router.get("/media/{filename}", include_in_schema=False)
async def media(filename: str) -> FileResponse:
    match = _MEDIA_NAME.match(filename)
    if match is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    path = media_path(filename)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return FileResponse(
        path,
        media_type=_MEDIA_TYPES[match.group(1)],
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


# --------------------------------------------------------------------------
# Ingredients
# --------------------------------------------------------------------------


@router.get("/api/ingredients", response_model=IngredientsResponse, summary="Ingredients & prices")
async def ingredients() -> IngredientsResponse:
    return await in_session(views.ingredients_view)


@router.post("/api/ingredients", response_model=IngredientWriteOut, summary="Add an ingredient")
async def ingredient_create(body: IngredientCreateIn) -> IngredientWriteOut:
    return await in_session(lambda s: views.ingredient_create_view(s, body))


@router.get(
    "/api/ingredients/{ingredient_id}",
    response_model=IngredientDetailOut,
    summary="One ingredient: offers, where used, price history",
)
async def ingredient_detail(ingredient_id: int) -> IngredientDetailOut:
    return await in_session(lambda s: views.ingredient_detail_view(s, ingredient_id))


@router.post(
    "/api/ingredients/{ingredient_id}/meta",
    response_model=IngredientWriteOut,
    summary="Name, category, notes; unit only while nothing references it",
)
async def ingredient_meta(ingredient_id: int, body: IngredientMetaIn) -> IngredientWriteOut:
    return await in_session(lambda s: views.ingredient_meta_view(s, ingredient_id, body))


@router.post(
    "/api/ingredients/{ingredient_id}/photo",
    response_model=IngredientPhotoOut,
    summary="Upload the ingredient's reference photo (raw body, <= 2 MB, PNG/JPEG/WebP)",
)
async def ingredient_photo_upload(
    ingredient_id: int,
    request: Request,
    x_operator: Annotated[str | None, Header()] = None,
) -> IngredientPhotoOut:
    data = await read_bounded_body(request)
    return await in_session(
        lambda s: views.ingredient_photo_upload_view(s, ingredient_id, data, x_operator)
    )


@router.post(
    "/api/ingredients/{ingredient_id}/photo/clear",
    response_model=IngredientPhotoOut,
    summary="Remove the ingredient's photo (the file is kept)",
)
async def ingredient_photo_clear(ingredient_id: int) -> IngredientPhotoOut:
    return await in_session(lambda s: views.ingredient_photo_clear_view(s, ingredient_id))


@router.post(
    "/api/ingredients/{ingredient_id}/allergens",
    response_model=IngredientAllergensOut,
    summary="Record the UK 14 allergens from the pack ([] = none; null = unknown)",
)
async def ingredient_allergens(
    ingredient_id: int, body: IngredientAllergensIn
) -> IngredientAllergensOut:
    return await in_session(lambda s: views.ingredient_allergens_view(s, ingredient_id, body))


@router.post(
    "/api/ingredients/{ingredient_id}/retire",
    response_model=IngredientWriteOut,
    summary="'Delete' = retire; refused (409) while a live recipe uses it",
)
async def ingredient_retire(ingredient_id: int, body: ActorIn) -> IngredientWriteOut:
    return await in_session(lambda s: views.ingredient_retire_view(s, ingredient_id, body.actor))


@router.post(
    "/api/ingredients/{ingredient_id}/price/preview",
    response_model=IngredientPricePreviewOut,
    summary="What recording this price would do to menu costs. WRITES NOTHING.",
)
async def ingredient_price_preview(
    ingredient_id: int, body: IngredientPricePreviewIn
) -> IngredientPricePreviewOut:
    return await in_session(lambda s: views.ingredient_price_preview_view(s, ingredient_id, body))


@router.post(
    "/api/ingredients/{ingredient_id}/price/apply",
    response_model=IngredientPriceAppliedOut,
    summary="Record the price from today and recost every item using it.",
)
async def ingredient_price_apply(
    ingredient_id: int, body: IngredientPriceApplyIn
) -> IngredientPriceAppliedOut:
    return await in_session(lambda s: views.ingredient_price_apply_view(s, ingredient_id, body))
