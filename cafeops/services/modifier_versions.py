"""Swaps (modifiers), edited through `modifier_version`. Recipes spec C-6 / R21.

The design saved a swap's charge and behaviour straight away and globally. That is
wrong for this backend twice over: `modifier` was not effective-dated, so an edit
silently re-resolved history on any re-expansion (invariant 3), and nothing showed
what the edit would do first (spec 5.6).

Now a modifier's behaviour lives in dated `modifier_version` rows. An edit closes the
open version and opens a new one from now; the `modifier` row's own columns are a
cache of the open version, written in the same transaction; and the resolver reads the
version in force at each sale's `sold_at` (`SqlCompositionRepository.modifiers`).
Identity (`name`, `lightspeed_modifier_id`) never changes, because that is what sales
resolve against.

Modifiers target a ROLE, not a recipe (ARCHITECTURE 4.2), so there is no per-recipe
switch here (spec C-7): turning oat milk off for one drink is that drink's MILK slot's
`Swappable` flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import (
    DrinkTemplate,
    Ingredient,
    Modifier,
    ModifierVersion,
    RecipeChange,
    Sale,
    TemplateComponent,
)
from cafeops.domain.composition import gbp, qty_text
from cafeops.domain.types import ComponentRole, ModifierAction
from cafeops.services.edit_composition import require_not_retroactive

__all__ = [
    "ModifierChange",
    "ModifierPreview",
    "apply_modifier_change",
    "preview_modifier_change",
]

WINDOW_DAYS = 30


@dataclass(frozen=True, slots=True)
class ModifierChange:
    """The new behaviour. Any field left None keeps the current value."""

    action: ModifierAction | None = None
    target_role: ComponentRole | None = None
    set_ingredient: bool = False
    ingredient_id: int | None = None
    set_qty_delta: bool = False
    qty_delta: Decimal | None = None
    set_qty_multiplier: bool = False
    qty_multiplier: Decimal | None = None
    price_pence: int | None = None
    price_is_estimate: bool | None = None
    is_active: bool | None = None


@dataclass(frozen=True, slots=True)
class _State:
    action: ModifierAction
    target_role: ComponentRole
    ingredient_id: int | None
    qty_delta: Decimal | None
    qty_multiplier: Decimal | None
    price_pence: int
    price_is_estimate: bool | None
    is_active: bool


@dataclass(frozen=True, slots=True)
class ModifierPreview:
    modifier_id: int
    name: str
    diff: tuple[str, ...]
    warnings: tuple[str, ...]
    refusals: tuple[str, ...]
    sales_in_window: int
    revenue_delta_pence: int | None
    window_days: int
    templates_affected: tuple[str, ...]
    at: datetime


def _current(modifier: Modifier) -> _State:
    return _State(
        action=modifier.action,
        target_role=modifier.target_role,
        ingredient_id=modifier.ingredient_id,
        qty_delta=modifier.qty_delta,
        qty_multiplier=modifier.qty_multiplier,
        price_pence=modifier.price_pence,
        price_is_estimate=modifier.price_is_estimate,
        is_active=modifier.is_active,
    )


def _next(state: _State, change: ModifierChange) -> _State:
    return _State(
        action=change.action or state.action,
        target_role=change.target_role or state.target_role,
        ingredient_id=change.ingredient_id if change.set_ingredient else state.ingredient_id,
        qty_delta=change.qty_delta if change.set_qty_delta else state.qty_delta,
        qty_multiplier=change.qty_multiplier if change.set_qty_multiplier else state.qty_multiplier,
        price_pence=state.price_pence if change.price_pence is None else change.price_pence,
        price_is_estimate=(
            state.price_is_estimate
            if change.price_is_estimate is None
            else change.price_is_estimate
        ),
        is_active=state.is_active if change.is_active is None else change.is_active,
    )


def _assess(
    session: Session, modifier: Modifier, change: ModifierChange
) -> tuple[_State, _State, list[str], list[str], list[str], list[str]]:
    before = _current(modifier)
    after = _next(before, change)
    names = dict(session.execute(select(Ingredient.id, Ingredient.name)).tuples().all())

    def ing(i: int | None) -> str:
        return "nothing" if i is None else names.get(i, f"#{i}")

    diff: list[str] = []
    refusals: list[str] = []
    warnings: list[str] = []
    verb = {
        ModifierAction.SUBSTITUTE: "replaces",
        ModifierAction.ADD: "adds",
        ModifierAction.SCALE: "scales",
    }
    if after.action is not before.action or after.target_role is not before.target_role:
        diff.append(
            f"{modifier.name}: {verb[before.action]} {before.target_role.value.lower()} → "
            f"{verb[after.action]} {after.target_role.value.lower()}"
        )
    if after.ingredient_id != before.ingredient_id:
        diff.append(f"{modifier.name}: {ing(before.ingredient_id)} → {ing(after.ingredient_id)}")
    if after.qty_delta != before.qty_delta:
        diff.append(
            f"{modifier.name} qty: "
            f"{qty_text(before.qty_delta) if before.qty_delta is not None else 'same'} → "
            f"{qty_text(after.qty_delta) if after.qty_delta is not None else 'same'}"
        )
    if after.qty_multiplier != before.qty_multiplier:
        diff.append(
            f"{modifier.name} multiplier: "
            f"{qty_text(before.qty_multiplier) if before.qty_multiplier is not None else '1'} → "
            f"{qty_text(after.qty_multiplier) if after.qty_multiplier is not None else '1'}"
        )
    if after.price_pence != before.price_pence:
        diff.append(f"{modifier.name} charge {gbp(before.price_pence)} → {gbp(after.price_pence)}")
    if after.price_is_estimate != before.price_is_estimate:
        diff.append(
            f"{modifier.name} charge marked as a guess"
            if after.price_is_estimate
            else f"{modifier.name} charge confirmed"
        )
    if after.is_active != before.is_active:
        diff.append(
            f"{modifier.name} offered again" if after.is_active else f"{modifier.name} withdrawn"
        )

    if after.price_pence < 0:
        refusals.append("a charge cannot be negative")
    if after.ingredient_id is not None and after.ingredient_id not in names:
        refusals.append(f"ingredient #{after.ingredient_id} does not exist")
    if after.action is ModifierAction.SUBSTITUTE and after.ingredient_id is None:
        refusals.append("a swap that replaces something needs the ingredient it swaps in")
    if after.action is ModifierAction.ADD and (
        after.ingredient_id is None or after.qty_delta is None
    ):
        refusals.append("a swap that adds something needs both an ingredient and a quantity")
    if after.action is ModifierAction.SCALE and after.qty_multiplier is None:
        refusals.append("a swap that scales a slot needs a multiplier")
    if after.qty_delta is not None and after.qty_delta <= 0:
        refusals.append("an added quantity must be more than 0")

    templates = sorted(
        {
            name
            for name, substitutable in session.execute(
                select(DrinkTemplate.name, TemplateComponent.is_substitutable)
                .join(TemplateComponent, TemplateComponent.template_id == DrinkTemplate.id)
                .where(
                    TemplateComponent.role == after.target_role,
                    TemplateComponent.effective_to.is_(None),
                )
            )
            if after.action is not ModifierAction.SUBSTITUTE or substitutable
        }
    )
    if after.action is ModifierAction.SUBSTITUTE:
        blocked = sorted(
            {
                name
                for name, substitutable in session.execute(
                    select(DrinkTemplate.name, TemplateComponent.is_substitutable)
                    .join(TemplateComponent, TemplateComponent.template_id == DrinkTemplate.id)
                    .where(
                        TemplateComponent.role == after.target_role,
                        TemplateComponent.effective_to.is_(None),
                    )
                )
                if not substitutable
            }
            - set(templates)
        )
        if blocked:
            warnings.append(
                f"The {after.target_role.value.lower()} slot of {', '.join(blocked)} cannot be "
                "swapped, so a sale of those with this swap is refused at stock expansion."
            )
    if after.price_is_estimate:
        warnings.append("The charge is a guess, so it shows in italics until confirmed.")
    return before, after, diff, refusals, warnings, templates


def _sales_in_window(session: Session, modifier_id: int, at: datetime, window_days: int) -> int:
    tz = settings.tz
    until = at.astimezone(tz).date()
    since = until - timedelta(days=window_days - 1)
    start = datetime.combine(since, time.min, tzinfo=tz)
    count = 0
    for applied, qty in session.execute(
        select(Sale.applied_modifiers, Sale.qty).where(
            Sale.voided.is_(False), Sale.sold_at >= start, Sale.sold_at <= at
        )
    ):
        if modifier_id in [int(x) for x in (applied or [])]:
            count += int(qty)
    return count


def preview_modifier_change(
    session: Session, modifier_id: int, change: ModifierChange, *, window_days: int = WINDOW_DAYS
) -> ModifierPreview:
    """What editing a swap would do. WRITES NOTHING."""
    at = datetime.now(UTC)
    modifier = session.get(Modifier, modifier_id)
    if modifier is None:
        raise LookupError(f"swap {modifier_id} not found")
    before, after, diff, refusals, warnings, templates = _assess(session, modifier, change)
    sales = _sales_in_window(session, modifier_id, at, window_days)
    revenue = (after.price_pence - before.price_pence) * sales if diff else None
    if sales == 0 and after.price_pence != before.price_pence:
        warnings.append(
            f"No sale in the last {window_days} days carried this swap. Lightspeed only sends "
            "swaps on its real-time feed (ARCHITECTURE 8K), so the till may be ringing it "
            "unseen: the takings figure is unknown, not zero."
        )
        revenue = None
    return ModifierPreview(
        modifier_id=modifier_id,
        name=modifier.name,
        diff=tuple(diff),
        warnings=tuple(warnings),
        refusals=tuple(refusals),
        sales_in_window=sales,
        revenue_delta_pence=revenue,
        window_days=window_days,
        templates_affected=tuple(templates),
        at=at,
    )


def apply_modifier_change(
    session: Session,
    modifier_id: int,
    change: ModifierChange,
    *,
    actor: str,
    effective_from: datetime | None = None,
) -> tuple[int, datetime, tuple[str, ...]]:
    """Close the open version, open the new one, refresh the cache. Commits itself.

    Returns (new version id, effective_from, diff lines).
    """
    at = require_not_retroactive(effective_from or datetime.now(UTC))
    actor = actor.strip()[:120]
    if not actor:
        raise ValueError("say who is making this change (the operator name)")
    modifier = session.get(Modifier, modifier_id)
    if modifier is None:
        raise LookupError(f"swap {modifier_id} not found")
    _before, after, diff, refusals, _warnings, templates = _assess(session, modifier, change)
    if refusals:
        raise ValueError("Nothing was applied: " + "; ".join(refusals) + ".")
    if not diff:
        raise ValueError("there is nothing to apply: the swap already behaves like this")
    try:
        open_version = session.scalar(
            select(ModifierVersion).where(
                ModifierVersion.modifier_id == modifier_id, ModifierVersion.effective_to.is_(None)
            )
        )
        if open_version is not None:
            if open_version.effective_from >= at:
                raise ValueError("this swap was already changed at this instant; try again")
            open_version.effective_to = at
            session.flush()
        version = ModifierVersion(
            modifier_id=modifier_id,
            action=after.action,
            target_role=after.target_role,
            ingredient_id=after.ingredient_id,
            qty_delta=after.qty_delta,
            qty_multiplier=after.qty_multiplier,
            price_pence=after.price_pence,
            price_is_estimate=after.price_is_estimate,
            is_active=after.is_active,
            effective_from=at,
            changed_by=actor,
        )
        session.add(version)
        modifier.action = after.action
        modifier.target_role = after.target_role
        modifier.ingredient_id = after.ingredient_id
        modifier.qty_delta = after.qty_delta
        modifier.qty_multiplier = after.qty_multiplier
        modifier.price_pence = after.price_pence
        modifier.price_is_estimate = after.price_is_estimate
        modifier.is_active = after.is_active
        session.flush()
        # One history line on every recipe the swap applies to, so each recipe's
        # history says what changed for it and who changed it.
        template_ids = session.scalars(
            select(DrinkTemplate.id).where(DrinkTemplate.name.in_(templates))
        ).all()
        for template_id in template_ids:
            session.add(
                RecipeChange(
                    template_id=template_id,
                    change_kind="modifier",
                    effective_from=at,
                    actor=actor,
                    summary=" · ".join(diff[:3]),
                    lines=list(diff),
                )
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return version.id, at, tuple(diff)
