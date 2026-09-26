"""Two settings the Stock drawer writes: an ingredient's tier, and its reorder floor.

Neither is a stock movement and neither orders anything. Both sit next to invariant 2 --
*auto-order eligibility is earned through drift history, never set manually* -- and both
are written so that neither can be used as a way round it.

## Tier (spec C1, DATA-MODEL 3.4)

Tier A is the auto-ordered core, and CLAUDE.md 4.7 says membership is **earned, not
assigned**. So:

* Moving **down** (A->B, A->C, B->C) and C->B are free: a human deciding an ingredient
  deserves less automation is never a risk.
* Moving **up to A** is refused unless the ingredient's own drift history would clear the
  gate (`domain.tiers.would_clear_gate`: two consecutive counts under 10%). That is the
  same evidence the gate itself demands, so "Promote to A" can only confirm what the
  counts already show.
* **`par_level.auto_order_enabled` is never touched.** Promotion makes an ingredient
  *eligible*; the gate grants the flag at the next count, through
  `SqlParLevelRepository.apply_gate_decision`, which is the only writer allowed.
* Every move writes an `ingredient_tier_change` row -- who, why, and what the gate said at
  the time -- in the same transaction as the tier itself.

Moving into A or B turns on `tracking_enabled` (the calculated tiers are the tracked ones:
ordering and the stock screen both read `list_tracked`), and moving to C turns it off
(tier C is a yes/no checklist, never calculated).

## Reorder floor (spec 4.1 "Reorder at")

`par_level.min_qty` is the floor `domain/ordering.size_line` raises an order to. Setting
it records who and when, refuses a floor above the ceiling `max_qty` (the order would be
cut back by the ceiling anyway and the par level is then self-contradictory), and -- again
-- never touches `auto_order_enabled`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import Ingredient, IngredientTierChange, ParLevel, Tier, Unit
from cafeops.db.repositories.drift import SqlDriftRepository
from cafeops.domain.tiers import clean_streak, would_clear_gate

__all__ = [
    "ParFloorChange",
    "SettingRefused",
    "TierChange",
    "change_tier",
    "set_par_floor",
    "tier_gate_evidence",
]

_REASON_MAX = 2000


class SettingRefused(ValueError):
    """Nothing changed, and the message says why. Shown verbatim."""


@dataclass(frozen=True, slots=True)
class GateEvidence:
    """What the drift history says about promotion. Read-only."""

    would_clear_gate: bool
    clean_streak: int
    required_streak: int
    recent_drift_pcts: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class TierChange:
    ingredient_id: int
    ingredient_name: str
    tier_before: Tier
    tier_after: Tier
    would_clear_gate: bool
    clean_streak: int
    required_streak: int
    auto_order_enabled: bool
    tracking_enabled: bool
    change_id: int
    note: str


@dataclass(frozen=True, slots=True)
class ParFloorChange:
    ingredient_id: int
    ingredient_name: str
    min_qty_before: Decimal
    min_qty_after: Decimal
    max_qty: Decimal
    auto_order_enabled: bool
    set_by: str
    set_at: datetime


def tier_gate_evidence(session: Session, ingredient_id: int) -> GateEvidence:
    """The promotion evidence: the same numbers the gate itself reads."""
    required = settings.drift_consecutive_counts_required
    recent = SqlDriftRepository(session).recent_drift_pcts(ingredient_id, limit=max(required, 8))
    eligible = settings.drift_auto_order_max_pct
    return GateEvidence(
        would_clear_gate=would_clear_gate(
            recent_drift_pcts=recent,
            required_consecutive=required,
            eligible_max_pct=eligible,
        ),
        clean_streak=clean_streak(recent, eligible_max_pct=eligible),
        required_streak=required,
        recent_drift_pcts=tuple(recent),
    )


def change_tier(
    session: Session,
    *,
    ingredient_id: int,
    tier: Tier,
    changed_by: str,
    reason: str,
    at: datetime | None = None,
) -> TierChange:
    """Move an ingredient between tiers. Promotion to A only on earned evidence."""
    if not changed_by.strip():
        raise SettingRefused("changed_by is required: a tier move is a decision somebody made")
    clean_reason = reason.strip()
    if not clean_reason:
        raise SettingRefused(
            "say why: a tier decides how an ingredient is ordered, and the reason is what "
            "the next person reads"
        )
    at = at or datetime.now(UTC)

    row = session.get(Ingredient, ingredient_id)
    if row is None:
        raise LookupError(f"ingredient {ingredient_id} not found")
    if row.retired_at is not None:
        raise SettingRefused(f"{row.name} is retired; bring it back before changing its tier")
    before = row.tier
    if before is tier:
        raise SettingRefused(f"{row.name} is already tier {tier.value}")

    evidence = tier_gate_evidence(session, ingredient_id)
    if tier is Tier.A:
        if before is Tier.C:
            raise SettingRefused(
                f"{row.name} is a tier C checklist item, so it has no counts to earn tier A "
                "with. Move it to B, count it, and promote it once two counts in a row come "
                "in under 10%."
            )
        if not evidence.would_clear_gate:
            raise SettingRefused(
                f"{row.name} has not earned tier A: it needs {evidence.required_streak} "
                f"counts in a row under {settings.drift_auto_order_max_pct:g}% drift and has "
                f"{evidence.clean_streak}. Tier A is earned through counts, never assigned "
                "(invariant 2). Count it again and promote it when it clears."
            )

    row.tier = tier
    row.tracking_enabled = tier is not Tier.C
    change = IngredientTierChange(
        ingredient_id=ingredient_id,
        tier_before=before,
        tier_after=tier,
        changed_at=at,
        changed_by=changed_by.strip()[:120],
        reason=clean_reason[:_REASON_MAX],
        would_clear_gate=evidence.would_clear_gate,
    )
    session.add(change)
    session.flush()

    par = session.scalar(select(ParLevel).where(ParLevel.ingredient_id == ingredient_id))
    auto_on = bool(par and par.auto_order_enabled)
    if tier is Tier.A:
        note = (
            "Promoted to tier A on its count history. Auto-ordering is NOT switched on "
            "here: the gate grants it at the next count, and every order is still "
            "confirmed in Telegram."
        )
    elif auto_on:
        note = (
            f"Moved to tier {tier.value}. Auto-ordering is still recorded as on; the gate "
            "turns it off at the next count, because only tier A can hold it. Nothing here "
            "flips it by hand (invariant 2)."
        )
    elif tier is Tier.C:
        note = "Moved to tier C: a yes/no checklist item, no longer worked out from sales."
    else:
        note = f"Moved to tier {tier.value}: worked out from sales, always reviewed."
    return TierChange(
        ingredient_id=ingredient_id,
        ingredient_name=row.name,
        tier_before=before,
        tier_after=tier,
        would_clear_gate=evidence.would_clear_gate,
        clean_streak=evidence.clean_streak,
        required_streak=evidence.required_streak,
        auto_order_enabled=auto_on,
        tracking_enabled=row.tracking_enabled,
        change_id=change.id,
        note=note,
    )


def set_par_floor(
    session: Session,
    *,
    ingredient_id: int,
    min_qty: Decimal,
    changed_by: str,
    at: datetime | None = None,
) -> ParFloorChange:
    """Set "Reorder at": `par_level.min_qty`. Never touches `auto_order_enabled`."""
    if isinstance(min_qty, float):
        raise TypeError("min_qty must be Decimal, not float (invariant 11)")
    if not changed_by.strip():
        raise SettingRefused("changed_by is required: a reorder level is a decision somebody made")
    if min_qty < 0:
        raise SettingRefused(f"a reorder level cannot be negative, got {min_qty}")
    at = at or datetime.now(UTC)

    ingredient = session.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise LookupError(f"ingredient {ingredient_id} not found")
    if ingredient.tier is Tier.C:
        raise SettingRefused(
            f"{ingredient.name} is tier C: a checklist item has no reorder level, because "
            "nothing works out how much of it is left"
        )
    if ingredient.unit is Unit.EACH and min_qty != min_qty.to_integral_value():
        raise SettingRefused(
            f"{ingredient.name} is counted in whole units: {min_qty} is not a whole number"
        )
    par = session.scalar(select(ParLevel).where(ParLevel.ingredient_id == ingredient_id))
    if par is None:
        raise SettingRefused(
            f"{ingredient.name} has no par level, so there is no ceiling to set a floor "
            "under. It is set up with the ingredient's par level, not here."
        )
    if min_qty > par.max_qty:
        raise SettingRefused(
            f"{min_qty} is above the par ceiling of {par.max_qty} "
            f"{ingredient.unit.value}: an order raised to that floor would be cut back by "
            "the ceiling, so the level would never take effect"
        )
    before = par.min_qty
    par.min_qty = min_qty
    par.min_qty_set_by = changed_by.strip()[:120]
    par.min_qty_set_at = at
    session.flush()
    return ParFloorChange(
        ingredient_id=ingredient_id,
        ingredient_name=ingredient.name,
        min_qty_before=before,
        min_qty_after=min_qty,
        max_qty=par.max_qty,
        auto_order_enabled=par.auto_order_enabled,
        set_by=par.min_qty_set_by,
        set_at=at,
    )
