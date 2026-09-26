"""Import the café's published menu boards into the ops menu.

The TV menu boards (transcribed for the website at
`site/backend/config/menu_board.toml`) are what customers see. The ops menu came
from the legacy workbook and has drifted from them: products missing, prices
different, and no categories at all. This brings the ops menu to the boards so it
can drive the public website (owner decision, 2026-09-26).

Rules, agreed with the back-office redesign:
- Dry run by default. `plan()` reads only and writes nothing.
- Every write goes through `services.menu_catalog` (dated prices, cost cache kept
  in sync). Nothing here inserts rows itself.
- Price conflicts are the owner's call. The plan lists each one with the board
  price, the current price and where the current one came from. `apply()` refuses
  to touch prices unless told `prices="board"` or `prices="keep"`.
- Products on the ops menu but not on the boards are listed and left alone.
  Deactivating them is a separate decision.
- Idempotent: once applied, a second plan is empty.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from cafeops.db.models.enums import MenuKind, SizeCode

ACTOR = "board-import"
FOOD_SLUGS = frozenset({"breakfast", "lunch", "waffles", "cakes"})
_SIZE = {"S": SizeCode.S, "M": SizeCode.M, "XL": SizeCode.XL, "One": None}


@dataclass(frozen=True)
class BoardItem:
    category: str
    name: str
    prices: dict[str, int]  # size column ("S"/"M"/"XL"/"One") -> pence


@dataclass(frozen=True)
class BoardCategory:
    slug: str
    name: str
    kind: MenuKind


@dataclass(frozen=True)
class Conflict:
    name: str
    size: str
    menu_item_id: int
    board_pence: int
    current_pence: int
    current_source: str


@dataclass
class Plan:
    migrated: bool
    categories_new: list[BoardCategory] = field(default_factory=list)
    categories_existing: list[str] = field(default_factory=list)
    assign: list[tuple[int, str, str | None, str]] = field(default_factory=list)
    """(anchor menu_item_id, ops name, current category, board category)"""
    create: list[BoardItem] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    sizes_missing: list[tuple[str, str]] = field(default_factory=list)
    """(ops name, board size) the ops product lacks. Reported, never added."""
    ops_only: list[str] = field(default_factory=list)
    inactive_matches: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.categories_new or self.assign or self.create or self.conflicts)


# --------------------------------------------------------------------------
# Reading the boards
# --------------------------------------------------------------------------


def _pence(text: str) -> int:
    value = Decimal(text) * 100
    if value != value.to_integral_value():
        raise ValueError(f"{text!r} is not a whole number of pence")
    return int(value)


def read_board(path: Path) -> tuple[list[BoardCategory], list[BoardItem], dict[str, str]]:
    doc = tomllib.loads(path.read_text(encoding="utf-8"))
    cats: list[BoardCategory] = []
    items: list[BoardItem] = []
    for c in doc["category"]:
        kind = MenuKind.FOOD if c["slug"] in FOOD_SLUGS else MenuKind.DRINKS
        cats.append(BoardCategory(slug=c["slug"], name=c["name"], kind=kind))
        sizes: list[str] = c["sizes"]
        for it in c["items"]:
            parts = it["p"].split()
            if len(parts) != len(sizes):
                raise ValueError(f"{c['name']} / {it['n']}: {len(parts)} prices for {sizes}")
            prices = {s: _pence(p) for s, p in zip(sizes, parts, strict=True) if p != "-"}
            items.append(BoardItem(category=c["name"], name=it["n"], prices=prices))
    aliases = {str(k): str(v) for k, v in doc.get("aliases", {}).items()}
    return cats, items, aliases


def _norm(name: str) -> str:
    s = name.lower().replace("&", " and ")
    s = re.sub(r"\((slice)\)", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(smoothie)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------
# Planning (reads only)
# --------------------------------------------------------------------------


def _migrated(session: Session) -> bool:
    tables = set(inspect(session.get_bind()).get_table_names())
    return {"menu_category", "menu_item_price"} <= tables


@dataclass(frozen=True)
class OpsRow:
    """The columns the plan needs, read with plain SQL so the dry run also works on a
    database the back-office migration hasn't reached yet (the ORM model already
    expects the new columns)."""

    id: int
    name: str
    size_code: str | None
    price_pence: int
    active: bool
    category: str | None


def _ops_rows(session: Session) -> list[OpsRow]:
    result = session.execute(
        text("SELECT id, name, size_code, price_pence, active, category FROM menu_item")
    )
    return [
        OpsRow(
            id=r.id,
            name=r.name,
            size_code=r.size_code,
            price_pence=r.price_pence,
            active=bool(r.active),
            category=r.category,
        )
        for r in result
    ]


def _size_key(row: OpsRow) -> str:
    code = row.size_code
    return "One" if code in (None, "", "ONE", "One") else str(code)


def _current_sources(session: Session, ids: list[int], migrated: bool) -> dict[int, str]:
    if not migrated or not ids:
        return {}
    from cafeops.db.models import MenuItemPrice

    rows = session.scalars(
        select(MenuItemPrice).where(
            MenuItemPrice.menu_item_id.in_(ids), MenuItemPrice.effective_to.is_(None)
        )
    )
    out: dict[int, str] = {}
    for r in rows:
        who = f" by {r.set_by}" if r.set_by else ""
        out[r.menu_item_id] = f"{r.source.value}{who}"
    return out


def plan(session: Session, board_path: Path) -> Plan:
    cats, items, aliases = read_board(board_path)
    migrated = _migrated(session)
    result = Plan(migrated=migrated)

    existing_cats: set[str] = set()
    if migrated:
        from cafeops.db.models import MenuCategory

        existing_cats = set(session.scalars(select(MenuCategory.name)))
    for c in cats:
        if c.name in existing_cats:
            result.categories_existing.append(c.name)
        else:
            result.categories_new.append(c)

    rows = _ops_rows(session)
    groups: dict[str, list[OpsRow]] = {}
    for r in rows:
        groups.setdefault(r.name, []).append(r)
    by_norm: dict[str, str] = {}
    for name in groups:
        by_norm.setdefault(_norm(name), name)

    matched: set[str] = set()
    pairs: list[tuple[BoardItem, list[OpsRow]]] = []
    for it in items:
        ops_name = aliases.get(it.name) or by_norm.get(_norm(it.name))
        if ops_name is None or ops_name not in groups:
            result.create.append(it)
            continue
        active = [r for r in groups[ops_name] if r.active]
        if not active:
            result.inactive_matches.append(ops_name)
            continue
        matched.add(ops_name)
        pairs.append((it, active))

    sources = _current_sources(session, [r.id for _, g in pairs for r in g], migrated)
    for it, active in pairs:
        anchor = active[0]
        if anchor.category != it.category:
            result.assign.append((anchor.id, anchor.name, anchor.category, it.category))
        by_size = {_size_key(r): r for r in active}
        # Legacy single-size items sit under "M"; a one-size board item matches them.
        if list(it.prices) == ["One"] and len(active) == 1:
            by_size = {"One": active[0]}
        for size, pence in it.prices.items():
            row = by_size.get(size)
            if row is None:
                result.sizes_missing.append((anchor.name, size))
            elif row.price_pence != pence:
                result.conflicts.append(
                    Conflict(
                        name=anchor.name,
                        size=size,
                        menu_item_id=row.id,
                        board_pence=pence,
                        current_pence=row.price_pence,
                        current_source=sources.get(row.id, "unknown (price history not migrated)"),
                    )
                )

    result.ops_only = sorted(
        n for n, g in groups.items() if n not in matched and any(r.active for r in g)
    )
    return result


def _gbp(p: int) -> str:
    return f"£{p // 100}.{p % 100:02d}"


def report_lines(p: Plan) -> list[str]:
    out: list[str] = []
    if not p.migrated:
        out.append(
            "! The back-office migration (menu_category, menu_item_price) is not applied to "
            "this database. This plan is read-only; --commit is refused until it is."
        )
    out.append(
        f"Categories: {len(p.categories_new)} to create, {len(p.categories_existing)} already there"
    )
    for c in p.categories_new:
        out.append(f"  + {c.name}  [{c.kind.value}]")
    out.append(f"Category assignments: {len(p.assign)}")
    for _id, name, cur, new in p.assign:
        out.append(f"  ~ {name}: {cur or '(none)'} -> {new}")
    out.append(f"New products from the boards: {len(p.create)}")
    for it in p.create:
        sizes = ", ".join(f"{s} {_gbp(v)}" for s, v in it.prices.items())
        out.append(f"  + {it.name}  [{it.category}]  {sizes}")
    out.append(f"Price conflicts (owner decides: --prices board | keep): {len(p.conflicts)}")
    for c in p.conflicts:
        out.append(
            f"  ! {c.name} {c.size}: board {_gbp(c.board_pence)}, now {_gbp(c.current_pence)}"
            f" (from {c.current_source})"
        )
    if p.sizes_missing:
        out.append(
            f"Sizes on the boards that ops lacks (not added; use add-size): {len(p.sizes_missing)}"
        )
        for name, size in p.sizes_missing:
            out.append(f"  ? {name} {size}")
    if p.inactive_matches:
        off = ", ".join(p.inactive_matches)
        out.append(f"Matched only switched-off products (left off): {off}")
    out.append(f"On the ops menu but not on the boards (left alone): {len(p.ops_only)}")
    for name in p.ops_only:
        out.append(f"  - {name}")
    return out


# --------------------------------------------------------------------------
# Applying (writes, through the services only)
# --------------------------------------------------------------------------


def apply(
    session: Session, board_path: Path, *, prices: Literal["board", "keep"] | None
) -> list[str]:
    from cafeops.services.menu_catalog import (
        SizeIn,
        apply_prices,
        create_category,
        create_menu_item,
        update_group,
    )

    p = plan(session, board_path)
    if not p.migrated:
        raise RuntimeError("the back-office migration is not applied; run alembic upgrade first")
    if p.conflicts and prices is None:
        raise ValueError(
            f"{len(p.conflicts)} price conflicts need the owner's decision: "
            "pass --prices board (board wins) or --prices keep (current prices stay)"
        )
    done: list[str] = []
    for c in p.categories_new:  # in board order, so sort_order follows the boards
        create_category(session, name=c.name, kind=c.kind)
        done.append(f"created category {c.name}")
    for anchor_id, name, _cur, new in p.assign:
        update_group(session, anchor_id, actor=ACTOR, category=new, set_category=True)
        done.append(f"{name} -> {new}")
    for it in p.create:
        sizes = [SizeIn(size_code=_SIZE[s], price_pence=v) for s, v in it.prices.items()]
        create_menu_item(
            session, name=it.name, category=it.category, note=None, sizes=sizes, actor=ACTOR
        )
        done.append(f"created {it.name}")
    if p.conflicts and prices == "board":
        applied = apply_prices(
            session, [(c.menu_item_id, c.board_pence) for c in p.conflicts], actor=ACTOR
        )
        done.append(f"repriced {len(applied.repriced)} rows to the board prices")
    elif p.conflicts:
        done.append(f"kept current prices on {len(p.conflicts)} conflicting rows")
    return done
