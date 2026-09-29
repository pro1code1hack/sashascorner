"""Load researched reference data: `cafeops seed-reference`. Contract:
`cafeops/seed/reference/README.md`.

Researched values are starting points, not facts, so every section here follows the
contract's first rule: **nothing outranks what the owner entered.**

- ingredients: new ones are created; on existing ones storage / shelf life / opened
  life are filled only while `shelf_life_source` is empty or ESTIMATE (a confirmed
  shelf life is never touched), and allergens only where the column is NULL.
- prices: supplier links are upserted by (supplier, ingredient, sku) and never
  starred when the ingredient already has a starred link; the recipe price is
  written as ESTIMATE only when the current one is not INVOICE / SUPPLIER_FEED
  (invariant 8), then the cost rollup runs.
- menu: description, tags and a benchmark go to `menu_item_reference`; the
  description also becomes the website blurb where the site has none. Sell prices
  are never touched.
- recipes: one-off lines for sizes that have NO recipe yet, dated from now through
  `menu_catalog.stage_manual_lines`, flagged "researched recipe — confirm".
- photos: `media_asset` (content-addressed, deduped), provenance recorded, attached
  to menu items / ingredients only where they have no photo. Website slots are
  never assigned.

Each section is one function and one transaction. With `commit=False` it reads only
and reports what it would do; with `commit=True` it writes, runs the rollup and
flushes; the caller's transaction commits (one `unit_of_work` per section). Every
decision is made against current state, so a second commit reports only skips.

Quantities are compared as loaded `Decimal`s in Python, never in SQL (ARCHITECTURE
8E).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    inspect,
    or_,
    select,
)
from sqlalchemy.orm import Session

from cafeops.db.models import (
    Ingredient,
    IngredientPrice,
    ManualRecipeLine,
    MediaAsset,
    MenuItem,
    MenuItemReference,
    Supplier,
    SupplierProduct,
)
from cafeops.domain.enums import PriceSource, SizeCode, Storage, Tier, Unit
from cafeops.domain.units import IncompatibleUnitsError, convert, pounds
from cafeops.jobs.cost_rollup import rollup_for_ingredient, rollup_menu_items
from cafeops.services.media_store import sniff_image, store_image
from cafeops.services.menu_catalog import LineIn, stage_manual_lines

__all__ = [
    "ACTOR",
    "RECIPE_FLAG",
    "UK14",
    "Change",
    "IngredientRow",
    "LibraryJob",
    "MenuRow",
    "PhotoRow",
    "ProductRow",
    "RecipeRow",
    "SectionReport",
    "seed_ingredient_photos",
    "seed_ingredients",
    "seed_menu",
    "seed_menu_photos",
    "seed_prices",
    "seed_recipes",
    "shelf_problem",
    "size_row",
]

ACTOR = "reference-seed"
RECIPE_FLAG = "researched recipe — confirm"
UK14 = frozenset(
    {
        "celery",
        "cereals_gluten",
        "crustaceans",
        "eggs",
        "fish",
        "lupin",
        "milk",
        "molluscs",
        "mustard",
        "tree_nuts",
        "peanuts",
        "sesame",
        "soya",
        "sulphites",
    }
)
_OWNER_SOURCES = (PriceSource.INVOICE, PriceSource.SUPPLIER_FEED)

Action = Literal["insert", "update", "skip"]


# --------------------------------------------------------------------------
# Rows (parsed and validated by cafeops/seed/reference_csv.py)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IngredientRow:
    where: str
    name: str
    is_new: bool
    unit: Unit
    category: str | None
    storage: Storage | None
    shelf_life_days: int | None
    open_life_days: int | None
    #: None = unknown (leave alone); () = confirmed none.
    allergens: tuple[str, ...] | None
    source_url: str | None
    fetched_on: date | None
    note: str | None


@dataclass(frozen=True, slots=True)
class ProductRow:
    where: str
    ingredient: str
    supplier: str
    product_name: str
    sku: str
    pack_size: Decimal
    pack_unit: Unit
    price_pence: int
    vat_included: bool
    product_url: str
    fetched_on: date
    note: str | None


@dataclass(frozen=True, slots=True)
class MenuRow:
    where: str
    name: str
    description: str | None
    tags: tuple[str, ...]
    benchmark_price_pence: int | None
    benchmark_source_url: str | None
    fetched_on: date | None


@dataclass(frozen=True, slots=True)
class RecipeRow:
    where: str
    menu_item: str
    size: str  # S | M | XL | One
    ingredient: str
    qty: Decimal
    role: str | None
    source_url: str
    note: str | None


@dataclass(frozen=True, slots=True)
class PhotoRow:
    where: str
    #: A menu item name, an ingredient name, or None for a category row.
    target: str | None
    #: For `category:<name>` rows in photos.csv.
    category: str | None
    file: Path
    licence: str
    author: str | None
    source_page_url: str
    fetched_on: date
    alt: str


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Change:
    action: Action
    key: str
    #: For an insert/update: what changes. For a skip: WHY, worded generically so
    #: skips group by reason.
    detail: str


@dataclass
class SectionReport:
    section: str
    committed: bool = False
    changes: list[Change] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, action: Action, key: str, detail: str) -> None:
        self.changes.append(Change(action, key, detail))

    def count(self, action: Action) -> int:
        return sum(1 for c in self.changes if c.action == action)

    def skip_reasons(self) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for c in self.changes:
            if c.action == "skip":
                counts[c.detail] = counts.get(c.detail, 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


@dataclass(frozen=True, slots=True)
class LibraryJob:
    """A photo the website's library (`site_media`) does not have yet. The site
    processes its own variants, so the caller hands these to the site's own
    `media-add` AFTER this section has committed (it needs the write lock)."""

    file: Path
    alt: str
    sha256: str


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _ingredients(session: Session) -> dict[str, Ingredient]:
    return {i.name: i for i in session.scalars(select(Ingredient))}


def _items_by_name(session: Session) -> dict[str, list[MenuItem]]:
    out: dict[str, list[MenuItem]] = {}
    for row in session.scalars(select(MenuItem).order_by(MenuItem.id)):
        out.setdefault(row.name, []).append(row)
    return out


def size_row(rows: Sequence[MenuItem], size: str) -> MenuItem | None:
    """The row for a size column. "One" is the size-less row, or the only row there
    is (legacy one-size products sit under M)."""
    if size == "One":
        for r in rows:
            if r.size_code is None:
                return r
        return rows[0] if len(rows) == 1 else None
    code = SizeCode(size)
    return next((r for r in rows if r.size_code is code), None)


def _finish(session: Session, report: SectionReport, commit: bool) -> SectionReport:
    """Close a section. `commit` means "these writes are meant to persist": they are
    flushed and the report says so. The caller's transaction commits them (or, on a
    dry run, rolls back whatever a section staged) -- a service never ends the
    caller's transaction (ARCHITECTURE: services flush, callers commit)."""
    if commit:
        session.flush()
        report.committed = True
    return report


