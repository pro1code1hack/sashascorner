"""Theoretical on-hand. Spec 5.1.

PHASE 0 SCOPE NOTE: this module belongs to Agent C (stock engine). Phase 0
implements only 5.1's formula, because `cafeops stock --as-of today` in the first
deliverable cannot exist without it. Drift (5.2) belongs in domain/drift.py and
tier gating in domain/tiers.py -- both Agent C's, both deliberately absent here.

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


def waste_factors_from(snapshots: Iterable[IngredientSnapshot]) -> dict[int, Decimal]:
    return {s.id: s.waste_factor for s in snapshots}
