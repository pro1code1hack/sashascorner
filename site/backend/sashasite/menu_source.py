"""Where the website's menu comes from, and the presentation layered over it.

Two sources, chosen per request:

* ``ops``   -- the back office's own menu, read READ-ONLY with Core ``text()``
  selects: active ``menu_item`` rows grouped by name within their category, the
  category's order and kind from ``menu_category``, and ``season`` for seasonal
  items. Used once the ``menu_category`` table exists AND at least one active item
  has a non-empty category.
* ``board`` -- ``config/menu_board.toml`` (menu.py), the fallback until then.

Presentation (blurbs, descriptions, signature and hidden flags, order) is an
overlay keyed by name. Since 2026-09-29 (DECISIONS.md 29, "one menu everywhere")
it is the back office's Order online catalogue, read READ-ONLY here:

* ``shop_category`` (``ops_name`` = ``menu_category.name``): display ``name``,
  ``blurb``, ``visible``, ``sort_order``;
* ``shop_product`` (``item_name`` = ``menu_item.name``): ``description``,
  ``visible`` (hidden = not visible), ``featured`` (= signature), ``sort_order``.

The site's own ``site_menu_category_meta`` / ``site_menu_item_meta`` tables are
kept but no longer written; they are read only for ``use_ops_note`` (publish the
ops ``menu_item.note`` as the description when the shop has none) and as the whole
overlay when the shop tables are absent (a checkout without the ops migration) --
which ``warnings`` says. Prices, names and sizes are never edited here; nothing
is edited here at all any more (menu_admin.py answers 410).

Both the public ``/api/menu`` and the admin view are cut from one ``MenuSnapshot``,
so what the admin shows is exactly what the public menu was built from.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, inspect, select, text
from sqlalchemy.orm import Session

from sashasite.config import get_settings
from sashasite.db import SiteMenuCategoryMeta, SiteMenuItemMeta, get_engine, session_scope, utcnow
from sashasite.drift import normalise
from sashasite.menu import (
    BOARD_PATH,
    SIZE_LABEL,
    SIZE_ORDER,
    MenuBoard,
    compile_board,
    load_board,
    slugify,
)
from sashasite.schemas import (
    ExtrasOut,
    MenuCategoryOut,
    MenuItemOut,
    MenuOut,
    SeasonalOut,
    SizeOut,
)

Source = Literal["ops", "board"]
CACHE_SECONDS = 60

# --- snapshot model ---------------------------------------------------------------


@dataclass(frozen=True)
class SeasonInfo:
    name: str
    starts_on: dt.date | None
    ends_on: dt.date | None
    recurring_annually: bool
    in_season: bool


@dataclass
class ItemWeb:
    """The effective website presentation of an item: the overlay row when there
    is one, otherwise the board's values (board mode) or the defaults."""

    description: str | None
    signature: bool
    hidden: bool
    position: int | None
    use_ops_note: bool
    has_row: bool


@dataclass
class SourceItem:
    key: str  # the item name: what the overlay is keyed by
    id: str  # public id (slug of the name, category-suffixed on a collision)
    name: str
    sizes: list[SizeOut]
    web: ItemWeb
    season: SeasonInfo | None = None
    board_note: str | None = None
    ops_note: str | None = None
    has_photo: bool = False
    #: The ``menu_item.id`` of each size (ops mode): where the back office edits it.
    ops_ids: list[int] = field(default_factory=list)
    natural: tuple[Any, ...] = ()

    @property
    def public_description(self) -> str | None:
        desc = (self.web.description or "").strip()
        if desc:
            return desc
        # The ops note may be internal ("check fridge"), so it is published only
        # when the owner has asked for exactly that on this item.
        if self.web.use_ops_note and self.ops_note and self.ops_note.strip():
            return self.ops_note.strip()
        return None

    @property
    def public(self) -> bool:
        return not self.web.hidden and (self.season is None or self.season.in_season)


@dataclass
class SourceCategory:
    name: str
    slug: str
    kind: str | None
    blurb: str | None
    hidden: bool
    position: int | None
    items: list[SourceItem]
    #: False when an ops category has no menu_category row (it sorts last).
    has_ops_row: bool = True
    #: The shop's display name when it differs from the ops name; public only.
    display_name: str | None = None
    natural: tuple[Any, ...] = ()

    @property
    def public_name(self) -> str:
        return self.display_name or self.name


