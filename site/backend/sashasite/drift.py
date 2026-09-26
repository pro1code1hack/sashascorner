"""Menu drift: the published board (menu_board.toml) vs the ops ``menu_item`` table.

A report only. Ops is read with a Core ``text()`` select and never written. The
board is what the website serves until the ops menu has categories (see
menu_source.py); the ops rows are what the POS/costing side believes. The two are
expected to converge, and this is the list of where they have not.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import text

from sashasite.db import get_engine
from sashasite.menu import MenuBoard, compile_board

_OPS_SQL = text(
    "SELECT name, size_code, price_pence FROM menu_item WHERE active = 1 ORDER BY name, id"
)
_TRAILING = (" (slice)", " smoothie")


def normalise(name: str) -> str:
    s = name.casefold().strip()
    for suffix in _TRAILING:
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    s = s.replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())


def _ops_code(raw: str | None) -> str:
    code = (raw or "One").strip()
    return "One" if code.upper() == "ONE" else code.upper()


@dataclass
class DriftReport:
    board_items: int = 0
    ops_items: int = 0
    matched: int = 0
    #: (board name, ops name, size, board pence | None, ops pence | None)
    price_mismatches: list[tuple[str, str, str, int | None, int | None]] = field(
        default_factory=list
    )
    board_only: list[str] = field(default_factory=list)
    ops_only: list[str] = field(default_factory=list)


def menu_drift(board: MenuBoard) -> DriftReport:
    categories, _extras = compile_board(board)

    ops: dict[str, dict[str, int]] = defaultdict(dict)  # ops name -> size -> pence
    with get_engine().connect() as conn:
        for r in conn.execute(_OPS_SQL):
            ops[r.name].setdefault(_ops_code(r.size_code), int(r.price_pence))
    ops_by_norm: dict[str, str] = {normalise(n): n for n in ops}
    alias = {normalise(k): v for k, v in board.aliases.items()}

    rep = DriftReport(ops_items=len(ops))
    used_ops: set[str] = set()
    for cat in categories:
        for item in cat.items:
            rep.board_items += 1
            key = normalise(item.name)
            ops_name = alias.get(key) or ops_by_norm.get(key)
            if ops_name is None or ops_name not in ops:
                rep.board_only.append(f"{item.name}  [{cat.slug}]")
                continue
            rep.matched += 1
            used_ops.add(ops_name)
            board_sizes = {s.code: s.price_pence for s in item.sizes}
            ops_sizes = dict(ops[ops_name])
            # Ops files single-size items under 'M' (legacy workbook). When both
            # sides have exactly one size, they are the same size.
            if len(board_sizes) == 1 and len(ops_sizes) == 1:
                ops_sizes = {next(iter(board_sizes)): next(iter(ops_sizes.values()))}
            for code in sorted({*board_sizes, *ops_sizes}, key=_size_key):
                b, o = board_sizes.get(code), ops_sizes.get(code)
                if b != o:
                    rep.price_mismatches.append((item.name, ops_name, code, b, o))
    rep.ops_only = sorted(n for n in ops if n not in used_ops)
    return rep


def _size_key(code: str) -> int:
    return {"S": 0, "M": 1, "XL": 2, "One": 3}.get(code, 9)


def _money(p: int | None) -> str:
    return "—" if p is None else f"£{p // 100}.{p % 100:02d}"


def format_report(rep: DriftReport, limit: int | None = None) -> list[str]:
    def cap(rows: list[str]) -> list[str]:
        if limit is None or len(rows) <= limit:
            return rows
        return [*rows[:limit], f"    … and {len(rows) - limit} more"]

    mism = [
        f"    {b} {size}: board {_money(bp)}  ops {_money(op)}"
        + ("" if b.casefold() == o.casefold() else f"   (ops: {o})")
        for b, o, size, bp, op in rep.price_mismatches
    ]
    out = [
        f"menu drift: board {rep.board_items} items, ops {rep.ops_items} names, "
        f"{rep.matched} matched",
        f"  price mismatches: {len(rep.price_mismatches)} (size-level)",
        f"  board items with no ops row: {len(rep.board_only)}",
        f"  ops items with no board row: {len(rep.ops_only)}",
        "  -- price mismatches (board vs ops) --",
        *cap(mism),
        "  -- on the board, not in ops --",
        *cap([f"    {n}" for n in rep.board_only]),
        "  -- in ops, not on the board --",
        *cap([f"    {n}" for n in rep.ops_only]),
    ]
    return out