def shelf_problem(
    storage: Storage, shelf: int | None, opened: int | None, buffer_days: int
) -> str | None:
    """The same rules `ingredient_catalog.create_ingredient` enforces."""
    if storage in (Storage.CHILLED, Storage.FROZEN) and shelf is None:
        return "chilled/frozen without a shelf life would never expire"
    if shelf is not None and shelf <= 0:
        return "shelf life must be at least 1 day"
    if opened is not None and opened <= 0:
        return "opened life must be at least 1 day"
    if shelf is not None and opened is not None and opened > shelf:
        return "opened life longer than shelf life"
    if shelf is not None and buffer_days >= shelf:
        return "shelf life not longer than the transit buffer"
    return None


def _days(v: int | None) -> str:
    return "never" if v is None else f"{v}d"


def _qty(v: Decimal) -> str:
    text = format(v.normalize(), "f")
    return text


def _at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 12, tzinfo=UTC)


# --------------------------------------------------------------------------
# ingredients
# --------------------------------------------------------------------------


def seed_ingredients(
    session: Session, rows: Sequence[IngredientRow], *, commit: bool
) -> SectionReport:
    report = SectionReport("ingredients")
    existing = _ingredients(session)
    for row in rows:
        ing = existing.get(row.name)
        if ing is None:
            _new_ingredient(session, report, row, commit=commit)
        else:
            _fill_ingredient(report, ing, row, commit=commit)
    if commit:
        session.flush()
    return _finish(session, report, commit)