@dataclass
class MenuSnapshot:
    source: Source
    categories: list[SourceCategory]
    #: Ops items with no category: shown in the admin, never public.
    unassigned: list[SourceItem]
    extras: ExtrasOut
    warnings: list[str] = field(default_factory=list)
    overlay_ready: bool = True
    #: Where the presentation came from: the shop tables, the site's legacy rows, none.
    overlay: OverlaySource = "none"
    #: Why the board is being served (board mode only).
    reason: str | None = None

    def category_by_slug(self, slug: str) -> SourceCategory | None:
        return next((c for c in self.categories if c.slug == slug), None)

    def all_items(self) -> Iterable[SourceItem]:
        for c in self.categories:
            yield from c.items
        yield from self.unassigned

    def item_keys(self) -> set[str]:
        return {i.key for i in self.all_items()}


# --- ops shape --------------------------------------------------------------------


@dataclass(frozen=True)
class OpsShape:
    has_menu_category: bool
    item_columns: frozenset[str]
    #: ``shop_category`` AND ``shop_product`` exist (docs/shop/CONTRACT.md 2.3-2.4):
    #: the back office's catalogue is the website's presentation overlay.
    has_shop: bool = False

    @property
    def has_note(self) -> bool:
        return "note" in self.item_columns

    @property
    def has_photo(self) -> bool:
        return "photo_asset_id" in self.item_columns


def ops_shape(conn: Connection) -> OpsShape:
    insp = inspect(conn)
    cols = (
        frozenset(c["name"] for c in insp.get_columns("menu_item"))
        if insp.has_table("menu_item")
        else frozenset()
    )
    return OpsShape(
        has_menu_category=insp.has_table("menu_category"),
        item_columns=cols,
        has_shop=insp.has_table("shop_category") and insp.has_table("shop_product"),
    )


def detect_source(conn: Connection, shape: OpsShape | None = None) -> tuple[Source, str | None]:
    """``("ops", None)`` or ``("board", why)``.

    "Has a category" means a category the back office has actually defined (a
    ``menu_category`` row). The legacy workbook import already left free-text
    categories on some rows ("Teas", "Spring saesonal drinks"); counting those
    would flip the site to a 31-item menu the moment the ops migration lands,
    before anyone has categorised anything.
    """
    shape = shape or ops_shape(conn)
    if not shape.item_columns:
        return "board", "no ops menu_item table"
    if not shape.has_menu_category:
        return "board", "the ops menu has no categories yet (no menu_category table)"
    n: int = conn.execute(
        text(
            "SELECT COUNT(*) FROM menu_item mi "
            "JOIN menu_category mc ON mc.name = TRIM(mi.category) "
            "WHERE mi.active = :t"
        ),
        {"t": True},
    ).scalar_one()
    if not n:
        return "board", "no active ops menu item is in a back-office category yet"
    return "ops", None


def _as_date(v: object) -> dt.date | None:
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])


def in_season(
    starts_on: dt.date | None, ends_on: dt.date | None, recurring: bool, today: dt.date
) -> bool:
    """Inclusive window. A recurring season compares (month, day) only and may
    wrap the year end (e.g. 1 Dec - 15 Jan). A window with a missing bound is open
    on that side."""
    if starts_on is None or ends_on is None:
        if starts_on is not None and not recurring:
            return today >= starts_on
        if ends_on is not None and not recurring:
            return today <= ends_on
        return True
    if not recurring:
        return starts_on <= today <= ends_on
    start, end, now = (
        (starts_on.month, starts_on.day),
        (ends_on.month, ends_on.day),
        (today.month, today.day),
    )
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end  # wraps the new year


def _norm_size(code: str | None) -> str:
    c = (code or "").strip()
    if not c or c.upper() == "ONE":
        return "One"
    return c.upper()


def _ops_sizes(rows: list[Any]) -> list[SizeOut]:
    if len(rows) == 1:
        return [SizeOut(code="One", label=SIZE_LABEL["One"], price_pence=int(rows[0].price_pence))]
    seen: dict[str, SizeOut] = {}
    for r in rows:
        code = _norm_size(r.size_code)
        if code not in seen:
            seen[code] = SizeOut(
                code=code, label=SIZE_LABEL.get(code, code), price_pence=int(r.price_pence)
            )
    return sorted(seen.values(), key=lambda s: SIZE_ORDER.get(s.code, 9))


