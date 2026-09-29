"""Read and validate the reference CSVs (`cafeops/seed/reference/`, README.md there).

Every file is parsed and checked BEFORE anything is written, and every problem is
collected, so one run lists all of them with file and row number rather than
stopping at the first. Checks: required columns, units and storage names, integer
pence (a `3.50` in a pence column is refused as float money), decimal-string
quantities, dates, duplicate keys, names that must exist (ingredients, menu items,
suppliers, categories, sizes), a number with no `source_url`/`fetched_on`, and photo
files (present, inside the folder, an image by its bytes, at most 2 MB).

Writing is `cafeops.services.reference_seed`; the site's photo library is fed by the
site's own `sashasite media-add` (`site_library_add` below), which processes its own
variants.
"""

from __future__ import annotations

import csv
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.config import REPO_ROOT, settings
from cafeops.db.models import Ingredient, MenuCategory, MenuItem, Supplier
from cafeops.domain.enums import Storage, Unit
from cafeops.domain.units import IncompatibleUnitsError, convert
from cafeops.seed.menu_board import _norm
from cafeops.services.media_store import MAX_BYTES, MediaRefusedError, sniff_image
from cafeops.services.reference_seed import (
    UK14,
    IngredientRow,
    MenuRow,
    PhotoRow,
    ProductRow,
    RecipeRow,
    shelf_problem,
    size_row,
)

DEFAULT_DIR = Path(__file__).resolve().parent / "reference"
SECTIONS = ("ingredients", "prices", "menu", "recipes", "photos", "ingredient-photos")
#: Opt-in only (`--only stock-photos`): a fallback for items photos.csv leaves bare.
OPT_IN = ("stock-photos",)
FILES: dict[str, tuple[str, ...]] = {
    "ingredients": ("ingredients.csv", "ingredients_extra.csv"),
    "prices": ("supplier_products.csv",),
    "menu": ("menu.csv",),
    "recipes": ("recipes.csv",),
    "photos": ("photos.csv",),
    "ingredient-photos": ("ingredient_photos.csv",),
    "stock-photos": ("photos_stock_menu.csv",),
}
COLUMNS: dict[str, tuple[str, ...]] = {
    "ingredients": (
        "name",
        "is_new",
        "unit",
        "category",
        "storage",
        "shelf_life_days",
        "open_life_days",
        "allergens",
        "source_url",
        "fetched_on",
        "note",
    ),
    "prices": (
        "ingredient",
        "supplier",
        "product_name",
        "sku",
        "pack_size",
        "pack_unit",
        "price_pence",
        "vat_included",
        "product_url",
        "fetched_on",
        "note",
    ),
    "menu": (
        "name",
        "category",
        "description",
        "tags",
        "benchmark_price_pence",
        "benchmark_source_url",
        "fetched_on",
    ),
    "recipes": ("menu_item", "size", "ingredient", "qty", "role", "source_url", "note"),
    "photos": (
        "menu_item",
        "file",
        "licence",
        "author",
        "source_page_url",
        "fetched_on",
        "alt",
    ),
    "ingredient-photos": (
        "ingredient",
        "file",
        "licence",
        "author",
        "source_page_url",
        "fetched_on",
        "alt",
    ),
}
COLUMNS["stock-photos"] = COLUMNS["photos"]
MIN_WIDTH = 1200
_INT = re.compile(r"^[0-9]+$")
_DEC = re.compile(r"^[0-9]+(\.[0-9]+)?$")
_SIZES = ("S", "M", "XL", "One")