def _new_ingredient(
    session: Session, report: SectionReport, row: IngredientRow, *, commit: bool
) -> None:
    if not row.is_new:
        report.add("skip", row.name, "not in the database and not marked is_new")
        return
    storage = row.storage or Storage.AMBIENT
    problem = shelf_problem(storage, row.shelf_life_days, row.open_life_days, 0)
    if problem:
        report.add("skip", row.name, f"refused: {problem}")
        return
    allergens = "unknown" if row.allergens is None else ("|".join(row.allergens) or "none")
    report.add(
        "insert",
        row.name,
        f"{row.unit.value}, {row.category or 'no category'}, {storage.value}, "
        f"shelf {_days(row.shelf_life_days)}, opened {_days(row.open_life_days)}, "
        f"allergens {allergens}",
    )
    if not commit:
        return
    has_life = row.shelf_life_days is not None or row.open_life_days is not None
    session.add(
        Ingredient(
            name=row.name,
            unit=row.unit,
            category=row.category,
            tier=Tier.C,
            tracking_enabled=False,
            waste_factor=Decimal("0"),
            storage=storage,
            shelf_life_days=row.shelf_life_days,
            open_life_days=row.open_life_days,
            transit_buffer_days=0,
            shelf_life_source=PriceSource.ESTIMATE if has_life else None,
            source_note=(row.source_url or row.note or "")[:400] or None,
            allergens=list(row.allergens) if row.allergens is not None else None,
            allergens_source=row.source_url if row.allergens is not None else None,
        )
    )
    session.flush()


def _fill_ingredient(
    report: SectionReport, ing: Ingredient, row: IngredientRow, *, commit: bool
) -> None:
    if ing.retired_at is not None:
        report.add("skip", row.name, "ingredient is retired")
        return
    done: list[str] = []
    kept: list[str] = []
    if ing.unit is not row.unit:
        kept.append(f"unit is {ing.unit.value} here, {row.unit.value} in the file")

    storage = row.storage if row.storage is not None and row.storage is not ing.storage else None
    shelf = (
        row.shelf_life_days
        if row.shelf_life_days is not None and row.shelf_life_days != ing.shelf_life_days
        else None
    )
    opened = (
        row.open_life_days
        if row.open_life_days is not None and row.open_life_days != ing.open_life_days
        else None
    )
    if storage is not None or shelf is not None or opened is not None:
        if ing.shelf_life_source not in (None, PriceSource.ESTIMATE):
            kept.append("shelf life is confirmed")
        else:
            new_storage = storage or ing.storage
            new_shelf = shelf if shelf is not None else ing.shelf_life_days
            new_open = opened if opened is not None else ing.open_life_days
            problem = shelf_problem(new_storage, new_shelf, new_open, ing.transit_buffer_days)
            if problem:
                kept.append(f"shelf life refused: {problem}")
            else:
                if storage is not None:
                    done.append(f"storage {ing.storage.value}→{storage.value}")
                if shelf is not None:
                    done.append(f"shelf {_days(ing.shelf_life_days)}→{_days(shelf)}")
                if opened is not None:
                    done.append(f"opened {_days(ing.open_life_days)}→{_days(opened)}")
                if commit:
                    ing.storage = new_storage
                    ing.shelf_life_days = new_shelf
                    ing.open_life_days = new_open
                    ing.shelf_life_source = PriceSource.ESTIMATE
                    ing.source_note = (row.source_url or "")[:400] or ing.source_note

    if row.allergens is not None:
        if ing.allergens is None:
            done.append(f"allergens {'|'.join(row.allergens) or 'none'}")
            if commit:
                ing.allergens = list(row.allergens)
                ing.allergens_source = row.source_url
        elif sorted(ing.allergens) != sorted(row.allergens):
            kept.append("allergens already recorded")

    if done:
        report.add("update", row.name, "; ".join(done + [f"kept: {k}" for k in kept]))
    else:
        report.add("skip", row.name, "; ".join(kept) or "already up to date")


# --------------------------------------------------------------------------
# prices
# --------------------------------------------------------------------------


def _per_unit(pack_size: Decimal, pack_unit: Unit, price_pence: int, unit: Unit) -> Decimal:
    return Decimal(price_pence) / convert(pack_size, pack_unit, unit)