# --- overlay ---------------------------------------------------------------------


OverlaySource = Literal["shop", "site", "none"]


@dataclass(frozen=True)
class CategoryMeta:
    """One category's presentation, whichever table it came from."""

    name: str
    #: The shop's display name (``shop_category.name``); None = the ops name.
    display_name: str | None
    #: A site-set URL slug (legacy rows only); the shop's slug is the shop's URL,
    #: the website keeps its own stable one (board slug, else the name's).
    slug: str | None
    blurb: str | None
    hidden: bool
    position: int | None


@dataclass(frozen=True)
class ItemMeta:
    key: str
    description: str | None
    signature: bool
    hidden: bool
    position: int | None
    use_ops_note: bool


@dataclass
class Overlay:
    #: Which table answered: the shop catalogue, the site's legacy rows, or none.
    source: OverlaySource
    #: The site's own overlay tables exist (legacy; still read for ``use_ops_note``).
    ready: bool
    categories: dict[str, CategoryMeta]
    items: dict[str, ItemMeta]

    def category(self, name: str) -> CategoryMeta | None:
        """Exact name, else case-insensitive: ``menu_item.category`` is free text
        and may not match ``menu_category.name`` letter for letter."""
        hit = self.categories.get(name)
        if hit is not None:
            return hit
        fold = name.casefold()
        return next((m for n, m in self.categories.items() if n.casefold() == fold), None)


def overlay_ready(conn: Connection) -> bool:
    insp = inspect(conn)
    return insp.has_table("site_menu_category_meta") and insp.has_table("site_menu_item_meta")


def _site_rows(conn: Connection) -> tuple[dict[str, CategoryMeta], dict[str, ItemMeta]]:
    with Session(bind=conn) as s:
        cats = {
            m.name: CategoryMeta(m.name, None, m.slug, m.blurb, bool(m.hidden), m.position)
            for m in s.scalars(select(SiteMenuCategoryMeta))
        }
        items = {
            m.item_name: ItemMeta(
                m.item_name,
                m.description,
                bool(m.signature),
                bool(m.hidden),
                m.position,
                bool(m.use_ops_note),
            )
            for m in s.scalars(select(SiteMenuItemMeta))
        }
        s.expunge_all()
    return cats, items


def _shop_rows(
    conn: Connection, site_items: dict[str, ItemMeta]
) -> tuple[dict[str, CategoryMeta], dict[str, ItemMeta]]:
    """The back office's catalogue as the website's overlay (READ-ONLY Core selects;
    ``sashasite`` never imports ``cafeops``)."""
    cats = {
        r.ops_name: CategoryMeta(
            name=r.ops_name,
            display_name=((r.name or "").strip() or None) if r.name != r.ops_name else None,
            slug=None,
            blurb=(r.blurb or "").strip() or None,
            hidden=not bool(r.visible),
            position=int(r.sort_order) if r.sort_order is not None else None,
        )
        for r in conn.execute(
            text("SELECT ops_name, name, blurb, visible, sort_order FROM shop_category")
        )
    }
    items = {
        r.item_name: ItemMeta(
            key=r.item_name,
            description=(r.description or "").strip() or None,
            signature=bool(r.featured),
            hidden=not bool(r.visible),
            position=int(r.sort_order) if r.sort_order is not None else None,
            # The one thing the shop has no column for: the site's old row keeps it.
            use_ops_note=(m.use_ops_note if (m := site_items.get(r.item_name)) else False),
        )
        for r in conn.execute(
            text("SELECT item_name, description, visible, featured, sort_order FROM shop_product")
        )
    }
    return cats, items


def load_overlay(conn: Connection, shape: OpsShape | None = None, *, ops: bool = True) -> Overlay:
    """The shop tables when they exist and the ops menu is the source; else the
    site's legacy rows (board mode keys by board names, which the shop never has)."""
    shape = shape or ops_shape(conn)
    ready = overlay_ready(conn)
    site_cats, site_items = _site_rows(conn) if ready else ({}, {})
    if shape.has_shop and ops:
        cats, items = _shop_rows(conn, site_items)
        return Overlay("shop", ready, cats, items)
    return Overlay("site" if ready else "none", ready, site_cats, site_items)


def _web(meta: ItemMeta | None, description: str | None, signature: bool) -> ItemWeb:
    if meta is None:
        return ItemWeb(description, signature, False, None, False, has_row=False)
    return ItemWeb(
        meta.description, meta.signature, meta.hidden, meta.position, meta.use_ops_note, True
    )


