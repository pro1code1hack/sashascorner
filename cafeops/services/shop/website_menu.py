"""Carry the public website's old menu presentation into the shop catalogue.

Until 2026-09-29 the website's menu page had its own overlay (``site_menu_item_meta``
/ ``site_menu_category_meta``, owned by ``sashasite``): descriptions, signature marks,
hidden flags, category blurbs. DECISIONS.md 29 ("one menu everywhere") makes the shop
catalogue (``shop_product`` / ``shop_category``) the one overlay the website reads, so
the copy the owner already wrote has to move over once or the public menu goes blank.

Idempotent and never overwrites: a shop field that is already set (a description, a
``featured`` or a hidden product, a blurb) is left as the back office set it. The site
tables are read with Core ``text()`` selects (cafeops has no model for them) and never
written or dropped. Run as ``cafeops shop adopt-website-menu``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from cafeops.db.models import ShopCategory, ShopProduct
from cafeops.services.shop.catalog import sync_categories, sync_products

__all__ = ["AdoptReport", "adopt_website_menu"]


@dataclass
class AdoptReport:
    site_tables_present: bool = True
    site_items: int = 0
    site_categories: int = 0
    descriptions: list[str] = field(default_factory=list)
    featured: list[str] = field(default_factory=list)
    hidden: list[str] = field(default_factory=list)
    blurbs: list[str] = field(default_factory=list)
    hidden_categories: list[str] = field(default_factory=list)
    #: Site rows naming no shop product / category (board-only names, retired items).
    unmatched_items: list[str] = field(default_factory=list)
    unmatched_categories: list[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return (
            len(self.descriptions)
            + len(self.featured)
            + len(self.hidden)
            + len(self.blurbs)
            + len(self.hidden_categories)
        )


def _lookup[T](rows: dict[str, T], name: str) -> T | None:
    hit = rows.get(name)
    if hit is not None:
        return hit
    fold = name.casefold()
    return next((r for n, r in rows.items() if n.casefold() == fold), None)


def adopt_website_menu(session: Session, *, dry_run: bool = False) -> AdoptReport:
    """Copy site overlay values into shop rows that still have the default.

    Syncs the catalogue first so every active menu name has a product row. With
    ``dry_run`` nothing is flushed: the report says what would change.
    """
    rep = AdoptReport()
    insp = inspect(session.connection())
    if not (insp.has_table("site_menu_item_meta") and insp.has_table("site_menu_category_meta")):
        rep.site_tables_present = False
        return rep
    sync_categories(session)
    sync_products(session)

    products: dict[str, ShopProduct] = {
        p.item_name: p for p in session.scalars(select(ShopProduct))
    }
    categories: dict[str, ShopCategory] = {
        c.ops_name: c for c in session.scalars(select(ShopCategory))
    }
    now = datetime.now(UTC)

    site_items = session.execute(
        text("SELECT item_name, description, signature, hidden FROM site_menu_item_meta")
    ).all()
    rep.site_items = len(site_items)
    touched_products: set[int] = set()
    for r in site_items:
        product = _lookup(products, str(r.item_name))
        if product is None:
            rep.unmatched_items.append(str(r.item_name))
            continue
        desc = (r.description or "").strip()
        if desc and not (product.description or "").strip():
            product.description = desc[:600]
            rep.descriptions.append(product.item_name)
            touched_products.add(product.id)
        if bool(r.signature) and not product.featured:
            product.featured = True
            rep.featured.append(product.item_name)
            touched_products.add(product.id)
        if bool(r.hidden) and product.visible:
            product.visible = False
            rep.hidden.append(product.item_name)
            touched_products.add(product.id)
    for p in products.values():
        if p.id in touched_products:
            p.updated_at = now

    site_cats = session.execute(
        text("SELECT name, blurb, hidden FROM site_menu_category_meta")
    ).all()
    rep.site_categories = len(site_cats)
    for r in site_cats:
        cat = _lookup(categories, str(r.name))
        if cat is None:
            rep.unmatched_categories.append(str(r.name))
            continue
        blurb = (r.blurb or "").strip()
        changed = False
        if blurb and not (cat.blurb or "").strip():
            cat.blurb = blurb[:300]
            rep.blurbs.append(cat.ops_name)
            changed = True
        if bool(r.hidden) and cat.visible:
            cat.visible = False
            rep.hidden_categories.append(cat.ops_name)
            changed = True
        if changed:
            cat.updated_at = now

    if dry_run:
        session.rollback()
    else:
        session.flush()
    return rep