def _has_star(session: Session, ingredient_id: int) -> bool:
    return (
        session.scalar(
            select(SupplierProduct.id)
            .join(Supplier, Supplier.id == SupplierProduct.supplier_id)
            .where(
                SupplierProduct.ingredient_id == ingredient_id,
                SupplierProduct.is_preferred.is_(True),
                SupplierProduct.archived_at.is_(None),
                Supplier.archived_at.is_(None),
            )
            .limit(1)
        )
        is not None
    )


def seed_prices(
    session: Session,
    rows: Sequence[ProductRow],
    *,
    commit: bool,
    planned: Mapping[str, Unit],
    actor: str = ACTOR,
) -> SectionReport:
    """`planned`: ingredients the ingredients section will create (dry run only)."""
    report = SectionReport("prices")
    at = datetime.now(UTC)
    suppliers = {s.name: s for s in session.scalars(select(Supplier))}
    ings = _ingredients(session)
    #: ingredient id -> [(row, starred)] for choosing the recipe price.
    chosen: dict[int, list[tuple[ProductRow, bool, Supplier]]] = {}
    starred_now: set[int] = set()
    for row in rows:
        key = f"{row.supplier} / {row.ingredient} / {row.sku}"
        supplier = suppliers.get(row.supplier)
        if supplier is None:
            report.add("skip", key, "supplier not in the database")
            continue
        if supplier.archived_at is not None:
            report.add("skip", key, "supplier is archived")
            continue
        ing = ings.get(row.ingredient)
        if ing is None:
            if row.ingredient in planned and not commit:
                report.add(
                    "insert",
                    key,
                    f"{_qty(row.pack_size)} {row.pack_unit.value} at "
                    f"{pounds(row.price_pence)} ★ (ingredient created by the "
                    "ingredients section first)",
                )
                report.add("insert", f"{row.ingredient} price", "ESTIMATE from the new link")
            else:
                report.add("skip", key, "ingredient not in the database")
            continue
        try:
            convert(row.pack_size, row.pack_unit, ing.unit)
        except IncompatibleUnitsError:
            report.add("skip", key, "pack unit cannot convert to the ingredient's unit")
            continue
        product = session.scalar(
            select(SupplierProduct).where(
                SupplierProduct.supplier_id == supplier.id,
                SupplierProduct.ingredient_id == ing.id,
                SupplierProduct.sku == row.sku,
            )
        )
        if product is None:
            starred = not _has_star(session, ing.id) and ing.id not in starred_now
            if starred:
                starred_now.add(ing.id)
            report.add(
                "insert",
                key,
                f"{_qty(row.pack_size)} {row.pack_unit.value} at {pounds(row.price_pence)}"
                + (" inc VAT" if row.vat_included else "")
                + (" ★" if starred else ""),
            )
            if commit:
                session.add(
                    SupplierProduct(
                        supplier_id=supplier.id,
                        ingredient_id=ing.id,
                        sku=row.sku,
                        pack_size=row.pack_size,
                        pack_unit=row.pack_unit,
                        price_pence=row.price_pence,
                        is_preferred=starred,
                        product_url=row.product_url,
                        moq_packs=1,
                        last_seen_price_at=_at(row.fetched_on),
                    )
                )
                session.flush()
            chosen.setdefault(ing.id, []).append((row, starred, supplier))
            continue
        if product.archived_at is not None:
            report.add("skip", key, "link was archived by a person")
            continue
        if (
            product.is_preferred
            and ing.current_cost_source in _OWNER_SOURCES
            and product.price_pence != row.price_pence
        ):
            report.add("skip", key, "★ link priced from an invoice or supplier feed")
            continue
        diffs: list[str] = []
        if product.pack_size != row.pack_size or product.pack_unit is not row.pack_unit:
            diffs.append(
                f"pack {_qty(product.pack_size)} {product.pack_unit.value}→"
                f"{_qty(row.pack_size)} {row.pack_unit.value}"
            )
        if product.price_pence != row.price_pence:
            diffs.append(f"price {pounds(product.price_pence)}→{pounds(row.price_pence)}")
        if product.product_url != row.product_url:
            diffs.append("url")
        seen = _at(row.fetched_on)
        newer = product.last_seen_price_at is None or product.last_seen_price_at < seen
        if diffs or newer:
            if not diffs:
                diffs.append(f"last seen {row.fetched_on.isoformat()}")
            report.add("update", key, ", ".join(diffs))
            if commit:
                product.pack_size = row.pack_size
                product.pack_unit = row.pack_unit
                product.price_pence = row.price_pence
                product.product_url = row.product_url
                if newer:
                    product.last_seen_price_at = seen
        else:
            report.add("skip", key, "link already up to date")
        chosen.setdefault(ing.id, []).append((row, product.is_preferred, supplier))

    recost: list[int] = []
    for ing_id, candidates in chosen.items():
        ing = session.get(Ingredient, ing_id)
        if ing is None:  # pragma: no cover - loaded above
            continue
        if _record_estimate(session, report, ing, candidates, at=at, actor=actor, commit=commit):
            recost.append(ing_id)
    if commit:
        session.flush()
        costed = 0
        for ing_id in recost:
            costed += rollup_for_ingredient(
                session, ing_id, at=at, trigger=f"researched price ({actor})"
            ).costed
        if recost:
            report.notes.append(
                f"cost rollup: {len(recost)} new recipe price(s), {costed} item costings refreshed"
            )
    return _finish(session, report, commit)