# --- building the snapshot ----------------------------------------------------------


def _assign_ids(cats: list[SourceCategory], unassigned: list[SourceItem]) -> None:
    """Slug of the name; on a collision, BOTH get a category suffix, so neither
    id depends on which one comes first (same rule as the board)."""
    groups: list[tuple[str, list[SourceItem]]] = [(c.slug, c.items) for c in cats]
    groups.append(("unassigned", unassigned))
    count: dict[str, int] = {}
    for _, items in groups:
        for i in items:
            count[slugify(i.name)] = count.get(slugify(i.name), 0) + 1
    for suffix, items in groups:
        for i in items:
            base = slugify(i.name) or "item"
            i.id = base if count.get(base, 0) == 1 else f"{base}-{suffix}"


def _sort(cats: list[SourceCategory]) -> None:
    def ckey(c: SourceCategory) -> tuple[Any, ...]:
        return (0, c.position, c.natural) if c.position is not None else (1, 0, c.natural)

    def ikey(i: SourceItem) -> tuple[Any, ...]:
        return (0, i.web.position, i.natural) if i.web.position is not None else (1, 0, i.natural)

    cats.sort(key=ckey)
    for c in cats:
        c.items.sort(key=ikey)


def _unique_slugs(cats: list[SourceCategory]) -> None:
    used: set[str] = set()
    for c in cats:
        base = c.slug or "category"
        slug, n = base, 2
        while slug in used:
            slug, n = f"{base}-{n}", n + 1
        c.slug = slug
        used.add(slug)


def _board_snapshot(overlay: Overlay, extras: ExtrasOut) -> MenuSnapshot:
    board = load_board()
    compiled, _ = compile_board(board)
    by_slug = {c.slug: c for c in board.category}
    cats: list[SourceCategory] = []
    for ci, c in enumerate(compiled):
        raw = by_slug[c.slug]
        meta = overlay.categories.get(c.name)
        items = [
            SourceItem(
                key=i.name,
                id=i.id,
                name=i.name,
                sizes=i.sizes,
                web=_web(overlay.items.get(i.name), i.description, i.signature),
                season=SeasonInfo(raw.seasonal, None, None, False, True) if raw.seasonal else None,
                board_note=i.note,
                natural=(ii,),
            )
            for ii, i in enumerate(c.items)
        ]
        cats.append(
            SourceCategory(
                name=c.name,
                slug=c.slug,  # the board's slugs are stable URLs already
                kind=None,
                blurb=meta.blurb if meta is not None and meta.blurb is not None else c.blurb,
                hidden=bool(meta and meta.hidden),
                position=meta.position if meta else None,
                items=items,
                natural=(ci,),
            )
        )
    _sort(cats)
    return MenuSnapshot(
        "board", cats, [], extras, overlay_ready=overlay.ready, overlay=overlay.source
    )


def _board_rank(board: MenuBoard) -> dict[str, int]:
    """Board order by normalised name (aliases included): the order items fall back
    to in ops mode until the owner sets one in the admin."""
    rank: dict[str, int] = {}
    n = 0
    for cat in board.category:
        for item in cat.items:
            for name in (item.n, board.aliases.get(item.n)):
                if name:
                    rank.setdefault(normalise(name), n)
            n += 1
    return rank


