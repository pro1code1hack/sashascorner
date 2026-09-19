"""Theoretical on-hand and the ledger entries that move it. Spec 5.1.

Phase 1 added the correction path (`reversal_movements`) alongside Phase 0's formula.
Drift lives in `domain/drift.py` and the auto-order gate in `domain/tiers.py`.

Two things this module deliberately does NOT do:

- **It writes no movement for a physical count.** A count is the source of truth and
  spec 5.1 re-anchors on it: the formula reads the latest count and sums only what
  happened after it, so a `COUNT_RESET` movement would be counted twice -- once as the
  new anchor and once as a delta on top of it. `MovementType.COUNT_RESET` exists for
  an explicit opening balance, not for reconciling a count.
- **It never amends a movement.** Invariant 9: the ledger is append-only, so undoing
  an expansion means appending equal-and-opposite `ADJUSTMENT` rows.

Pure: dataclasses in, dataclasses out. No SQLAlchemy, no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal

from cafeops.domain.types import (
    IngredientSnapshot,
    MovementSpec,
    MovementType,
    OnHand,
    ResolvedLine,
)

__all__ = [
    "apply_waste",
    "depletion_movements",
    "reversal_movements",
    "theoretical_on_hand",
    "waste_factors_from",
]


def theoretical_on_hand(
    *,
    ingredient_id: int,
    as_of: datetime,
    latest_count: tuple[Decimal, datetime] | None,
    movement_sum: Decimal,
    movement_count: int = 0,
) -> OnHand:
    """on_hand = latest count before `as_of` + signed sum of movements after it.

    `movement_sum` must already be restricted to the half-open window
    (count.counted_at, as_of] by the caller. The repository does that in SQL so
    this stays a pure statement of the spec's formula.

    With no count on record the count term is zero and the result is a bare
    movement sum. That is a genuinely weaker number, and `OnHand.has_count_basis`
    reports it as such so nothing prints it as though it were anchored.
    """
    if latest_count is None:
        base, counted_at = Decimal("0"), None
    else:
        base, counted_at = latest_count

    return OnHand(
        ingredient_id=ingredient_id,
        qty=base + movement_sum,
        as_of=as_of,
        basis_count_qty=None if latest_count is None else base,
        basis_counted_at=counted_at,
        movement_sum=movement_sum,
        movement_count=movement_count,
    )


def apply_waste(qty: Decimal, waste_factor: Decimal) -> Decimal:
    """qty * (1 + waste_factor).

    INVARIANT 5: this belongs to stock depletion ONLY. Menu cost is the recipe
    without waste. Two different numbers, both needed, never conflated -- so this
    function exists separately rather than being folded into resolution.
    """
    return qty * (Decimal("1") + waste_factor)


def depletion_movements(
    *,
    sale_id: int,
    sold_qty: Decimal,
    sold_at: datetime,
    lines: Sequence[ResolvedLine],
    waste_factors: dict[int, Decimal],
    tracked_ingredient_ids: Iterable[int] | None = None,
) -> tuple[MovementSpec, ...]:
    """Turn one sold line into SALE ledger entries, one per tracked ingredient.

    Quantities are negated: consumption reduces stock. A refund arrives with a
    negative `sold_qty` and therefore produces a POSITIVE movement, which is what
    nets a refunded sale back out of the ledger.
    """
    tracked = None if tracked_ingredient_ids is None else set(tracked_ingredient_ids)
    movements: list[MovementSpec] = []

    for line in lines:
        if tracked is not None and line.ingredient_id not in tracked:
            continue
        consumed = apply_waste(
            sold_qty * line.qty, waste_factors.get(line.ingredient_id, Decimal("0"))
        )
        if consumed == 0:
            continue
        movements.append(
            MovementSpec(
                ingredient_id=line.ingredient_id,
                type=MovementType.SALE,
                qty=-consumed,
                occurred_at=sold_at,
                ref_type="sale",
                ref_id=sale_id,
            )
        )
    return tuple(movements)


def reversal_movements(
    originals: Sequence[MovementSpec],
    *,
    occurred_at: datetime,
    note: str,
    ref_type: str = "reversal",
) -> tuple[MovementSpec, ...]:
    """Equal-and-opposite `ADJUSTMENT` rows that undo `originals`.

    INVARIANT 9: the ledger is append-only. A receipt voided after it was expanded, a
    double-expanded window, a recipe that was wrong when the sale resolved -- none of
    them may update or delete the rows already written. They append the inverse, and
    the sum comes out right while the history stays readable.

    One reversal per original rather than one per ingredient, so the correction can be
    traced back to the exact row it answers. `ref_id` is carried over from the original
    and `ref_type` is retyped (`sale` -> `sale_reversal` by convention at the call
    site) so a later run can tell a reversal from the thing it reversed and never
    reverse it twice.
    """
    return tuple(
        MovementSpec(
            ingredient_id=original.ingredient_id,
            type=MovementType.ADJUSTMENT,
            qty=-original.qty,
            occurred_at=occurred_at,
            ref_type=ref_type,
            ref_id=original.ref_id,
            note=note,
        )
        for original in originals
        if original.qty != 0
    )


def waste_factors_from(snapshots: Iterable[IngredientSnapshot]) -> dict[int, Decimal]:
    return {s.id: s.waste_factor for s in snapshots}