def _record_estimate(
    session: Session,
    report: SectionReport,
    ing: Ingredient,
    candidates: list[tuple[ProductRow, bool, Supplier]],
    *,
    at: datetime,
    actor: str,
    commit: bool,
) -> bool:
    """Open an ESTIMATE ingredient_price from the researched link. True if written."""
    key = f"{ing.name} price"
    if ing.current_cost_source in _OWNER_SOURCES:
        report.add("skip", key, "recipe price is from an invoice or supplier feed")
        return False
    starred = [c for c in candidates if c[1]]
    pool = starred or candidates
    row, _star, supplier = min(
        pool,
        key=lambda c: (_per_unit(c[0].pack_size, c[0].pack_unit, c[0].price_pence, ing.unit),),
    )
    per_unit = _per_unit(row.pack_size, row.pack_unit, row.price_pence, ing.unit)
    current = session.scalar(
        select(IngredientPrice)
        .where(IngredientPrice.ingredient_id == ing.id, IngredientPrice.effective_to.is_(None))
        .order_by(IngredientPrice.effective_from.desc())
        .limit(1)
    )
    if (
        current is not None
        and current.source is PriceSource.ESTIMATE
        and current.supplier_id == supplier.id
        and current.pack_cost_pence == row.price_pence
        and current.pack_size == row.pack_size
        and current.pack_unit is row.pack_unit
    ):
        report.add("skip", key, "researched price already recorded")
        return False
    if current is not None and current.effective_from >= at:
        report.add("skip", key, "a price was recorded this instant")
        return False
    was = (
        f"{_qty(current.cost_per_unit_pence.quantize(Decimal('0.0001')))}p {current.source.value}"
        if current is not None
        else "none"
    )
    report.add(
        "insert",
        key,
        f"ESTIMATE {_qty(per_unit.quantize(Decimal('0.0001')))}p/{ing.unit.value} "
        f"from {supplier.name} (was {was})",
    )
    if not commit:
        return False
    if current is not None:
        current.effective_to = at
    vat = "inc VAT" if row.vat_included else "ex VAT"
    session.add(
        IngredientPrice(
            ingredient_id=ing.id,
            supplier_id=supplier.id,
            pack_size=row.pack_size,
            pack_unit=row.pack_unit,
            pack_cost_pence=row.price_pence,
            cost_per_unit_pence=per_unit,
            effective_from=at,
            source=PriceSource.ESTIMATE,
            note=f"researched {row.fetched_on.isoformat()} ({vat}): {row.product_url}"[:400],
            recorded_by=actor,
        )
    )
    ing.current_cost_pence_per_unit = per_unit
    ing.current_cost_source = PriceSource.ESTIMATE
    session.flush()
    return True


# --------------------------------------------------------------------------
# menu
# --------------------------------------------------------------------------

#: The website's overlay table (site/backend owns it and its migrations; it lives in
#: the same SQLite file). Declared on a private MetaData so cafeops' Alembic never
#: sees it.
_SITE = MetaData()
_SITE_ITEM_META = Table(
    "site_menu_item_meta",
    _SITE,
    Column("item_name", String(200), primary_key=True),
    Column("description", Text),
    Column("signature", Boolean),
    Column("hidden", Boolean),
    Column("position", Integer),
    Column("use_ops_note", Boolean),
    Column("updated_at", DateTime),
)
_SITE_MEDIA = Table(
    "site_media",
    _SITE,
    Column("id", Integer, primary_key=True),
    Column("sha256", String(64)),
)