def _ops_snapshot(
    conn: Connection,
    shape: OpsShape,
    overlay: Overlay,
    extras: ExtrasOut,
    today: dt.date,
    board: MenuBoard,
) -> MenuSnapshot:
    extra_cols = (", mi.note AS note" if shape.has_note else "") + (
        ", mi.photo_asset_id AS photo_asset_id" if shape.has_photo else ""
    )
    rows = conn.execute(
        text(
            "SELECT mi.id, mi.name, mi.category, mi.size_code, mi.price_pence, "
            "s.name AS season_name, s.starts_on, s.ends_on, s.is_recurring_annually"
            f"{extra_cols} "
            "FROM menu_item mi LEFT JOIN season s ON s.id = mi.season_id "
            "WHERE mi.active = :t ORDER BY mi.id"
        ),
        {"t": True},
    ).all()
    ops_cats = {
        r.name: r
        for r in conn.execute(text("SELECT name, kind, sort_order FROM menu_category")).all()
    }
    ops_cats_fold = {n.casefold(): r for n, r in ops_cats.items()}

    grouped: dict[tuple[str, str], list[Any]] = {}
    for r in rows:
        cat = (r.category or "").strip()
        grouped.setdefault((cat, r.name), []).append(r)

    # What the printed board says about an item beyond its price -- the customer
    # note ("+ £1 chicken", "Order ahead") and the category's seasonal label --
    # has no back-office column, so ops mode keeps it from the board, matched by
    # normalised name or alias. Prices, names and sizes still come from ops.
    board_info: dict[str, tuple[str | None, str | None, str | None, bool]] = {}
    for bcat in board.category:
        for bitem in bcat.items:
            info = (bitem.note, bcat.seasonal, bitem.description, bitem.signature)
            for alias in (bitem.n, board.aliases.get(bitem.n)):
                if alias:
                    board_info.setdefault(normalise(alias), info)

    def make_item(name: str, group: list[Any]) -> SourceItem:
        b_note, b_season, b_desc, b_sig = board_info.get(normalise(name), (None, None, None, False))
        srow = next((g for g in group if g.season_name is not None), None)
        season = None
        if srow is not None:
            start, end = _as_date(srow.starts_on), _as_date(srow.ends_on)
            recurring = bool(srow.is_recurring_annually)
            season = SeasonInfo(
                srow.season_name, start, end, recurring, in_season(start, end, recurring, today)
            )
        elif b_season:
            season = SeasonInfo(b_season, None, None, False, True)
        note = next(
            (g.note for g in group if shape.has_note and g.note and str(g.note).strip()), None
        )
        return SourceItem(
            key=name,
            id="",
            name=name,
            sizes=_ops_sizes(group),
            web=_web(overlay.items.get(name), b_desc, b_sig),
            season=season,
            board_note=b_note,
            ops_note=note,
            has_photo=shape.has_photo and any(g.photo_asset_id is not None for g in group),
            ops_ids=[int(g.id) for g in group],
            natural=(rank.get(normalise(name), 1_000_000), name.casefold(), name),
        )

    rank = _board_rank(board)
    by_cat: dict[str, list[SourceItem]] = {}
    unassigned: list[SourceItem] = []
    for (cat, name), group in grouped.items():
        item = make_item(name, group)
        (by_cat.setdefault(cat, []) if cat else unassigned).append(item)

    # The board's slugs are the site's stable URLs (/menu#hot-matcha, photo slots
    # `menu.<slug>`). A back-office category with the board's name keeps that slug
    # unless the overlay sets another, so switching to ops breaks no link.
    board_slugs = {c.name.casefold(): c.slug for c in board.category}
    cats: list[SourceCategory] = []
    loose: list[str] = []
    for name, items in by_cat.items():
        ops_row = ops_cats.get(name) or ops_cats_fold.get(name.casefold())
        if ops_row is None:
            # Free text the legacy import left on the row, never registered in the
            # back office: not a public category. Admin-only until it's fixed there.
            loose.append(name)
            unassigned.extend(items)
            continue
        meta = overlay.category(name)
        cats.append(
            SourceCategory(
                name=name,
                slug=(meta.slug if meta and meta.slug else "")
                or board_slugs.get(name.casefold(), "")
                or slugify(name),
                kind=str(ops_row.kind) if ops_row is not None else None,
                blurb=meta.blurb if meta else None,
                hidden=bool(meta and meta.hidden),
                position=meta.position if meta else None,
                items=items,
                has_ops_row=ops_row is not None,
                display_name=meta.display_name if meta else None,
                natural=(
                    (0, int(ops_row.sort_order), name.casefold())
                    if ops_row is not None
                    else (1, 0, name.casefold())
                ),
            )
        )
    unassigned.sort(key=lambda i: i.natural)
    _sort(cats)
    _unique_slugs(cats)
    _assign_ids(cats, unassigned)

    snap = MenuSnapshot(
        "ops", cats, unassigned, extras, overlay_ready=overlay.ready, overlay=overlay.source
    )
    if overlay.source == "shop":
        unsynced = [i.name for c in cats for i in c.items if not i.web.has_row]
        if unsynced:
            snap.warnings.append(
                f"{len(unsynced)} item(s) have no Order online product row yet, so the website "
                "shows them with no description: open Café Ops › Menu items "  # noqa: RUF001
                "(the list syncs them)"
            )
    if unassigned:
        snap.warnings.append(
            f"{len(unassigned)} ops item(s) have no category, so they are not on the website"
        )
    if loose:
        snap.warnings.append(
            "not on the website until they get a back-office category: items filed under "
            + ", ".join(repr(n) for n in loose)
        )
    return snap


