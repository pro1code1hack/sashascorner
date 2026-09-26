"""Admin endpoints for the website menu's presentation (ADMIN.md, "Website menu").

Only the ``site_menu_*_meta`` overlay is written. Prices, names and sizes belong to
the ops back office and are read-only here. Every write is validated against the
CURRENT source (a slug or key the public menu could not show is a 404/422) and
audited in the same transaction.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from sashasite.auth import audit, require_admin_session
from sashasite.db import (
    SiteMenuCategoryMeta,
    SiteMenuItemMeta,
    session_scope,
    utcnow,
)
from sashasite.drift import menu_drift
from sashasite.menu import load_board
from sashasite.menu_source import (
    MenuSnapshot,
    SourceCategory,
    SourceItem,
    build_snapshot,
    invalidate,
)
from sashasite.schemas import SizeOut

router = APIRouter(prefix="/api/admin/menu", dependencies=[Depends(require_admin_session)])

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


class AdminMenuCategoryOut(BaseModel):
    name: str
    slug: str
    #: ``DRINKS`` | ``FOOD`` | ``OTHER`` from ops; null for the board or no ops row.
    kind: str | None
    blurb: str | None
    hidden: bool
    position: int | None
    items: list[AdminMenuItemOut]


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
    warnings: list[str]
    categories: list[AdminMenuCategoryOut]
    unassigned: list[AdminMenuItemOut]
    drift: AdminDriftOut


class CategoryPutIn(BaseModel):
    #: null clears the website's override (the board's blurb, or none, shows).
    blurb: str | None = Field(default=None, max_length=300)
    hidden: bool | None = None

    @field_validator("blurb", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class ItemPutIn(BaseModel):
    #: "" or null: no website description.
    description: str | None = Field(default=None, max_length=600)
    signature: bool | None = None
    hidden: bool | None = None
    use_ops_note: bool | None = None

    @field_validator("description", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip() or None
        return v


class OrderIn(BaseModel):
    #: Every listed slug takes that position; categories left out follow, in the
    #: source's own order. Omit to leave category order untouched.
    categories: list[str] | None = None
    #: slug -> item keys, same rule within each category.
    items: dict[str, list[str]] = Field(default_factory=dict)


# --- rendering ----------------------------------------------------------------------


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
        warnings=snap.warnings,
        categories=[_category_out(c) for c in snap.categories],
        unassigned=[_item_out(i, False) for i in snap.unassigned],
        drift=_drift(),
    )


# --- writes -------------------------------------------------------------------------


def _writable_snapshot() -> MenuSnapshot:
    snap = build_snapshot()
    if not snap.overlay_ready:
        raise HTTPException(
            503, detail="Website menu settings are not set up yet: run the database migration."
        )
    return snap


def _category_row(s: Session, c: SourceCategory) -> SiteMenuCategoryMeta:
    row = s.get(SiteMenuCategoryMeta, c.name)
    if row is None:
        # Start from what is showing now, so a partial edit changes only its fields.
        row = SiteMenuCategoryMeta(name=c.name, hidden=c.hidden, position=c.position)
        s.add(row)
    return row


def _item_row(s: Session, i: SourceItem) -> SiteMenuItemMeta:
    row = s.get(SiteMenuItemMeta, i.key)
    if row is None:
        # Seed from the effective values (e.g. the board's description), or a
        # first edit of one field would silently blank the others.
        row = SiteMenuItemMeta(
            item_name=i.key,
            description=i.web.description,
            signature=i.web.signature,
            hidden=i.web.hidden,
            position=i.web.position,
            use_ops_note=i.web.use_ops_note,
        )
        s.add(row)
    return row


@router.get("", response_model=AdminMenuOut)
def get_menu() -> AdminMenuOut:
    return admin_menu()


@router.put("/categories/{slug}", response_model=AdminMenuCategoryOut)
def put_category(slug: str, body: CategoryPutIn, request: Request) -> AdminMenuCategoryOut:
    snap = _writable_snapshot()
    cat = snap.category_by_slug(slug)
    if cat is None:
        raise HTTPException(404, detail=f"No category {slug!r} on the {snap.source} menu.")
    changed = body.model_dump(include=body.model_fields_set)
    with session_scope(immediate=True) as s:
        row = _category_row(s, cat)
        if "blurb" in body.model_fields_set:
            row.blurb = body.blurb
        if "hidden" in body.model_fields_set and body.hidden is not None:
            row.hidden = body.hidden
        row.updated_at = utcnow()
        audit(
            "menu.category",
            {"name": cat.name, "slug": slug, **changed},
            request=request,
            session=s,
        )
    invalidate()
    out = build_snapshot().category_by_slug(slug)
    assert out is not None
    return _category_out(out)


@router.put("/items/{key:path}", response_model=AdminMenuItemOut)
def put_item(key: str, body: ItemPutIn, request: Request) -> AdminMenuItemOut:
    snap = _writable_snapshot()
    item = next((i for i in snap.all_items() if i.key == key), None)
    if item is None:
        raise HTTPException(404, detail=f"No item {key!r} on the {snap.source} menu.")
    fields = body.model_fields_set
    with session_scope(immediate=True) as s:
        row = _item_row(s, item)
        if "description" in fields:
            row.description = body.description
        if body.signature is not None:
            row.signature = body.signature
        if body.hidden is not None:
            row.hidden = body.hidden
        if body.use_ops_note is not None:
            row.use_ops_note = body.use_ops_note
        row.updated_at = utcnow()
        audit(
            "menu.item",
            {"key": key, **body.model_dump(include=fields)},
            request=request,
            session=s,
        )
    invalidate()
    fresh = build_snapshot()
    for c in fresh.categories:
        for i in c.items:
            if i.key == key:
                return _item_out(i, not c.hidden)
    for i in fresh.unassigned:
        if i.key == key:
            return _item_out(i, False)
    raise HTTPException(404, detail=f"No item {key!r}.")  # vanished mid-request


def _dupes(xs: list[str]) -> list[str]:
    return sorted({x for x in xs if xs.count(x) > 1})


@router.post("/order", response_model=AdminMenuOut)
def post_order(body: OrderIn, request: Request) -> AdminMenuOut:
    snap = _writable_snapshot()
    errors: list[str] = []
    if body.categories is not None:
        unknown = [x for x in body.categories if snap.category_by_slug(x) is None]
        if unknown:
            errors.append(f"unknown category slug(s): {unknown}")
        if d := _dupes(body.categories):
            errors.append(f"duplicate category slug(s): {d}")
    for slug, keys in body.items.items():
        cat = snap.category_by_slug(slug)
        if cat is None:
            errors.append(f"unknown category slug {slug!r} in items")
            continue
        have = {i.key for i in cat.items}
        if missing := [k for k in keys if k not in have]:
            errors.append(f"{slug}: not in this category: {missing}")
        if d := _dupes(keys):
            errors.append(f"{slug}: duplicate item key(s): {d}")
    if errors:
        raise HTTPException(422, detail=errors)

    # All or nothing: one IMMEDIATE transaction for every position.
    with session_scope(immediate=True) as s:
        now = utcnow()
        if body.categories is not None:
            pos = {slug: n for n, slug in enumerate(body.categories)}
            for c in snap.categories:
                p = pos.get(c.slug)
                row = s.get(SiteMenuCategoryMeta, c.name)
                if row is None and p is None:
                    continue
                row = row or _category_row(s, c)
                row.position, row.updated_at = p, now
        for slug, keys in body.items.items():
            cat = snap.category_by_slug(slug)
            assert cat is not None
            ipos = {k: n for n, k in enumerate(keys)}
            for i in cat.items:
                p = ipos.get(i.key)
                irow = s.get(SiteMenuItemMeta, i.key)
                if irow is None and p is None:
                    continue
                irow = irow or _item_row(s, i)
                irow.position, irow.updated_at = p, now
        audit(
            "menu.order",
            {"categories": body.categories, "items": body.items},
            request=request,
            session=s,
        )
    invalidate()
    return admin_menu()