@dataclass
class Bundle:
    root: Path
    present: set[str] = field(default_factory=set)
    ingredients: list[IngredientRow] = field(default_factory=list)
    products: list[ProductRow] = field(default_factory=list)
    menu: list[MenuRow] = field(default_factory=list)
    recipes: list[RecipeRow] = field(default_factory=list)
    photos: list[PhotoRow] = field(default_factory=list)
    ingredient_photos: list[PhotoRow] = field(default_factory=list)
    stock_photos: list[PhotoRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def planned_new(self, existing: set[str]) -> dict[str, Unit]:
        """New ingredients the ingredients section WILL create (it refuses some)."""
        return {
            r.name: r.unit
            for r in self.ingredients
            if r.is_new
            and r.name not in existing
            and shelf_problem(r.storage or Storage.AMBIENT, r.shelf_life_days, r.open_life_days, 0)
            is None
        }


class _Row:
    """One CSV row with its location, and typed getters that record errors."""

    def __init__(self, bundle: Bundle, file: str, line: int, raw: dict[str, str]) -> None:
        self.b = bundle
        self.where = f"{file}:{line}"
        self.raw = raw

    def err(self, msg: str) -> None:
        self.b.errors.append(f"{self.where}: {msg}")

    def s(self, col: str) -> str | None:
        v = (self.raw.get(col) or "").strip()
        return v or None

    def req(self, col: str) -> str:
        v = self.s(col)
        if v is None:
            self.err(f"{col} is required")
            return ""
        return v

    def int_(self, col: str, *, money: bool = False) -> int | None:
        v = self.s(col)
        if v is None:
            return None
        if not _INT.match(v):
            what = "whole pence (no £, no decimal point)" if money else "a whole number"
            self.err(f"{col} = {v!r} must be {what}")
            return None
        n = int(v)
        if n <= 0:
            self.err(f"{col} must be more than 0")
            return None
        return n

    def dec(self, col: str) -> Decimal | None:
        v = self.s(col)
        if v is None:
            self.err(f"{col} is required")
            return None
        if not _DEC.match(v):
            self.err(f"{col} = {v!r} must be a plain decimal string like 0.25")
            return None
        try:
            d = Decimal(v)
        except InvalidOperation:  # pragma: no cover - regex guards it
            self.err(f"{col} = {v!r} is not a decimal")
            return None
        if d <= 0:
            self.err(f"{col} must be more than 0")
            return None
        if d != d.quantize(Decimal("0.000001")):
            self.err(f"{col} = {v!r} has more than 6 decimal places")
            return None
        return d

    def day(self, col: str) -> date | None:
        v = self.s(col)
        if v is None:
            return None
        try:
            return date.fromisoformat(v)
        except ValueError:
            self.err(f"{col} = {v!r} must be YYYY-MM-DD")
            return None

    def unit(self, col: str) -> Unit | None:
        v = self.req(col)
        try:
            return Unit(v) if v else None
        except ValueError:
            self.err(f"{col} = {v!r} must be one of L|KG|ML|G|EACH")
            return None

    def bool_(self, col: str) -> bool:
        v = (self.s(col) or "").lower()
        if v in ("true", "false"):
            return v == "true"
        self.err(f"{col} = {v!r} must be true or false")
        return False


def _read(bundle: Bundle, section: str, name: str) -> list[_Row]:
    path = bundle.root / name
    if not path.exists():
        return []
    bundle.present.add(section)
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = [h.strip() for h in (reader.fieldnames or [])]
        missing = [c for c in COLUMNS[section] if c not in header]
        if missing:
            bundle.errors.append(f"{name}: missing column(s) {', '.join(missing)}")
            return []
        extra = [c for c in header if c and c not in COLUMNS[section]]
        if extra:
            bundle.warnings.append(f"{name}: extra column(s) ignored: {', '.join(extra)}")
        rows: list[_Row] = []
        for i, raw in enumerate(reader, start=2):
            clean = {(k or "").strip(): (v or "") for k, v in raw.items() if k is not None}
            if not any(v.strip() for v in clean.values()):
                continue
            rows.append(_Row(bundle, name, i, clean))
        return rows


def _evidence(r: _Row, what: str, url_col: str = "source_url") -> None:
    if r.s(url_col) is None or r.s("fetched_on") is None:
        r.err(f"{what} without {url_col} and fetched_on (a number without a source)")


@dataclass(frozen=True)
class _Db:
    ingredients: dict[str, Unit]
    ingredient_folded: dict[str, str]
    menu: dict[str, list[MenuItem]]
    suppliers: set[str]
    categories: set[str]


def _db(session: Session) -> _Db:
    ings = {i.name: i.unit for i in session.scalars(select(Ingredient))}
    menu: dict[str, list[MenuItem]] = {}
    for m in session.scalars(select(MenuItem).order_by(MenuItem.id)):
        menu.setdefault(m.name, []).append(m)
    cats = {c.casefold() for c in session.scalars(select(MenuCategory.name))}
    cats |= {(m.category or "").casefold() for rows in menu.values() for m in rows if m.category}
    return _Db(
        ingredients=ings,
        ingredient_folded={n.casefold(): n for n in ings},
        menu=menu,
        suppliers=set(session.scalars(select(Supplier.name))),
        categories=cats,
    )


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80]