def build_snapshot(today: dt.date | None = None) -> MenuSnapshot:
    today = today or dt.datetime.now(ZoneInfo(get_settings().local_timezone)).date()
    board = load_board()  # extras come from the board in both modes
    _, extras = compile_board(board)
    with get_engine().connect() as conn:
        shape = ops_shape(conn)
        source, why = detect_source(conn, shape)
        overlay = load_overlay(conn, shape, ops=source == "ops")
        if source == "ops":
            snap = _ops_snapshot(conn, shape, overlay, extras, today, board)
        else:
            snap = _board_snapshot(overlay, extras)
            snap.reason = f"serving config/menu_board.toml: {why}"
            snap.warnings.insert(0, snap.reason)
    if source == "ops" and not shape.has_shop:
        snap.warnings.append(
            "the ops shop tables (shop_category / shop_product) are missing, so the website "
            "menu's presentation comes from the site's own legacy rows and cannot be edited: "
            "run the ops migration (`uv run alembic upgrade head` in cafeops)"
            if overlay.ready
            else "the ops shop tables (shop_category / shop_product) are missing and so are the "
            "site's own overlay tables: the website menu has no descriptions or blurbs until "
            "the ops migration runs (`uv run alembic upgrade head` in cafeops)"
        )
    return snap


# --- public menu ---------------------------------------------------------------------


def public_menu(snap: MenuSnapshot) -> MenuOut:
    categories: list[MenuCategoryOut] = []
    for c in snap.categories:
        if c.hidden:
            continue
        items = [
            MenuItemOut(
                id=i.id,
                name=i.name,
                description=i.public_description,
                note=i.board_note,
                signature=i.web.signature,
                seasonal=SeasonalOut(name=i.season.name) if i.season else None,
                sizes=i.sizes,
                # TODO(ops media): when i.has_photo, point at the ops file once the
                # back office confirms its scheme, expected /media/<sha256>.<ext>.
                # The site does not serve ops files.
                photo=None,
            )
            for i in c.items
            if i.public
        ]
        if items:
            categories.append(
                MenuCategoryOut(slug=c.slug, name=c.public_name, blurb=c.blurb or "", items=items)
            )
    body = {
        "source": snap.source,
        "categories": [c.model_dump(mode="json") for c in categories],
        "extras": snap.extras.model_dump(mode="json"),
    }
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return MenuOut(
        generated_at=utcnow(),
        source=snap.source,
        version=digest[:12],
        categories=categories,
        extras=snap.extras,
    )


def build_menu() -> MenuOut:
    return public_menu(build_snapshot())


_cache_lock = threading.Lock()
_cache: tuple[float, int, MenuOut] | None = None  # (built_at monotonic, board mtime_ns, menu)


def cached_menu() -> MenuOut:
    """Rebuilt when 60 s old, when the board file changes, or on ``invalidate``.
    Back-office edits (Menu items) therefore show within a minute."""
    global _cache
    with _cache_lock:
        now = time.monotonic()
        mtime = BOARD_PATH.stat().st_mtime_ns
        if _cache is not None and now - _cache[0] < CACHE_SECONDS and _cache[1] == mtime:
            return _cache[2]
        menu = build_menu()
        _cache = (now, mtime, menu)
        return menu


def invalidate() -> None:
    global _cache
    with _cache_lock:
        _cache = None


# --- health ------------------------------------------------------------------------


@dataclass
class Orphans:
    categories: list[str]
    items: list[str]


def known_names() -> tuple[set[str], set[str]]:
    """Every category and item name either source knows (board, or ops whether or
    not it is the current source), so a row seeded for the switch-over is not
    called an orphan before the switch."""
    board = load_board()
    cats = {c.name for c in board.category}
    items = {i.n for c in board.category for i in c.items}
    with get_engine().connect() as conn:
        shape = ops_shape(conn)
        if shape.item_columns:
            for r in conn.execute(
                text("SELECT DISTINCT name, category FROM menu_item WHERE active = :t"),
                {"t": True},
            ):
                items.add(r.name)
                if r.category and r.category.strip():
                    cats.add(r.category.strip())
        if shape.has_menu_category:
            cats.update(n for (n,) in conn.execute(text("SELECT name FROM menu_category")))
    return cats, items