def _has_table(session: Session, name: str) -> bool:
    return inspect(session.get_bind()).has_table(name)


def seed_menu(
    session: Session,
    rows: Sequence[MenuRow],
    *,
    commit: bool,
    board_described: frozenset[str],
) -> SectionReport:
    """`board_described`: ops names whose text the TV menu board already supplies (the
    site shows the board's description/signature while it has no overlay row, and an
    overlay row would replace both)."""
    report = SectionReport("menu")
    now = datetime.now(UTC)
    names = set(session.scalars(select(MenuItem.name).distinct()))
    refs = {r.item_name: r for r in session.scalars(select(MenuItemReference))}
    site = _has_table(session, "site_menu_item_meta")
    meta: dict[str, str | None] = {}
    if site:
        meta = {
            str(n): d
            for n, d in session.execute(
                select(_SITE_ITEM_META.c.item_name, _SITE_ITEM_META.c.description)
            )
        }
    for row in rows:
        if row.name not in names:
            report.add("skip", row.name, "menu item not in the database")
            continue
        _reference(session, report, refs.get(row.name), row, now=now, commit=commit)
        if not row.description:
            continue
        key = f"{row.name} (website blurb)"
        if not site:
            report.add("skip", key, "website tables not migrated")
        elif row.name in meta:
            current = (meta[row.name] or "").strip()
            if current:
                report.add("skip", key, "website already has a description")
            else:
                report.add("update", key, row.description)
                if commit:
                    session.execute(
                        _SITE_ITEM_META.update()
                        .where(_SITE_ITEM_META.c.item_name == row.name)
                        .values(
                            description=row.description,
                            updated_at=now.replace(tzinfo=None),
                        )
                    )
        elif row.name in board_described:
            report.add("skip", key, "the menu board supplies this item's text")
        else:
            report.add("insert", key, row.description)
            if commit:
                session.execute(
                    _SITE_ITEM_META.insert().values(
                        item_name=row.name,
                        description=row.description,
                        signature=False,
                        hidden=False,
                        position=None,
                        use_ops_note=False,
                        updated_at=now.replace(tzinfo=None),
                    )
                )
    if commit:
        session.flush()
    return _finish(session, report, commit)


def _reference(
    session: Session,
    report: SectionReport,
    ref: MenuItemReference | None,
    row: MenuRow,
    *,
    now: datetime,
    commit: bool,
) -> None:
    key = f"{row.name} (reference)"
    tags = list(row.tags) or None
    bench = f", benchmark {pounds(row.benchmark_price_pence)}" if row.benchmark_price_pence else ""
    if ref is None:
        report.add("insert", key, f"tags {'|'.join(row.tags) or '-'}{bench}")
        if commit:
            session.add(
                MenuItemReference(
                    item_name=row.name,
                    description=row.description,
                    tags=tags,
                    benchmark_price_pence=row.benchmark_price_pence,
                    benchmark_source_url=row.benchmark_source_url,
                    fetched_on=row.fetched_on,
                    updated_at=now,
                )
            )
        return
    diffs: list[str] = []
    if ref.description != row.description:
        diffs.append("description")
    if (ref.tags or None) != tags:
        diffs.append("tags")
    if ref.benchmark_price_pence != row.benchmark_price_pence:
        diffs.append("benchmark")
    if ref.benchmark_source_url != row.benchmark_source_url or ref.fetched_on != row.fetched_on:
        diffs.append("source")
    if not diffs:
        report.add("skip", key, "reference already up to date")
        return
    report.add("update", key, ", ".join(diffs))
    if commit:
        ref.description = row.description
        ref.tags = tags
        ref.benchmark_price_pence = row.benchmark_price_pence
        ref.benchmark_source_url = row.benchmark_source_url
        ref.fetched_on = row.fetched_on
        ref.updated_at = now


# --------------------------------------------------------------------------
# recipes
# --------------------------------------------------------------------------


