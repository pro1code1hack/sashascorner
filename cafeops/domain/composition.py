"""Recipe resolution. Spec 4.3.

PHASE 0 SCOPE NOTE: this module belongs to Agent B (composition engine). Phase 0
implements `resolve_recipe` because it is the contract every other agent depends
on and because `seed --demo` and `stock --as-of` cannot exist without it. Agent B
owns hardening it and adding the impact preview and the cost cascade (spec 5.5).

Pure: dataclasses in, dataclasses out. No SQLAlchemy, no I/O. Effective dating is
resolved by the repository BEFORE this function runs -- a MenuItemSpec already
contains only the components and options in force at the resolve date. That split
is deliberate: the date arithmetic is a query concern, and keeping it out of here
is what makes resolution testable without a database.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from cafeops.domain.stock import apply_waste
from cafeops.domain.types import (
    ComponentRole,
    CostBreakdownLine,
    IngredientSnapshot,
    MenuItemSpec,
    ModifierAction,
    ModifierSpec,
    PriceSource,
    ResolvedLine,
    ResolvedRecipe,
    SubstitutionError,
    VariantOptionSpec,
)

__all__ = ["resolve_recipe"]

#: Spec 4.3 rule 3. Order matters: a SUBSTITUTE must land before a SCALE that
#: targets the same role, or the scale would multiply the ingredient it replaced.
_MODIFIER_ORDER: dict[ModifierAction, int] = {
    ModifierAction.SUBSTITUTE: 0,
    ModifierAction.SCALE: 1,
    ModifierAction.ADD: 2,
}


def resolve_recipe(
    item: MenuItemSpec,
    modifiers: Sequence[ModifierSpec],
    at: datetime,
    *,
    ingredients: dict[int, IngredientSnapshot] | None = None,
) -> ResolvedRecipe:
    """Resolve a menu item into concrete ingredient quantities and a cost breakdown.

    Template components (already narrowed to `at` and to the item's size), with
    variant options filling role-matched empty slots, then modifiers applied in
    the order SUBSTITUTE -> SCALE -> ADD.

    `lines` carry RECIPE quantities. `depletion_lines` carry the same quantities
    with each ingredient's waste factor applied. INVARIANT 5: waste affects stock
    depletion only, never menu cost -- so they are two separate values and no
    caller can reach for the wrong one by accident.
    """
    ingredients = ingredients or {}
    warnings: list[str] = []

    if item.manual_recipe:
        lines = [
            ResolvedLine(ingredient_id=ing_id, qty=qty, role=ComponentRole.BASE)
            for ing_id, qty in item.manual_lines
        ]
        if not lines:
            warnings.append(
                f"{item.name!r} is a manual-recipe item with no recipe lines at "
                f"{at.date().isoformat()}; it depletes nothing and has no cost"
            )
    else:
        lines = _resolve_template(item, warnings)
        lines = _apply_modifiers(lines, modifiers, item, warnings)

    # Drop anything that resolved to zero or nothing: a slot with no ingredient
    # and no option is not a requirement, it is an unfilled optional slot.
    lines = [line for line in lines if line.qty != 0]

    depletion = tuple(
        ResolvedLine(
            ingredient_id=line.ingredient_id,
            qty=apply_waste(
                line.qty,
                ingredients[line.ingredient_id].waste_factor
                if line.ingredient_id in ingredients
                else Decimal("0"),
            ),
            role=line.role,
            from_modifier_id=line.from_modifier_id,
            from_option_id=line.from_option_id,
        )
        for line in lines
    )

    return ResolvedRecipe(
        menu_item_id=item.menu_item_id,
        size_code=item.size_code,
        resolved_at=at,
        lines=tuple(lines),
        depletion_lines=depletion,
        cost_breakdown=_cost_breakdown(lines, ingredients),
        applied_modifier_ids=tuple(m.modifier_id for m in modifiers),
        warnings=tuple(warnings),
    )


# --------------------------------------------------------------------------


def _resolve_template(item: MenuItemSpec, warnings: list[str]) -> list[ResolvedLine]:
    """Rules 1 and 2: components at this size, with options filling empty slots."""
    options_by_role: dict[ComponentRole, list[VariantOptionSpec]] = {}
    for option in item.options:
        options_by_role.setdefault(option.role, []).append(option)

    lines: list[ResolvedLine] = []
    filled_roles: set[ComponentRole] = set()

    for component in item.components:
        if component.ingredient_id is not None:
            if component.qty is None:
                # A slot with no quantity at this size is normal and expected for
                # size-determined components: the 8oz cup slot has a quantity at S
                # and nothing at M or XL. Only warn when the slot is REQUIRED,
                # otherwise every resolution emits noise and real warnings get lost.
                if component.is_required:
                    warnings.append(
                        f"required component {component.component_id} "
                        f"({component.role.value}) has no quantity for size "
                        f"{item.size_code.value if item.size_code else '-'}"
                    )
                continue
            lines.append(
                ResolvedLine(
                    ingredient_id=component.ingredient_id,
                    qty=component.qty,
                    role=component.role,
                )
            )
            continue

        # Empty slot -- a variant option must fill it.
        candidates = options_by_role.get(component.role, [])
        if not candidates:
            if component.is_required:
                warnings.append(
                    f"required {component.role.value} slot has no option selected for {item.name!r}"
                )
            continue
        for opt in candidates:
            if opt.ingredient_id is None:
                continue
            # Rule 2: an option's own qty_by_size overrides the slot's.
            qty = opt.qty if opt.qty is not None else component.qty
            if qty is None:
                warnings.append(
                    f"option {opt.name!r} fills the {component.role.value} slot but "
                    "neither it nor the slot has a quantity for this size"
                )
                continue
            lines.append(
                ResolvedLine(
                    ingredient_id=opt.ingredient_id,
                    qty=qty,
                    role=component.role,
                    from_option_id=opt.option_id,
                )
            )
        filled_roles.add(component.role)

    # An option on an axis whose slot does not exist still contributes -- some
    # templates express a whole role through the axis alone.
    for role, candidates in options_by_role.items():
        if role in filled_roles:
            continue
        if any(c.role is role and c.ingredient_id is None for c in item.components):
            continue
        for opt in candidates:
            if opt.ingredient_id is None or opt.qty is None:
                continue
            lines.append(
                ResolvedLine(
                    ingredient_id=opt.ingredient_id,
                    qty=opt.qty,
                    role=role,
                    from_option_id=opt.option_id,
                )
            )

    return lines


def _apply_modifiers(
    lines: list[ResolvedLine],
    modifiers: Sequence[ModifierSpec],
    item: MenuItemSpec,
    warnings: list[str],
) -> list[ResolvedLine]:
    """Rule 3, in the order SUBSTITUTE -> SCALE -> ADD. Rule 4 raises."""
    substitutable: dict[ComponentRole, bool] = {}
    for component in item.components:
        # A role is substitutable if any slot filling it says so.
        substitutable[component.role] = substitutable.get(component.role, False) or (
            component.is_substitutable
        )

    result = list(lines)
    for modifier in sorted(modifiers, key=lambda m: _MODIFIER_ORDER[m.action]):
        targets = [i for i, line in enumerate(result) if line.role is modifier.target_role]

        if modifier.action is ModifierAction.SUBSTITUTE:
            if not targets:
                warnings.append(
                    f"modifier {modifier.name!r} targets {modifier.target_role.value} but "
                    f"{item.name!r} has no such component; ignored"
                )
                continue
            # RULE 4: substituting into a slot that forbids it is an ERROR, not a
            # silent no-op. Silently ignoring it would charge a customer for oat
            # milk, serve dairy, and deplete the wrong ingredient.
            if not substitutable.get(modifier.target_role, False):
                raise SubstitutionError(
                    f"modifier {modifier.name!r} cannot substitute the "
                    f"{modifier.target_role.value} component of {item.name!r}: "
                    f"the slot is not substitutable"
                )
            if modifier.ingredient_id is None:
                warnings.append(
                    f"SUBSTITUTE modifier {modifier.name!r} names no replacement "
                    "ingredient; ignored"
                )
                continue
            for index in targets:
                old = result[index]
                result[index] = ResolvedLine(
                    ingredient_id=modifier.ingredient_id,
                    # Carries the replaced slot's quantity across.
                    qty=old.qty,
                    role=old.role,
                    from_modifier_id=modifier.modifier_id,
                    from_option_id=old.from_option_id,
                )

        elif modifier.action is ModifierAction.SCALE:
            factor = modifier.qty_multiplier
            if factor is None:
                warnings.append(f"SCALE modifier {modifier.name!r} has no multiplier; ignored")
                continue
            if not targets:
                warnings.append(
                    f"modifier {modifier.name!r} targets {modifier.target_role.value} but "
                    f"{item.name!r} has no such component; ignored"
                )
                continue
            for index in targets:
                old = result[index]
                result[index] = ResolvedLine(
                    ingredient_id=old.ingredient_id,
                    qty=old.qty * factor,
                    role=old.role,
                    from_modifier_id=modifier.modifier_id,
                    from_option_id=old.from_option_id,
                )

        else:  # ADD
            if modifier.ingredient_id is None or modifier.qty_delta is None:
                warnings.append(
                    f"ADD modifier {modifier.name!r} needs both an ingredient and a "
                    "quantity; ignored"
                )
                continue
            result.append(
                ResolvedLine(
                    ingredient_id=modifier.ingredient_id,
                    qty=modifier.qty_delta,
                    role=modifier.target_role,
                    from_modifier_id=modifier.modifier_id,
                )
            )

    return _merge_same_ingredient(result)


def _merge_same_ingredient(lines: list[ResolvedLine]) -> list[ResolvedLine]:
    """Collapse duplicate ingredients, summing quantities.

    An "extra shot" ADD and the base COFFEE component are the same ingredient and
    must deplete as one number, not two ledger rows that happen to add up.
    """
    merged: dict[tuple[int, ComponentRole], ResolvedLine] = {}
    order: list[tuple[int, ComponentRole]] = []
    for line in lines:
        key = (line.ingredient_id, line.role)
        if key in merged:
            existing = merged[key]
            merged[key] = ResolvedLine(
                ingredient_id=existing.ingredient_id,
                qty=existing.qty + line.qty,
                role=existing.role,
                from_modifier_id=existing.from_modifier_id or line.from_modifier_id,
                from_option_id=existing.from_option_id or line.from_option_id,
            )
        else:
            merged[key] = line
            order.append(key)
    return [merged[k] for k in order]


def _cost_breakdown(
    lines: Sequence[ResolvedLine], ingredients: dict[int, IngredientSnapshot]
) -> tuple[CostBreakdownLine, ...]:
    """Cost from RECIPE quantities -- no waste factor (invariant 5).

    An ingredient with no price yields line_cost_pence=None rather than zero.
    Invariant 6: a missing cost must stay visible all the way up, so the margin
    screen can exclude the item instead of reporting a flattering fiction.
    """
    out: list[CostBreakdownLine] = []
    for line in lines:
        snapshot = ingredients.get(line.ingredient_id)
        cost_per_unit = snapshot.cost_per_unit_pence if snapshot else None
        source: PriceSource | None = snapshot.cost_source if snapshot else None
        out.append(
            CostBreakdownLine(
                ingredient_id=line.ingredient_id,
                ingredient_name=snapshot.name if snapshot else f"#{line.ingredient_id}",
                qty=line.qty,
                unit=snapshot.unit if snapshot else None,
                cost_per_unit_pence=cost_per_unit,
                line_cost_pence=None if cost_per_unit is None else cost_per_unit * line.qty,
                source=source,
            )
        )
    return tuple(out)
