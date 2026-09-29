"""Admin view of the website menu (ADMIN.md, "Website menu") -- read-only since
2026-09-29 (DECISIONS.md 29, "one menu everywhere").

The presentation the public menu is built from is the back office's Order online
catalogue (``shop_category`` / ``shop_product``, read by menu_source.py). It is
edited in the back office (Menu items), so the three write routes that used to update the
site's own ``site_menu_*_meta`` rows answer **410** and say where to go. ``GET``
still works: the back office's Website / Today reads ``menu.warnings`` from the
summary, and ``sashasite doctor`` reads the same snapshot.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sashasite.auth import require_admin_session
from sashasite.config import get_settings
from sashasite.drift import menu_drift
from sashasite.menu import load_board
from sashasite.menu_source import (
    MenuSnapshot,
    OverlaySource,
    SourceCategory,
    SourceItem,
    build_snapshot,
)
from sashasite.schemas import SizeOut

router = APIRouter(prefix="/api/admin/menu", dependencies=[Depends(require_admin_session)])

EDIT_ELSEWHERE = "Edit the menu in Café Ops › Menu items"  # noqa: RUF001

# --- shapes --------------------------------------------------------------------------


class AdminSeasonOut(BaseModel):
    name: str
    starts_on: dt.date | None
    ends_on: dt.date | None
    recurring_annually: bool
    #: False means the item is hidden from the public menu until its season opens.
    in_season: bool


class AdminItemWebOut(BaseModel):
    description: str | None
    signature: bool
    hidden: bool
    position: int | None
    use_ops_note: bool


class AdminMenuItemOut(BaseModel):
    key: str
    id: str
    name: str
    sizes: list[SizeOut]
    seasonal: AdminSeasonOut | None
    #: ops ``menu_item.note``: shown here, public only with ``web.use_ops_note``.
    ops_note: str | None
    has_photo: bool
    #: Would the public menu show it right now (not hidden, in season, category shown)?
    public: bool
    web: AdminItemWebOut
    #: The ``menu_item.id`` the back office edits this under (ops source only).
    ops_menu_item_id: int | None = None
    #: The back-office page to edit it on; null when it cannot be computed.
    edit_href: str | None = None


class AdminMenuCategoryOut(BaseModel):
    name: str
    slug: str
    #: ``DRINKS`` | ``FOOD`` | ``OTHER`` from ops; null for the board or no ops row.
    kind: str | None
    blurb: str | None
    hidden: bool
    position: int | None
    items: list[AdminMenuItemOut]
    #: The shop's display name when it differs from the ops name.
    display_name: str | None = None
    #: Where categories are edited (the Categories drawer on Menu items).
    edit_href: str | None = None


class DriftMismatchOut(BaseModel):
    board_name: str
    ops_name: str
    size: str
    board_pence: int | None
    ops_pence: int | None


class AdminDriftOut(BaseModel):
    board_items: int
    ops_items: int
    matched: int
    price_mismatches: list[DriftMismatchOut]
    board_only: list[str]
    ops_only: list[str]
    error: str | None = None


class AdminMenuOut(BaseModel):
    source: Literal["ops", "board"]
    #: ``shop``: the back office's catalogue (editable there); ``site``: the site's
    #: legacy rows (read-only, the ops migration is missing); ``none``.
    overlay: OverlaySource
    #: Where the menu is edited now.
    edit_href: str
    warnings: list[str]
    categories: list[AdminMenuCategoryOut]
    unassigned: list[AdminMenuItemOut]
    drift: AdminDriftOut


# --- rendering ----------------------------------------------------------------------


def ops_href(path: str) -> str:
    """A back-office page: ``ops_href("/menu/12")`` -> ``{SITE_OPS_URL}/#/menu/12``."""
    return f"{get_settings().ops_url.rstrip('/')}/#{path}"


def _item_edit_href(i: SourceItem) -> str | None:
    return ops_href(f"/menu/{i.ops_ids[0]}") if i.ops_ids else None