def load(root: Path, session: Session) -> Bundle:
    """Parse and validate every file under `root`. Reads the database, writes nothing."""
    b = Bundle(root=root)
    db = _db(session)
    _ingredients(b, db)
    known = set(db.ingredients) | {r.name for r in b.ingredients if r.is_new}
    units = dict(db.ingredients) | {r.name: r.unit for r in b.ingredients if r.is_new}
    _products(b, db, known, units)
    _menu(b, db)
    _recipes(b, db, known)
    b.photos = _photos(b, db, "photos", known)
    b.stock_photos = _photos(b, db, "stock-photos", known)
    b.ingredient_photos = _photos(b, db, "ingredient-photos", known)
    return b


def _ingredients(b: Bundle, db: _Db) -> None:
    seen: dict[str, str] = {}
    for name in FILES["ingredients"]:
        for r in _read(b, "ingredients", name):
            n = r.req("name")
            is_new = r.bool_("is_new")
            unit = r.unit("unit")
            storage: Storage | None = None
            if (sv := r.s("storage")) is not None:
                try:
                    storage = Storage(sv)
                except ValueError:
                    r.err(f"storage = {sv!r} must be AMBIENT|CHILLED|FROZEN")
            elif is_new:
                r.err("storage is required for a new ingredient")
            shelf = r.int_("shelf_life_days")
            opened = r.int_("open_life_days")
            allergens: tuple[str, ...] | None = None
            if (av := r.s("allergens")) is not None:
                if av.lower() == "none":
                    allergens = ()
                else:
                    parts = tuple(sorted({p.strip().lower() for p in av.split("|") if p.strip()}))
                    bad = [p for p in parts if p not in UK14]
                    if bad:
                        r.err(f"allergens {', '.join(bad)} not in the UK 14")
                    allergens = parts
            fetched = r.day("fetched_on")
            if shelf is not None or opened is not None or allergens is not None:
                _evidence(r, "shelf life / allergens")
            if not n:
                continue
            if n in seen:
                r.err(f"{n!r} already listed at {seen[n]}")
                continue
            seen[n] = r.where
            if is_new:
                folded = db.ingredient_folded.get(n.casefold())
                if folded is not None and folded != n:
                    r.err(f"{n!r} differs only in case from existing {folded!r}")
            elif n not in db.ingredients:
                hint = db.ingredient_folded.get(n.casefold())
                r.err(
                    f"unknown ingredient {n!r}"
                    + (f" (did you mean {hint!r}?)" if hint else "; mark is_new=true to create it")
                )
            if unit is None:
                continue
            b.ingredients.append(
                IngredientRow(
                    where=r.where,
                    name=n,
                    is_new=is_new,
                    unit=unit,
                    category=r.s("category"),
                    storage=storage,
                    shelf_life_days=shelf,
                    open_life_days=opened,
                    allergens=allergens,
                    source_url=r.s("source_url"),
                    fetched_on=fetched,
                    note=r.s("note"),
                )
            )