def orphans() -> Orphans:
    with get_engine().connect() as conn:
        overlay = load_overlay(conn)
    cats, items = known_names()
    return Orphans(
        categories=sorted(n for n in overlay.categories if n not in cats),
        items=sorted(n for n in overlay.items if n not in items),
    )


def summary() -> dict[str, Any]:
    """For the admin dashboard: ``{source, warnings, messages, reason}``.

    ``warnings`` is a count (what the dashboard shows) of ``messages``, the things
    the owner could act on. Serving the board is the expected state until the back
    office has categories, so it is ``reason``, not a warning.
    """
    try:
        snap = build_snapshot()
    except Exception as exc:  # the dashboard must still render
        msg = f"menu unavailable: {type(exc).__name__}: {exc}"
        return {"source": "board", "warnings": 1, "messages": [msg], "reason": None}
    messages = [w for w in snap.warnings if w != snap.reason]
    if snap.overlay == "site":
        orph = orphans()
        n = len(orph.categories) + len(orph.items)
        if n:
            messages.append(
                f"{n} website menu setting(s) name items or categories no longer on any menu"
            )
    return {
        "source": snap.source,
        "overlay": snap.overlay,
        "warnings": len(messages),
        "messages": messages,
        "reason": snap.reason,
    }


# --- seeding the overlay from the board ------------------------------------------------


@dataclass
class SeedResult:
    categories_added: int = 0
    items_added: int = 0
    items_existing: int = 0
    mapped_to_ops: list[tuple[str, str]] = field(default_factory=list)


def seed_overlay() -> SeedResult:
    """Carry the board's blurbs, descriptions and signature flags into the overlay.

    Idempotent and never overwrites: a name that already has a row is left as the
    owner set it. Items are seeded under the board name and, where the drift
    matcher (alias or normalised name) finds one, under the ops name too, so the
    copy survives the switch to the ops source.

    Legacy: refused once the ops shop tables exist, because the site's overlay is
    then read-only (DECISIONS.md 29). Carry the board's copy into the shop with
    ``cafeops shop adopt-website-menu`` instead.
    """
    board = load_board()
    compiled, _ = compile_board(board)
    with get_engine().connect() as conn:
        if not overlay_ready(conn):
            raise RuntimeError("overlay tables missing: run `uv run alembic upgrade head`")
        shape = ops_shape(conn)
        if shape.has_shop:
            raise RuntimeError(
                "the website menu is edited in Café Ops › Menu items now "  # noqa: RUF001
                "(shop_product / "
                "shop_category); the site's own overlay is read-only. To carry the site's "
                "old rows into the shop run `uv run cafeops shop adopt-website-menu`."
            )
        ops_names: list[str] = (
            [
                n
                for (n,) in conn.execute(
                    text("SELECT DISTINCT name FROM menu_item WHERE active = :t"), {"t": True}
                )
            ]
            if shape.item_columns
            else []
        )
    ops_by_norm = {normalise(n): n for n in ops_names}
    alias = {normalise(k): v for k, v in board.aliases.items()}
    res = SeedResult()
    now = utcnow()
    with session_scope(immediate=True) as s:
        have_cats = set(s.scalars(select(SiteMenuCategoryMeta.name)))
        have_items = set(s.scalars(select(SiteMenuItemMeta.item_name)))
        for c in compiled:
            if c.name not in have_cats:
                s.add(SiteMenuCategoryMeta(name=c.name, blurb=c.blurb, updated_at=now))
                have_cats.add(c.name)
                res.categories_added += 1
            for i in c.items:
                if not (i.description or i.signature):
                    continue  # a row with nothing in it changes nothing
                targets = [i.name]
                ops = alias.get(normalise(i.name)) or ops_by_norm.get(normalise(i.name))
                if ops and ops in ops_names and ops != i.name:
                    targets.append(ops)
                for t in targets:
                    if t in have_items:
                        res.items_existing += 1
                        continue
                    s.add(
                        SiteMenuItemMeta(
                            item_name=t,
                            description=i.description,
                            signature=i.signature,
                            updated_at=now,
                        )
                    )
                    have_items.add(t)
                    res.items_added += 1
                    if t != i.name:
                        res.mapped_to_ops.append((i.name, t))
    invalidate()
    return res
