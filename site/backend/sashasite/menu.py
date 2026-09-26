"""The board menu: config/menu_board.toml, transcribed from the café's own boards.

This is the FALLBACK source. menu_source.py serves the ops back office's menu
instead once it has categories; until then this file is the menu, and it always
supplies ``extras``. `sashasite doctor` compares the two (see drift.py). Prices are
pounds in the file and integer pence everywhere else, converted with Decimal,
never float.
"""

from __future__ import annotations

import re
import threading
import time
import tomllib
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sashasite.config import CONFIG_DIR
from sashasite.schemas import (
    ExtraOut,
    ExtrasOut,
    MenuCategoryOut,
    MenuItemOut,
    MenuOut,
    SeasonalOut,
    SizeOut,
)

BOARD_PATH = CONFIG_DIR / "menu_board.toml"
CACHE_SECONDS = 60

SizeCode = Literal["S", "M", "XL", "One"]
SIZE_ORDER: dict[str, int] = {"S": 0, "M": 1, "XL": 2, "One": 3}
SIZE_LABEL: dict[str, str] = {"S": "Small", "M": "Medium", "XL": "Large", "One": "One size"}


def slugify(name: str) -> str:
    s = name.casefold().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def pounds_to_pence(token: str) -> int:
    """'4.80' -> 480. Refuses anything that is not an exact, non-negative amount
    of whole pence -- '4.805', 'NaN', '-1', '4,80' all raise."""
    try:
        d = Decimal(token)
    except InvalidOperation as exc:
        raise ValueError(f"{token!r} is not a number") from exc
    if not d.is_finite() or d < 0:
        raise ValueError(f"{token!r} is not a valid price")
    pence = d * 100
    if pence != pence.to_integral_value():
        raise ValueError(f"{token!r} has fractions of a penny")
    return int(pence)


# --- file format ------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BoardItem(_Strict):
    n: str = Field(min_length=1)
    p: str
    description: str | None = None
    signature: bool = False
    note: str | None = None


class BoardCategory(_Strict):
    slug: str = Field(pattern=r"^[a-z0-9-]+$")
    name: str
    blurb: str
    sizes: list[SizeCode] = Field(min_length=1)
    seasonal: str | None = None
    items: list[BoardItem] = Field(min_length=1)


class BoardExtra(_Strict):
    n: str
    p: str


class BoardExtras(_Strict):
    title: str
    items: list[BoardExtra]


class MenuBoard(_Strict):
    category: list[BoardCategory] = Field(min_length=1)
    extras: BoardExtras
    #: board name -> ops menu_item name, for the doctor's drift report only.
    aliases: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _compiles(self) -> MenuBoard:
        compile_board(self)  # every price/size/slug error surfaces at load time
        return self


def _item_sizes(cat: BoardCategory, item: BoardItem) -> list[SizeOut]:
    where = f"category {cat.slug!r}, item {item.n!r}"
    tokens = item.p.split()
    if len(tokens) != len(cat.sizes):
        raise ValueError(
            f"{where}: {len(tokens)} price(s) {item.p!r} for {len(cat.sizes)} size column(s) "
            f"{cat.sizes}"
        )
    if len(set(cat.sizes)) != len(cat.sizes):
        raise ValueError(f"category {cat.slug!r}: duplicate size column in {cat.sizes}")
    out: list[SizeOut] = []
    for code, tok in zip(cat.sizes, tokens, strict=True):
        if tok == "-":
            continue  # size not offered
        try:
            pence = pounds_to_pence(tok)
        except ValueError as exc:
            raise ValueError(f"{where}, size {code}: {exc}") from exc
        out.append(SizeOut(code=code, label=SIZE_LABEL[code], price_pence=pence))
    if not out:
        raise ValueError(f"{where}: every size is '-', so nothing is on sale")
    return sorted(out, key=lambda s: SIZE_ORDER[s.code])


def compile_board(board: MenuBoard) -> tuple[list[MenuCategoryOut], ExtrasOut]:
    cat_slugs = [c.slug for c in board.category]
    dupes = sorted({s for s in cat_slugs if cat_slugs.count(s) > 1})
    if dupes:
        raise ValueError(f"duplicate category slug(s): {dupes}")

    # Item ids: slug of the name; on a collision, BOTH get the category suffix so
    # neither id depends on which one happens to come first in the file.
    base_count: dict[str, int] = {}
    for c in board.category:
        for i in c.items:
            base_count[slugify(i.n)] = base_count.get(slugify(i.n), 0) + 1

    seen: dict[str, str] = {}
    categories: list[MenuCategoryOut] = []
    for c in board.category:
        items: list[MenuItemOut] = []
        for i in c.items:
            base = slugify(i.n)
            if not base:
                raise ValueError(f"category {c.slug!r}, item {i.n!r}: name has no sluggable text")
            item_id = base if base_count[base] == 1 else f"{base}-{c.slug}"
            if item_id in seen:
                raise ValueError(
                    f"category {c.slug!r}, item {i.n!r}: duplicate item id {item_id!r} "
                    f"(also {seen[item_id]})"
                )
            seen[item_id] = f"category {c.slug!r}, item {i.n!r}"
            items.append(
                MenuItemOut(
                    id=item_id,
                    name=i.n,
                    description=i.description,
                    note=i.note,
                    signature=i.signature,
                    seasonal=SeasonalOut(name=c.seasonal) if c.seasonal else None,
                    sizes=_item_sizes(c, i),
                )
            )
        categories.append(MenuCategoryOut(slug=c.slug, name=c.name, blurb=c.blurb, items=items))

    extras = ExtrasOut(
        title=board.extras.title,
        items=[ExtraOut(name=e.n, price_text=e.p) for e in board.extras.items],
    )
    return categories, extras


def load_board(path: Path = BOARD_PATH) -> MenuBoard:
    """Parse and fully validate. Raises pydantic.ValidationError naming the
    category and item on any bad price, size count or duplicate id."""
    with path.open("rb") as fh:
        return MenuBoard.model_validate(tomllib.load(fh))


def build_menu(path: Path = BOARD_PATH) -> MenuOut:
    categories, extras = compile_board(load_board(path))
    return MenuOut(generated_at=datetime.now(UTC), categories=categories, extras=extras)


_cache_lock = threading.Lock()
_cache: tuple[float, int, MenuOut] | None = None  # (built_at monotonic, file mtime_ns, menu)


def cached_menu() -> MenuOut:
    """Rebuilt when 60 s old or when the file's mtime changes, whichever first.
    If an edit breaks the file, the error surfaces (500 + log) rather than
    silently serving a stale menu."""
    global _cache
    with _cache_lock:
        now = time.monotonic()
        mtime = BOARD_PATH.stat().st_mtime_ns
        if _cache is not None and now - _cache[0] < CACHE_SECONDS and _cache[1] == mtime:
            return _cache[2]
        menu = build_menu()
        _cache = (now, mtime, menu)
        return menu
