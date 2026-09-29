"""The shop's catalogue: synced FROM the ops menu, served TO the ordering app.

CONTRACT §2.3-2.6 and §4 `GET /api/shop/catalogue`.

Two writers and one reader:

- `sync_categories` inserts a `shop_category` for every `menu_category` without one;
  `sync_products` inserts a `shop_product` for every active `menu_item` name without
  one. Neither deletes and neither overwrites an admin's copy: a product the admin
  moved to another category stays there, a retired item keeps its blurb for when it
  comes back. Both are called by the admin GET and by the migration's data step.
- `catalogue` reads everything the customer sees in one pass -- sizes and prices come
  from `menu_item` live, so a price change on the till is a price change in the shop
  the moment the client refetches -- and hashes the payload into `version`, which is
  what the client caches on.

Nothing here commits. `in_session` (the API) or the caller owns the transaction.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import (
    MediaAsset,
    MenuCategory,
    MenuItem,
    ShopBanner,
    ShopCategory,
    ShopOptionGroup,
    ShopProduct,
    ShopProductOptionGroup,
    ShopSettings,
    ShopUpsell,
    UpsellPlacement,
)
from cafeops.domain.shop import SIZE_LABELS, SIZE_ORDER, allergens_state, slugify
from cafeops.services.media_store import media_url
from cafeops.services.shop.errors import ShopError

__all__ = [
    "BannerView",
    "Catalogue",
    "CategoryView",
    "GroupView",
    "OptionView",
    "ProductView",
    "SizeView",
    "UpsellView",
    "catalogue",
    "groups_for_product",
    "load_settings",
    "option_groups_by_id",
    "product_slug",
    "sync_categories",
    "sync_products",
]


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------


def load_settings(session: Session) -> ShopSettings:
    """The one row. Its absence is a deployment fault (the migration writes it)."""
    row = session.get(ShopSettings, 1)
    if row is None:
        raise ShopError(
            503,
            "shop_not_set_up",
            "Online ordering is not set up on this server. Run the migrations "
            "(`uv run alembic upgrade head`).",
        )
    return row


# --------------------------------------------------------------------------
# sync from the ops menu
# --------------------------------------------------------------------------


def _unique_slug(base: str, taken: set[str]) -> str:
    slug = base or "category"
    n = 2
    while slug in taken:
        slug = f"{base}-{n}"
        n += 1
    taken.add(slug)
    return slug


def sync_categories(session: Session) -> int:
    """A `shop_category` for every `menu_category` missing one. Returns how many were made."""
    existing = {row.ops_name: row for row in session.scalars(select(ShopCategory))}
    taken = {row.slug for row in existing.values()}
    now = datetime.now(UTC)
    made = 0
    for cat in session.scalars(
        select(MenuCategory).order_by(MenuCategory.sort_order, MenuCategory.id)
    ):
        if cat.name in existing:
            continue
        session.add(
            ShopCategory(
                ops_name=cat.name,
                name=cat.name,
                slug=_unique_slug(slugify(cat.name), taken),
                sort_order=cat.sort_order,
                visible=True,
                updated_at=now,
            )
        )
        made += 1
    if made:
        session.flush()
    return made


def sync_products(session: Session) -> int:
    """A `shop_product` for every active `menu_item` name missing one. Never deletes.

    An existing row is left alone (the admin may have moved or renamed it); only a row
    whose category was never set picks up the menu's.
    """
    existing = {row.item_name: row for row in session.scalars(select(ShopProduct))}
    now = datetime.now(UTC)
    next_sort = max((row.sort_order for row in existing.values()), default=0)
    made = 0
    seen: set[str] = set()
    for item in session.scalars(
        select(MenuItem).where(MenuItem.active.is_(True)).order_by(MenuItem.id)
    ):
        if item.name in seen:
            continue
        seen.add(item.name)
        category = (item.category or "").strip() or None
        row = existing.get(item.name)
        if row is not None:
            if row.category_ops_name is None and category is not None:
                row.category_ops_name = category
            continue
        next_sort += 10
        session.add(
            ShopProduct(
                item_name=item.name,
                category_ops_name=category,
                allergens=[],
                dietary=[],
                sort_order=next_sort,
                visible=True,
                available=True,
                featured=False,
                updated_at=now,
            )
        )
        made += 1
    if made:
        session.flush()
    return made


# --------------------------------------------------------------------------
# the customer's catalogue
# --------------------------------------------------------------------------


#: Products whose ops menu item has no category are served under this catch-all
#: category (it sorts last; the admin's Menu items list shows "no category").
MORE_SLUG = "more"
MORE_NAME = "More from the menu"


@dataclass(frozen=True, slots=True)
class SizeView:
    menu_item_id: int
    code: str
    label: str
    price_pence: int
    kcal: int | None


@dataclass(frozen=True, slots=True)
class OptionView:
    id: int
    name: str
    description: str | None
    price_delta_pence: int
    kcal: int | None
    is_default: bool
    available: bool
    photo_url: str | None


@dataclass(frozen=True, slots=True)
class GroupView:
    id: int
    name: str
    prompt: str | None
    kind: str
    layout: str
    required: bool
    min_select: int
    max_select: int | None
    collapsed: bool
    options: tuple[OptionView, ...]


@dataclass(frozen=True, slots=True)
class UpsellView:
    heading: str
    product_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ProductView:
    id: int
    slug: str
    name: str
    category_slug: str | None
    description: str | None
    note: str | None
    kcal: int | None
    kcal_by_size: dict[str, Any] | None
    nutrition: dict[str, Any] | None
    allergens: tuple[str, ...]
    #: 'unknown' (empty list: nobody said), 'none' (confirmed), 'listed'.
    allergens_state: str
    dietary: tuple[str, ...]
    ingredients_text: str | None
    photo_url: str | None
    badge: str | None
    available: bool
    featured: bool
    from_price_pence: int
    default_size: str
    sizes: tuple[SizeView, ...]
    option_groups: tuple[GroupView, ...]
    upsells: tuple[UpsellView, ...]


@dataclass(frozen=True, slots=True)
class CategoryView:
    id: int
    slug: str
    name: str
    blurb: str | None
    photo_url: str | None
    #: True when `photo_url` is borrowed from the first visible product with one.
    photo_is_fallback: bool
    product_count: int


@dataclass(frozen=True, slots=True)
class BannerView:
    id: int
    title: str
    subtitle: str | None
    photo_url: str | None
    link_href: str | None


@dataclass(frozen=True, slots=True)
class Catalogue:
    generated_at: datetime
    version: str
    banners: tuple[BannerView, ...]
    categories: tuple[CategoryView, ...]
    products: tuple[ProductView, ...]
    basket_upsells: tuple[UpsellView, ...]
    #: Every other product's id by slug, for the client's routing.
    slugs: dict[str, int] = field(default_factory=dict)


def product_slug(product: ShopProduct) -> str:
    return slugify(product.display_name or product.item_name) or f"item-{product.id}"


def option_groups_by_id(
    session: Session, *, active_only: bool = True
) -> dict[int, ShopOptionGroup]:
    stmt = select(ShopOptionGroup).order_by(ShopOptionGroup.sort_order, ShopOptionGroup.id)
    if active_only:
        stmt = stmt.where(ShopOptionGroup.active.is_(True))
    return {g.id: g for g in session.scalars(stmt)}


def groups_for_product(
    product: ShopProduct,
    category_slug: str | None,
    groups: Mapping[int, ShopOptionGroup],
    attachments: Mapping[int, Sequence[tuple[int, int]]],
) -> list[ShopOptionGroup]:
    """Explicit attachments (their own order) then category-wide groups, each once."""
    chosen: dict[int, tuple[int, int]] = {}
    for group_id, sort in attachments.get(product.id, ()):
        if group_id in groups:
            chosen[group_id] = (0, sort)
    for group in groups.values():
        if group.id in chosen:
            continue
        if category_slug is not None and category_slug in (group.applies_to_categories or []):
            chosen[group.id] = (1, group.sort_order)
    ordered = sorted(chosen.items(), key=lambda kv: (kv[1][0], kv[1][1], kv[0]))
    return [groups[gid] for gid, _ in ordered]


def _photo_lookup(session: Session, ids: Iterable[int | None]) -> dict[int, str]:
    wanted = sorted({i for i in ids if i is not None})
    if not wanted:
        return {}
    return {
        asset.id: media_url(asset.filename)
        for asset in session.scalars(select(MediaAsset).where(MediaAsset.id.in_(wanted)))
    }


def _group_view(group: ShopOptionGroup, photos: Mapping[int, str]) -> GroupView:
    return GroupView(
        id=group.id,
        name=group.name,
        prompt=group.prompt,
        kind=group.kind.value.lower(),
        layout=group.layout.value.lower(),
        required=group.required,
        min_select=group.min_select,
        max_select=group.max_select,
        collapsed=group.collapsed,
        options=tuple(
            OptionView(
                id=o.id,
                name=o.name,
                description=o.description,
                price_delta_pence=o.price_delta_pence,
                kcal=o.kcal,
                is_default=o.is_default,
                available=o.available,
                photo_url=photos.get(o.photo_asset_id) if o.photo_asset_id else None,
            )
            for o in group.options
        ),
    )


def _banner_live(b: ShopBanner, today: date) -> bool:
    if not b.active:
        return False
    if b.starts_on is not None and today < b.starts_on:
        return False
    return not (b.ends_on is not None and today > b.ends_on)


def _version(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def catalogue(session: Session, *, now: datetime | None = None) -> Catalogue:
    """Everything the ordering app renders, in one read. Prices live from `menu_item`."""
    now = now or datetime.now(UTC)
    today = now.astimezone(settings.tz).date()

    categories = list(
        session.scalars(select(ShopCategory).order_by(ShopCategory.sort_order, ShopCategory.id))
    )
    slug_by_ops = {c.ops_name: c.slug for c in categories}
    visible_slugs = {c.slug for c in categories if c.visible}

    # Active menu rows by product name -- the sizes and their prices.
    rows_by_name: dict[str, list[MenuItem]] = defaultdict(list)
    for item in session.scalars(select(MenuItem).where(MenuItem.active.is_(True))):
        rows_by_name[item.name].append(item)

    products = list(
        session.scalars(select(ShopProduct).order_by(ShopProduct.sort_order, ShopProduct.id))
    )
    groups = option_groups_by_id(session)
    attachments: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for link in session.scalars(
        select(ShopProductOptionGroup).order_by(
            ShopProductOptionGroup.sort_order, ShopProductOptionGroup.group_id
        )
    ):
        attachments[link.product_id].append((link.group_id, link.sort_order))
    upsells = list(
        session.scalars(
            select(ShopUpsell)
            .where(ShopUpsell.active.is_(True))
            .order_by(ShopUpsell.sort_order, ShopUpsell.id)
        )
    )
    banners = [
        b
        for b in session.scalars(select(ShopBanner).order_by(ShopBanner.sort_order, ShopBanner.id))
        if _banner_live(b, today)
    ]

    photo_ids: list[int | None] = [p.photo_asset_id for p in products]
    photo_ids += [c.photo_asset_id for c in categories]
    photo_ids += [b.photo_asset_id for b in banners]
    photo_ids += [o.photo_asset_id for g in groups.values() for o in g.options]
    for rows in rows_by_name.values():
        photo_ids += [r.photo_asset_id for r in rows]
    photos = _photo_lookup(session, photo_ids)

    group_views = {gid: _group_view(g, photos) for gid, g in groups.items()}
    item_upsells = tuple(
        UpsellView(u.heading, tuple(int(i) for i in u.product_ids))
        for u in upsells
        if u.placement is UpsellPlacement.ITEM_PAGE
    )
    basket_upsells = tuple(
        UpsellView(u.heading, tuple(int(i) for i in u.product_ids))
        for u in upsells
        if u.placement is UpsellPlacement.BASKET
    )

    served: list[ProductView] = []
    slugs: dict[str, int] = {}
    count_by_slug: dict[str, int] = defaultdict(int)
    #: The first served product's photo per category, for a tile with none of its own.
    first_photo_by_slug: dict[str, str] = {}
    for product in products:
        rows = rows_by_name.get(product.item_name, [])
        category_slug = slug_by_ops.get(product.category_ops_name or "")
        if not product.visible or not rows:
            continue
        if category_slug is not None and category_slug not in visible_slugs:
            continue
        if category_slug is None:
            # An ops menu item with no category (58 of them at the time of writing,
            # Pumpkin Spice Latte included). Hiding them would be a silent loss; they
            # go in one catch-all tile until somebody assigns a category in Menu items.
            category_slug = MORE_SLUG
        sizes = sorted(
            (
                SizeView(
                    menu_item_id=r.id,
                    code=r.size_code.value if r.size_code else "ONE",
                    label=SIZE_LABELS.get(r.size_code.value if r.size_code else "ONE", ""),
                    price_pence=r.price_pence,
                    kcal=(
                        int(product.kcal_by_size[r.size_code.value])
                        if product.kcal_by_size
                        and r.size_code
                        and str(product.kcal_by_size.get(r.size_code.value, "")).isdigit()
                        else product.kcal
                    ),
                )
                for r in rows
            ),
            key=lambda s: SIZE_ORDER.get(s.code, 9),
        )
        cheapest = min(sizes, key=lambda s: (s.price_pence, SIZE_ORDER.get(s.code, 9)))
        default_size = (
            product.default_size
            if product.default_size in {s.code for s in sizes}
            else cheapest.code
        )
        photo = photos.get(product.photo_asset_id) if product.photo_asset_id else None
        if photo is None:
            photo = next(
                (photos[r.photo_asset_id] for r in rows if r.photo_asset_id in photos), None
            )
        slug = product_slug(product)
        if slug in slugs:
            slug = f"{slug}-{product.id}"
        slugs[slug] = product.id
        if category_slug is not None:
            count_by_slug[category_slug] += 1
            if photo is not None and category_slug not in first_photo_by_slug:
                first_photo_by_slug[category_slug] = photo
        served.append(
            ProductView(
                id=product.id,
                slug=slug,
                name=product.display_name or product.item_name,
                category_slug=category_slug,
                description=product.description,
                note=product.note,
                kcal=product.kcal,
                kcal_by_size=product.kcal_by_size,
                nutrition=product.nutrition,
                allergens=tuple(str(a) for a in product.allergens or []),
                allergens_state=allergens_state(product.allergens or []),
                dietary=tuple(str(d) for d in product.dietary or []),
                ingredients_text=product.ingredients_text,
                photo_url=photo,
                badge=product.badge,
                available=product.available,
                featured=product.featured,
                from_price_pence=cheapest.price_pence,
                default_size=default_size,
                sizes=tuple(sizes),
                option_groups=tuple(
                    group_views[g.id]
                    for g in groups_for_product(product, category_slug, groups, attachments)
                ),
                upsells=item_upsells,
            )
        )

    category_views: list[CategoryView] = []
    if count_by_slug.get(MORE_SLUG):
        category_views.append(
            CategoryView(
                id=0,
                slug=MORE_SLUG,
                name=MORE_NAME,
                blurb=None,
                photo_url=first_photo_by_slug.get(MORE_SLUG),
                photo_is_fallback=MORE_SLUG in first_photo_by_slug,
                product_count=count_by_slug[MORE_SLUG],
            )
        )
    for c in categories:
        if not c.visible:
            continue
        own = photos.get(c.photo_asset_id) if c.photo_asset_id else None
        category_views.append(
            CategoryView(
                id=c.id,
                slug=c.slug,
                name=c.name,
                blurb=c.blurb,
                photo_url=own if own is not None else first_photo_by_slug.get(c.slug),
                photo_is_fallback=own is None and c.slug in first_photo_by_slug,
                product_count=count_by_slug.get(c.slug, 0),
            )
        )
    banner_views = tuple(
        BannerView(
            id=b.id,
            title=b.title,
            subtitle=b.subtitle,
            photo_url=photos.get(b.photo_asset_id) if b.photo_asset_id else None,
            link_href=b.link_href,
        )
        for b in banners
    )
    body = {
        "banners": [asdict(b) for b in banner_views],
        "categories": [asdict(c) for c in category_views],
        "products": [asdict(p) for p in served],
        "basket_upsells": [asdict(u) for u in basket_upsells],
    }
    return Catalogue(
        generated_at=now,
        version=_version(body),
        banners=banner_views,
        categories=tuple(category_views),
        products=tuple(served),
        basket_upsells=basket_upsells,
        slugs=slugs,
    )