def _products(b: Bundle, db: _Db, known: set[str], units: dict[str, Unit]) -> None:
    seen: dict[tuple[str, str, str], str] = {}
    for r in _read(b, "prices", FILES["prices"][0]):
        ing = r.req("ingredient")
        sup = r.req("supplier")
        pname = r.req("product_name")
        sku = (r.s("sku") or _slug(pname))[:80]
        size = r.dec("pack_size")
        unit = r.unit("pack_unit")
        price = r.int_("price_pence", money=True)
        if r.s("price_pence") is None:
            r.err("price_pence is required")
        vat = r.bool_("vat_included")
        url = r.req("product_url")
        fetched = r.day("fetched_on")
        if fetched is None and r.s("fetched_on") is None:
            r.err("fetched_on is required (a price without a date is not evidence)")
        if ing and ing not in known:
            r.err(f"unknown ingredient {ing!r}")
        if sup and sup not in db.suppliers:
            r.err(f"unknown supplier {sup!r} (one of: {', '.join(sorted(db.suppliers))})")
        key = (sup, ing, sku)
        if key in seen:
            r.err(f"duplicate (supplier, ingredient, sku) {key}, first at {seen[key]}")
            continue
        seen[key] = r.where
        if size is not None and unit is not None and ing in units:
            try:
                convert(size, unit, units[ing])
            except IncompatibleUnitsError:
                r.err(f"pack unit {unit.value} cannot convert to {ing}'s unit {units[ing].value}")
        if not (ing and sup and size and unit and price and url and fetched):
            continue
        b.products.append(
            ProductRow(
                where=r.where,
                ingredient=ing,
                supplier=sup,
                product_name=pname,
                sku=sku,
                pack_size=size,
                pack_unit=unit,
                price_pence=price,
                vat_included=vat,
                product_url=url,
                fetched_on=fetched,
                note=r.s("note"),
            )
        )


def _menu(b: Bundle, db: _Db) -> None:
    seen: dict[str, str] = {}
    for r in _read(b, "menu", FILES["menu"][0]):
        n = r.req("name")
        desc = r.s("description")
        if desc is not None and len(desc) > 140:
            r.err(f"description is {len(desc)} characters; the limit is 140")
        tags = tuple(t.strip().lower() for t in (r.s("tags") or "").split("|") if t.strip())
        bench = r.int_("benchmark_price_pence", money=True)
        fetched = r.day("fetched_on")
        if bench is not None:
            _evidence(r, "benchmark_price_pence", "benchmark_source_url")
        if not n:
            continue
        if n in seen:
            r.err(f"{n!r} already listed at {seen[n]}")
            continue
        seen[n] = r.where
        if n not in db.menu:
            r.err(f"unknown menu item {n!r}")
            continue
        b.menu.append(
            MenuRow(
                where=r.where,
                name=n,
                description=desc,
                tags=tags,
                benchmark_price_pence=bench,
                benchmark_source_url=r.s("benchmark_source_url"),
                fetched_on=fetched,
            )
        )


def _recipes(b: Bundle, db: _Db, known: set[str]) -> None:
    seen: dict[tuple[str, str, str], str] = {}
    for r in _read(b, "recipes", FILES["recipes"][0]):
        item = r.req("menu_item")
        size = r.req("size")
        ing = r.req("ingredient")
        qty = r.dec("qty")
        # A line derived from a sibling recipe ("mirrors Hot Rose Matcha", a cup and
        # lid) carries its reasoning in `note` instead of a URL. One of the two.
        url = r.s("source_url") or ""
        if not url and r.s("note") is None:
            r.err("source_url or a note saying where the quantity comes from is required")
        if size and size not in _SIZES:
            r.err(f"size = {size!r} must be one of S|M|XL|One")
        if item and item not in db.menu:
            r.err(f"unknown menu item {item!r}")
        elif item and size in _SIZES and size_row(db.menu[item], size) is None:
            have = ", ".join(m.size_code.value if m.size_code else "One" for m in db.menu[item])
            r.err(f"{item!r} has no size {size} (it has {have})")
        if ing and ing not in known:
            r.err(f"unknown ingredient {ing!r}")
        key = (item, size, ing)
        if key in seen:
            r.err(f"duplicate (menu_item, size, ingredient), first at {seen[key]}")
            continue
        seen[key] = r.where
        if not (item and size and ing and qty and (url or r.s("note"))):
            continue
        b.recipes.append(
            RecipeRow(
                where=r.where,
                menu_item=item,
                size=size,
                ingredient=ing,
                qty=qty,
                role=r.s("role"),
                source_url=url,
                note=r.s("note"),
            )
        )