CATEGORIES_HREF = "/menu?view=list"


def _item_out(i: SourceItem, category_shown: bool) -> AdminMenuItemOut:
    s = i.season
    return AdminMenuItemOut(
        key=i.key,
        id=i.id,
        name=i.name,
        sizes=i.sizes,
        seasonal=AdminSeasonOut(
            name=s.name,
            starts_on=s.starts_on,
            ends_on=s.ends_on,
            recurring_annually=s.recurring_annually,
            in_season=s.in_season,
        )
        if s
        else None,
        ops_note=i.ops_note,
        has_photo=i.has_photo,
        public=category_shown and i.public,
        web=AdminItemWebOut(
            description=i.web.description,
            signature=i.web.signature,
            hidden=i.web.hidden,
            position=i.web.position,
            use_ops_note=i.web.use_ops_note,
        ),
        ops_menu_item_id=i.ops_ids[0] if i.ops_ids else None,
        edit_href=_item_edit_href(i),
    )


def _category_out(c: SourceCategory) -> AdminMenuCategoryOut:
    return AdminMenuCategoryOut(
        name=c.name,
        slug=c.slug,
        kind=c.kind,
        blurb=c.blurb,
        hidden=c.hidden,
        position=c.position,
        items=[_item_out(i, not c.hidden) for i in c.items],
        display_name=c.display_name,
        edit_href=ops_href(CATEGORIES_HREF),
    )


def _drift() -> AdminDriftOut:
    try:
        rep = menu_drift(load_board())
    except Exception as exc:
        return AdminDriftOut(
            board_items=0,
            ops_items=0,
            matched=0,
            price_mismatches=[],
            board_only=[],
            ops_only=[],
            error=f"{type(exc).__name__}: {exc}",
        )
    return AdminDriftOut(
        board_items=rep.board_items,
        ops_items=rep.ops_items,
        matched=rep.matched,
        price_mismatches=[
            DriftMismatchOut(board_name=b, ops_name=o, size=sz, board_pence=bp, ops_pence=op)
            for b, o, sz, bp, op in rep.price_mismatches
        ],
        board_only=rep.board_only,
        ops_only=rep.ops_only,
    )


def admin_menu(snap: MenuSnapshot | None = None) -> AdminMenuOut:
    snap = snap or build_snapshot()
    return AdminMenuOut(
        source=snap.source,
        overlay=snap.overlay,
        edit_href=ops_href(CATEGORIES_HREF),
        warnings=snap.warnings,
        categories=[_category_out(c) for c in snap.categories],
        unassigned=[_item_out(i, False) for i in snap.unassigned],
        drift=_drift(),
    )


@router.get("", response_model=AdminMenuOut)
def get_menu() -> AdminMenuOut:
    return admin_menu()


# --- writes: gone (410) -----------------------------------------------------------------


def _gone(where: str | None) -> HTTPException:
    detail = EDIT_ELSEWHERE + (f" ({where})" if where else "")
    return HTTPException(410, detail=detail)


@router.put("/categories/{slug}")
def put_category(slug: str) -> None:
    """410. Category blurbs, names, visibility and order are the Categories drawer
    on Menu items in the back office (``shop_category``)."""
    raise _gone(ops_href(CATEGORIES_HREF))


@router.put("/items/{key:path}")
def put_item(key: str) -> None:
    """410. An item's description, signature and visibility are the "Online
    ordering" section of its page in the back office's Menu items (``shop_product``)."""
    snap = build_snapshot()
    item = next((i for i in snap.all_items() if i.key == key), None)
    raise _gone(_item_edit_href(item) if item is not None else ops_href("/menu"))


@router.post("/order")
def post_order() -> None:
    """410. Order is ``shop_category.sort_order`` / ``shop_product.sort_order``,
    set on Menu items in the back office."""
    raise _gone(ops_href(CATEGORIES_HREF))