def seed_recipes(
    session: Session,
    rows: Sequence[RecipeRow],
    *,
    commit: bool,
    planned: Mapping[str, Unit],
    actor: str = ACTOR,
) -> SectionReport:
    report = SectionReport("recipes")
    at = datetime.now(UTC)
    items = _items_by_name(session)
    ings = _ingredients(session)
    groups: dict[tuple[str, str], list[RecipeRow]] = {}
    for row in rows:
        groups.setdefault((row.menu_item, row.size), []).append(row)
    touched: list[int] = []
    for (name, size), lines in groups.items():
        key = f"{name} {size}"
        members = items.get(name)
        if not members:
            report.add("skip", key, "menu item not in the database")
            continue
        target = size_row(members, size)
        if target is None:
            report.add("skip", key, "menu item has no such size")
            continue
        if target.template_id is not None:
            report.add("skip", key, "made from a template (has a recipe)")
            continue
        open_lines = session.scalars(
            select(ManualRecipeLine.id).where(
                ManualRecipeLine.menu_item_id == target.id,
                ManualRecipeLine.effective_from <= at,
                or_(
                    ManualRecipeLine.effective_to.is_(None),
                    ManualRecipeLine.effective_to > at,
                ),
            )
        ).all()
        if open_lines:
            report.add("skip", key, "already has recipe lines")
            continue
        missing = [ln.ingredient for ln in lines if ln.ingredient not in ings]
        pending = [m for m in missing if m in planned and not commit]
        if len(pending) != len(missing):
            report.add("skip", key, "needs an ingredient that is not in the database")
            continue
        detail = ", ".join(f"{ln.ingredient} {_qty(ln.qty)}" for ln in lines)
        report.add("insert", key, detail + (" (after new ingredients)" if pending else ""))
        if not commit:
            continue
        target.manual_recipe = True
        flag = target.data_quality_flag
        if not flag:
            target.data_quality_flag = RECIPE_FLAG
        elif RECIPE_FLAG not in flag:
            target.data_quality_flag = f"{flag}; {RECIPE_FLAG}"[:200]
        session.flush()
        stage_manual_lines(
            session,
            target.id,
            [LineIn(ingredient_id=ings[ln.ingredient].id, qty=ln.qty) for ln in lines],
            actor=actor,
            effective_from=at,
        )
        touched.append(target.id)
    if commit and touched:
        rolled = rollup_menu_items(session, touched, at=at, trigger=f"researched recipes ({actor})")
        report.notes.append(f"cost rollup: {rolled.costed} menu item(s) costed")
    return _finish(session, report, commit)


# --------------------------------------------------------------------------
# photos
# --------------------------------------------------------------------------


@dataclass
class _PhotoState:
    """Per-run memory so a dry run reports what a commit would do, row by row."""

    planned_sha: set[str] = field(default_factory=set)
    library_sha: set[str] = field(default_factory=set)
    jobs: list[LibraryJob] = field(default_factory=list)


def _asset_for(
    session: Session,
    report: SectionReport,
    row: PhotoRow,
    state: _PhotoState,
    *,
    commit: bool,
    actor: str,
    library: bool,
) -> tuple[str, MediaAsset | None]:
    """(sha256, asset) -- the asset is None on a dry run for a photo not stored yet."""
    data = row.file.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    asset = session.scalar(select(MediaAsset).where(MediaAsset.sha256 == sha))
    key = row.file.name
    if asset is None:
        if sha not in state.planned_sha:
            content_type, width, height = sniff_image(data)
            report.add(
                "insert",
                f"{key} (media)",
                f"{content_type} {width}x{height}, {len(data) // 1024} kB, {row.licence}",
            )
            state.planned_sha.add(sha)
        if commit:
            stored = store_image(session, data, uploaded_by=actor)
            asset = session.get(MediaAsset, stored.asset_id)
    if asset is not None:
        filled: list[str] = []
        if asset.licence is None:
            filled.append("licence")
        if asset.author is None and row.author:
            filled.append("author")
        if asset.source_url is None:
            filled.append("source")
        if commit:
            asset.licence = asset.licence or row.licence[:80]
            asset.author = asset.author or (row.author or "")[:200] or None
            asset.source_url = asset.source_url or row.source_page_url[:500]
        if filled and sha not in state.planned_sha:
            report.add("update", f"{key} (media)", "provenance: " + ", ".join(filled))
    # A third-party packshot (a brand's product photo on the Deliveroo listing) is fine
    # on an internal menu item but not the café's to publish: keep it out of the site.
    if library and row.licence.startswith("third-party"):
        report.add("skip", f"{key} (website library)", "third-party packshot: not for the site")
    elif library and sha not in state.library_sha:
        state.library_sha.add(sha)
        state.jobs.append(LibraryJob(file=row.file, alt=row.alt, sha256=sha))
        report.add("insert", f"{key} (website library)", row.alt or "(no alt text)")
    return sha, asset