def _photos(b: Bundle, db: _Db, section: str, known: set[str]) -> list[PhotoRow]:
    out: list[PhotoRow] = []
    target_col = "ingredient" if section == "ingredient-photos" else "menu_item"
    seen: dict[str, str] = {}
    root = b.root.resolve()
    for r in _read(b, section, FILES[section][0]):
        target = r.req(target_col)
        rel = r.req("file")
        licence = r.req("licence")
        page = r.req("source_page_url")
        fetched = r.day("fetched_on")
        if r.s("fetched_on") is None:
            r.err("fetched_on is required")
        if r.s("author") is None:
            b.warnings.append(f"{r.where}: no author recorded")
        alt = r.s("alt") or ""
        if not alt:
            b.warnings.append(f"{r.where}: no alt text")
        category: str | None = None
        name: str | None = target or None
        if section == "ingredient-photos":
            if target and target not in known:
                r.err(f"unknown ingredient {target!r}")
        elif target.startswith("category:"):
            category = target.split(":", 1)[1].strip()
            name = None
            if category.casefold() not in db.categories:
                r.err(f"unknown menu category {category!r}")
        elif target and target not in db.menu:
            r.err(f"unknown menu item {target!r}")
        if target:
            if target in seen:
                r.err(f"{target!r} already has a photo row at {seen[target]}")
            seen[target] = r.where
        path = (b.root / rel).resolve() if rel else None
        if path is not None:
            if not path.is_relative_to(root):
                r.err(f"file {rel!r} is outside the reference folder")
                path = None
            elif not path.is_file():
                r.err(f"file {rel!r} not found")
                path = None
        if path is not None:
            size = path.stat().st_size
            if size > MAX_BYTES:
                r.err(f"{rel} is {size / 1024 / 1024:.1f} MB; the limit is 2 MB")
                path = None
            else:
                try:
                    _ct, width, _h = sniff_image(path.read_bytes())
                    if width is not None and width < MIN_WIDTH:
                        b.warnings.append(f"{r.where}: {rel} is {width}px wide (< {MIN_WIDTH})")
                except MediaRefusedError:
                    r.err(f"{rel} is not a JPEG, PNG or WebP (judged by its bytes)")
                    path = None
        if path is None or not (target and licence and page and fetched):
            continue
        out.append(
            PhotoRow(
                where=r.where,
                target=name,
                category=category,
                file=path,
                licence=licence,
                author=r.s("author"),
                source_page_url=page,
                fetched_on=fetched,
                alt=alt,
            )
        )
    return out


# --------------------------------------------------------------------------
# The menu board and the website's photo library
# --------------------------------------------------------------------------


def board_described(session: Session, board: Path | None = None) -> frozenset[str]:
    """Ops item names whose website text the TV menu board supplies (a description or
    a signature mark), matched the way the site matches: alias first, then a
    normalised name."""
    path = board or REPO_ROOT / "site" / "backend" / "config" / "menu_board.toml"
    if not path.exists():
        return frozenset()
    doc = tomllib.loads(path.read_text(encoding="utf-8"))
    aliases = {_norm(str(k)): str(v) for k, v in doc.get("aliases", {}).items()}
    names = set(session.scalars(select(MenuItem.name).distinct()))
    by_norm = {_norm(n): n for n in names}
    out: set[str] = set()
    for cat in doc.get("category", []):
        for item in cat.get("items", []):
            if not (item.get("description") or item.get("signature")):
                continue
            board_name = str(item["n"])
            out.add(board_name)
            ops = aliases.get(_norm(board_name)) or by_norm.get(_norm(board_name))
            if ops:
                out.add(ops)
    return frozenset(out)


def site_library_add(file: Path, alt: str) -> str:
    """Add a photo to the website's library with the site's own processing
    (`sashasite media-add`, no slot). Same database as cafeops, always."""
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env["SITE_DATABASE_URL"] = settings.database_url
    cmd = [
        "uv",
        "run",
        "--quiet",
        "--project",
        str(REPO_ROOT / "site" / "backend"),
        "sashasite",
        "media-add",
        str(file),
        "--alt",
        alt,
    ]
    done = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300, check=False)
    if done.returncode != 0:
        raise RuntimeError(
            f"sashasite media-add {file.name} failed: {(done.stderr or done.stdout).strip()}"
        )
    return done.stdout.strip()