def seed_menu_photos(
    session: Session,
    rows: Sequence[PhotoRow],
    *,
    commit: bool,
    section: str = "photos",
    fallback: bool = False,
    skip_names: frozenset[str] = frozenset(),
    actor: str = ACTOR,
) -> tuple[SectionReport, list[LibraryJob], frozenset[str]]:
    """Menu photos: media asset, provenance, website library, menu item link.

    Returns (report, library jobs to run after the commit, names given a photo).
    `fallback` (photos_stock_menu.csv): only items with no photo on ANY size, and
    not in `skip_names` (items photos.csv is giving a photo in this run).
    """
    report = SectionReport(section)
    state = _PhotoState()
    library = _has_table(session, "site_media")
    if library:
        state.library_sha = {str(s) for s in session.scalars(select(_SITE_MEDIA.c.sha256))}
    else:
        report.notes.append("website library tables missing: photos not added to it")
    items = _items_by_name(session)
    assigned: set[str] = set()
    ordered = sorted(rows, key=lambda r: r.category is not None)  # items before categories
    for row in ordered:
        if row.category is not None:
            targets = [
                name
                for name, members in items.items()
                if any(
                    m.active and (m.category or "").casefold() == row.category.casefold()
                    for m in members
                )
            ]
            label = f"category:{row.category}"
        else:
            targets = [row.target] if row.target else []
            label = row.target or "?"
        given: list[str] = []
        fill: list[MenuItem] = []
        for name in targets:
            members = items.get(name, [])
            if not members:
                report.add("skip", name, "menu item not in the database")
                continue
            if name in assigned:
                continue
            if name in skip_names:
                report.add("skip", name, "photos.csv gives it a photo in this run")
                continue
            empty = [m for m in members if m.photo_asset_id is None]
            if not empty:
                if row.category is None:
                    report.add("skip", name, "menu item already has a photo")
                continue
            if fallback and len(empty) != len(members):
                report.add("skip", name, "menu item already has a photo")
                continue
            given.append(name)
            assigned.add(name)
            fill.extend(empty)
        if given or not fallback:
            # A fallback photo nobody needs is not stored at all: it would only
            # clutter the library the owner picks website photos from.
            _sha, asset = _asset_for(
                session, report, row, state, commit=commit, actor=actor, library=library
            )
            if commit and asset is not None:
                for m in fill:
                    m.photo_asset_id = asset.id
        if given:
            report.add(
                "update",
                label,
                f"photo {row.file.name} → "
                + (", ".join(given) if len(given) <= 4 else f"{len(given)} items"),
            )
        elif row.category is not None:
            report.add("skip", label, "every item in the category already has a photo")
    if commit:
        session.flush()
    return _finish(session, report, commit), state.jobs, frozenset(assigned)


def seed_ingredient_photos(
    session: Session,
    rows: Sequence[PhotoRow],
    *,
    commit: bool,
    planned: Mapping[str, Unit],
    actor: str = ACTOR,
) -> SectionReport:
    """Ingredient reference photos: media asset + provenance, linked where the
    ingredient has none. Not added to the website library (they are not for the site)."""
    report = SectionReport("ingredient-photos")
    state = _PhotoState()
    ings = _ingredients(session)
    for row in rows:
        _sha, asset = _asset_for(
            session, report, row, state, commit=commit, actor=actor, library=False
        )
        name = row.target or ""
        ing = ings.get(name)
        if ing is None:
            if name in planned and not commit:
                report.add("update", name, f"photo {row.file.name} (after it is created)")
            else:
                report.add("skip", name, "ingredient not in the database")
            continue
        if ing.photo_asset_id is not None:
            report.add("skip", name, "ingredient already has a photo")
            continue
        report.add("update", name, f"photo {row.file.name}")
        if commit and asset is not None:
            ing.photo_asset_id = asset.id
    if commit:
        session.flush()
    return _finish(session, report, commit)
