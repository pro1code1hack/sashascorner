"""Recipe resolution and impact preview. Spec 4.3 and 5.5.

PHASE 0 SCOPE NOTE: this module belongs to Agent B (composition engine). Phase 0
implements `resolve_recipe` because it is the contract every other agent depends
on and because `seed --demo` and `stock --as-of` cannot exist without it. Agent B
owns hardening it and adding the impact preview and the cost cascade (spec 5.5).
`preview_impact` (spec 5.5) is Agent B's addition: it answers "what would this edit
do" from two resolutions of the same item, and it is the reason a composition edit
can be reviewed before it commits rather than explained afterwards.

Pure: dataclasses in, dataclasses out. No SQLAlchemy, no I/O. Effective dating is
resolved by the repository BEFORE this function runs -- a MenuItemSpec already
contains only the components and options in force at the resolve date. That split
is deliberate: the date arithmetic is a query concern, and keeping it out of here
is what makes resolution testable without a database.

SEASONS (v2, spec 4.3). `resolve_recipe` **always resolves, in season or not.** An
out-of-season option produces a WARNING, never an error and never a dropped line.
Two reasons, and the second is the load-bearing one:

1. A past sale has to resolve. Expansion resolves each sale at its own `sold_at`,
   and a Pistachio Latte sold in April must still deplete pistachio syrup when the
   ledger is rebuilt in September.
2. Even a sale dated TODAY, out of season, has to resolve. If the till rang one up,
   the syrup left the building. Refusing to resolve would hide real consumption
   behind a calendar, and the stock figures -- the thing the drift metric rests on
   -- would be wrong in the one direction nobody would think to check.

Out-of-season is therefore an **availability** fact, not a composition fact.
`availability_at` answers it for the MENU: the item is listed as unavailable, the
ordering path stops buying for it, and the recipe still works. Those are separate
questions and this module keeps them separate.
"""

from __future__ import annotations

import enum
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from cafeops.domain.labour import (
    UNTIMED,
    ItemLabour,
    MarginRanking,
    PrepTime,
    labour_for,
    rank_menu,
    resolve_prep_time,
)
from cafeops.domain.stock import apply_waste
from cafeops.domain.types import (
    ComponentRole,
    ComponentSpec,
    CostBreakdownLine,
    ImpactedItem,
    ImpactPreview,
    IngredientSnapshot,
    MenuItemSpec,
    ModifierAction,
    ModifierSpec,
    PriceSource,
    ResolvedLine,
    ResolvedRecipe,
    SeasonSpec,
    SizeCode,
    SubstitutionError,
    Unit,
    VariantOptionSpec,
)

__all__ = [
    "SIZE_ORDER",
    "Availability",
    "ChangeImpact",
    "ChangesetContext",
    "ChangesetOutcome",
    "DraftAxis",
    "DraftComponent",
    "DraftItem",
    "DraftOption",
    "ItemAvailability",
    "ItemChange",
    "ItemImpact",
    "LabourImpact",
    "LabourImpactedItem",
    "OpBasePrice",
    "OpComponentAdd",
    "OpComponentQty",
    "OpComponentRemove",
    "OpComponentSet",
    "OpOptionActive",
    "OpOptionAdd",
    "OpOptionRemove",
    "OpOptionSet",
    "OpPrepSet",
    "OpTemplateRename",
    "OptionSeason",
    "Refusal",
    "TemplateDraft",
    "TemplateOp",
    "apply_template_ops",
    "availability_at",
    "base_prices",
    "gbp",
    "item_prep",
    "item_spec_from_draft",
    "preview_impact",
    "preview_labour_impact",
    "qty_text",
    "resolve_recipe",
    "summarise_changes",
    "unit_gbp",
    "unit_label",
]

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
    option_seasons: Mapping[int, SeasonSpec] | None = None,
    item_season: SeasonSpec | None = None,
) -> ResolvedRecipe:
    """Resolve a menu item into concrete ingredient quantities and a cost breakdown.

    Template components (already narrowed to `at` and to the item's size), with
    variant options filling role-matched empty slots, then modifiers applied in
    the order SUBSTITUTE -> SCALE -> ADD.

    `lines` carry RECIPE quantities. `depletion_lines` carry the same quantities
    with each ingredient's waste factor applied. INVARIANT 5: waste affects stock
    depletion only, never menu cost -- so they are two separate values and no
    caller can reach for the wrong one by accident.

    `option_seasons` maps option_id -> the season that option belongs to, and
    `item_season` is the menu item's own season. They are passed alongside the spec
    rather than being fields on `MenuItemSpec` because that type is integrator-owned;
    see the module docstring for why they only ever produce a warning. Omitting them
    costs the warning, never the resolution -- which is the right failure direction
    for a function that sale expansion depends on.
    """
    ingredients = ingredients or {}
    warnings: list[str] = []
    warnings.extend(_season_warnings(item, at, option_seasons, item_season))

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


# ==========================================================================
# Seasons (spec 4.3): resolution always answers, the MENU is what closes
# ==========================================================================


def _season_warnings(
    item: MenuItemSpec,
    at: datetime,
    option_seasons: Mapping[int, SeasonSpec] | None,
    item_season: SeasonSpec | None,
) -> list[str]:
    """Say when a resolution is out of season, without changing the answer.

    The wording matters as much as the check. "It still resolves" has to be on the
    warning, because the natural reading of an out-of-season warning is that
    something was skipped -- and nothing was.
    """
    day = at.date()
    out: list[str] = []

    if item_season is not None and not item_season.contains(day):
        out.append(
            f"{item.name!r} is out of season at {day.isoformat()} "
            f"({item_season.name}, {_window(item_season)}); it still resolves -- a sale "
            "that happened consumed what it consumed. The menu lists it as unavailable."
        )

    for option in item.options:
        season = (option_seasons or {}).get(option.option_id)
        if season is None or season.contains(day):
            continue
        out.append(
            f"variant option {option.name!r} is out of season at {day.isoformat()} "
            f"({season.name}, {_window(season)}); it still resolves -- resolution is a "
            "record of what a drink contains, not a decision about whether to sell it."
        )
    return out


def _window(season: SeasonSpec) -> str:
    span = f"{season.starts_on.strftime('%d %b')} to {season.ends_on.strftime('%d %b')}"
    return f"{span}, recurring" if season.is_recurring_annually else span


class Availability(enum.StrEnum):
    """Whether the MENU should offer an item today. Not whether it resolves."""

    AVAILABLE = "AVAILABLE"
    OUT_OF_SEASON = "OUT_OF_SEASON"
    INACTIVE = "INACTIVE"


@dataclass(frozen=True, slots=True)
class OptionSeason:
    """A variant option and the season it belongs to, for the availability check."""

    option_id: int
    name: str
    season: SeasonSpec


@dataclass(frozen=True, slots=True)
class ItemAvailability:
    """The menu's answer for one item on one day, with the reason attached.

    `days_remaining` is the remaining span of the binding season when the item IS
    available, which is what the ordering path caps a cover window against (spec 5.4,
    invariant 4). It is None out of season, because "days left" of a season that is
    not running is not a number.
    """

    menu_item_id: int
    name: str
    size_code: SizeCode | None
    on: date
    availability: Availability
    reasons: tuple[str, ...] = ()
    binding_season: SeasonSpec | None = None
    days_remaining: int | None = None

    @property
    def is_available(self) -> bool:
        return self.availability is Availability.AVAILABLE

    @property
    def label(self) -> str:
        return f"{self.name} {self.size_code.value}" if self.size_code else self.name

    def describe(self) -> str:
        if self.is_available and self.binding_season is not None:
            left = "" if self.days_remaining is None else f", {self.days_remaining} day(s) left"
            return f"available ({self.binding_season.name}{left})"
        if self.is_available:
            return "available"
        return f"{self.availability.value}: {'; '.join(self.reasons)}"


def availability_at(
    *,
    menu_item_id: int,
    name: str,
    size_code: SizeCode | None,
    on: date,
    is_active: bool = True,
    item_season: SeasonSpec | None = None,
    option_seasons: Sequence[OptionSeason] = (),
) -> ItemAvailability:
    """Should the menu offer this item on `on`?

    This is the half of the seasonal question that DOES say no. An item is
    unavailable when it is inactive, when its own season is not running, or when any
    variant option it is built from is out of season -- a Pistachio Latte cannot be
    sold in September because the pistachio option is a spring option, even though
    the latte template runs all year.

    `resolve_recipe` is deliberately not consulted and deliberately unaffected. The
    recipe of an unavailable item is still a fact; its place on today's menu is not.

    When more than one season applies, the one with the FEWEST days remaining binds.
    That is the season that will stop the item first, so it is the one an order has
    to be capped against.
    """
    reasons: list[str] = []
    if not is_active:
        return ItemAvailability(
            menu_item_id=menu_item_id,
            name=name,
            size_code=size_code,
            on=on,
            availability=Availability.INACTIVE,
            reasons=("the menu item is marked inactive",),
        )

    seasons: list[SeasonSpec] = []
    if item_season is not None:
        seasons.append(item_season)
        if not item_season.contains(on):
            reasons.append(f"item season {item_season.name!r} ({_window(item_season)}) is not open")
    for option in option_seasons:
        seasons.append(option.season)
        if not option.season.contains(on):
            reasons.append(
                f"option {option.name!r} is {option.season.name!r} only ({_window(option.season)})"
            )

    if reasons:
        return ItemAvailability(
            menu_item_id=menu_item_id,
            name=name,
            size_code=size_code,
            on=on,
            availability=Availability.OUT_OF_SEASON,
            reasons=tuple(reasons),
        )

    remaining = [(s.days_remaining(on), s) for s in seasons]
    binding = min(
        ((days, s) for days, s in remaining if days is not None),
        key=lambda pair: pair[0],
        default=None,
    )
    return ItemAvailability(
        menu_item_id=menu_item_id,
        name=name,
        size_code=size_code,
        on=on,
        availability=Availability.AVAILABLE,
        binding_season=None if binding is None else binding[1],
        days_remaining=None if binding is None else binding[0],
    )


# ==========================================================================
# Impact preview (spec 5.5)
# ==========================================================================


@dataclass(frozen=True, slots=True)
class ItemImpact:
    """One menu item resolved twice: as it is, and as a pending edit would make it.

    The caller resolves both sides -- that is a query concern, like effective
    dating -- and this module decides what the difference MEANS. `units_sold` is
    the real volume over the preview window, so the COGS projection is measured
    rather than assumed.
    """

    menu_item_id: int
    name: str
    size_code: SizeCode | None
    price_pence: int
    before: ResolvedRecipe
    after: ResolvedRecipe
    units_sold: Decimal = Decimal("0")
    #: v2: the item's prep time and the loaded rate in force, so the preview can say
    #: what the edit does to TRUE margin and to margin-per-minute as well as to cost.
    #: A recipe change moves both, and the second can reorder the menu.
    prep: PrepTime = UNTIMED
    loaded_hourly_rate_pence: int | None = None
    template_id: int | None = None

    def labour(self, cost_pence: Decimal | None) -> ItemLabour:
        """This item's labour picture at a given ingredient cost.

        Called twice per item -- once with the cost before the edit and once with the
        cost after -- because labour is unchanged by a recipe edit while TRUE margin
        and margin-per-minute both move with the ingredient cost.
        """
        return ItemLabour(
            labour=labour_for(
                menu_item_id=self.menu_item_id,
                price_pence=self.price_pence,
                ingredient_cost_pence=cost_pence,
                prep=self.prep,
                loaded_hourly_rate_pence=self.loaded_hourly_rate_pence,
            ),
            name=self.name,
            size_code=self.size_code,
            prep=self.prep,
            units_sold=self.units_sold,
            template_id=self.template_id,
        )

    @property
    def recipe_changed(self) -> bool:
        return _fingerprint(self.before) != _fingerprint(self.after)

    @property
    def cost_changed(self) -> bool:
        return (
            self.before.cost_pence != self.after.cost_pence
            or self.before.has_missing_cost != self.after.has_missing_cost
            or self.before.cost_source != self.after.cost_source
        )

    @property
    def is_affected(self) -> bool:
        """An item the edit actually moves.

        Both halves matter: a recipe quantity edit changes the lines, and an
        ingredient price change leaves the lines identical and moves only the
        cost. Either one is an impact.
        """
        return self.recipe_changed or self.cost_changed


def preview_impact(
    candidates: Sequence[ItemImpact],
    *,
    window_days: int = 30,
    extra_warnings: Sequence[str] = (),
) -> ImpactPreview:
    """What a pending composition or price edit would do, before it commits.

    Spec 5.5. Pure: resolved recipes in, an ImpactPreview out. The UI renders it;
    it does not compute it, and neither does the CLI.

    INVARIANT 6: an item whose cost is unknown on either side is named in
    `warnings` and left out of every total. It is never treated as zero -- a
    flattering COGS figure built on absent prices is worse than no figure, because
    nothing downstream can tell it was a guess.
    """
    warnings: list[str] = list(extra_warnings)

    affected = [c for c in candidates if c.is_affected]
    items: list[ImpactedItem] = [
        ImpactedItem(
            menu_item_id=c.menu_item_id,
            name=c.name,
            size_code=c.size_code,
            cost_before_pence=c.before.cost_pence,
            cost_after_pence=c.after.cost_pence,
            price_pence=c.price_pence,
        )
        for c in affected
    ]

    pairs = list(zip(affected, items, strict=True))
    priced = [(c, i) for c, i in pairs if i.cost_delta_pence is not None]
    unpriced = [(c, i) for c, i in pairs if i.cost_delta_pence is None]

    for candidate, item in unpriced:
        warnings.append(
            f"{_label(item)}: cost unknown ({_missing_detail(candidate)}) -- EXCLUDED "
            "from the cost delta and the COGS projection, not counted as zero"
        )
    if unpriced:
        warnings.append(
            f"{len(unpriced)} of {len(items)} affected item(s) have a missing "
            "ingredient cost and are excluded from every total below"
        )

    per_item = _per_item_delta([i for _c, i in priced], warnings)
    cogs = _cogs_delta(priced, window_days, warnings)
    worst = _worst_margin_after(items)

    return ImpactPreview(
        affected_item_count=len(affected),
        items=tuple(items),
        cost_delta_pence_per_item=per_item,
        monthly_cogs_delta_pence=cogs,
        worst_margin_after=worst,
        warnings=tuple(warnings),
    )


# --------------------------------------------------------------------------


def _fingerprint(recipe: ResolvedRecipe) -> tuple[tuple[int, str, str], ...]:
    """Ingredient, role and quantity -- what makes two recipes the same recipe.

    Quantities compare as normalised strings so 0.18 and 0.180 are one recipe and
    not a spurious edit.
    """
    return tuple(
        sorted(
            (line.ingredient_id, line.role.value, format(line.qty.normalize(), "f"))
            for line in recipe.lines
        )
    )


def _label(item: ImpactedItem) -> str:
    return f"{item.name} {item.size_code.value}" if item.size_code else item.name


def _missing_detail(candidate: ItemImpact) -> str:
    """Name the unpriced ingredients, so the fix is obvious from the warning."""
    missing = sorted(
        {
            line.ingredient_name
            for recipe in (candidate.before, candidate.after)
            for line in recipe.cost_breakdown
            if line.is_missing_cost
        }
    )
    return ", ".join(missing) if missing else "no priced ingredients at all"


def _per_item_delta(priced: Sequence[ImpactedItem], warnings: list[str]) -> Decimal | None:
    """One number only when the items agree on it.

    Distinct deltas are reported as a range instead of averaged. An average would
    be a figure that describes no actual menu item, and the reviewer would take it
    for the per-item cost it is named after.
    """
    deltas = [d for d in (i.cost_delta_pence for i in priced) if d is not None]
    if not deltas:
        return None
    distinct = sorted(set(deltas))
    if len(distinct) == 1:
        return distinct[0]
    warnings.append(
        f"cost delta is not uniform across the affected items "
        f"({_pence(distinct[0])} to {_pence(distinct[-1])}) -- see the per-item "
        "figures; no single per-item delta is reported"
    )
    return None


def _cogs_delta(
    priced: Sequence[tuple[ItemImpact, ImpactedItem]],
    window_days: int,
    warnings: list[str],
) -> Decimal | None:
    if not priced:
        return None
    total = Decimal("0")
    volume = Decimal("0")
    for candidate, item in priced:
        delta = item.cost_delta_pence
        if delta is None:  # pragma: no cover -- filtered by the caller
            continue
        total += delta * candidate.units_sold
        volume += candidate.units_sold
    if volume == 0:
        warnings.append(
            f"no sales recorded for the affected items in the last {window_days} "
            "days, so the COGS delta is 0 by absence of volume, not by absence of effect"
        )
    if window_days != 30:
        # The field is named `monthly_cogs_delta_pence`. If the caller measured a
        # different window, say so rather than letting a 7-day figure be read as a
        # month's.
        warnings.append(
            f"the COGS figure covers {window_days} days of sales, NOT a month -- "
            "read it as a projection over that window"
        )
    return total


def _worst_margin_after(items: Sequence[ImpactedItem]) -> ImpactedItem | None:
    """Thinnest margin once the edit lands. Items with an unknown cost cannot rank."""
    ranked = [
        (margin, item)
        for item in items
        if (margin := item.margin_pct(item.cost_after_pence)) is not None
    ]
    if not ranked:
        return None
    return min(ranked, key=lambda pair: pair[0])[1]


def _pence(value: Decimal) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}{abs(value):.3f}p"


# ==========================================================================
# The labour half of an impact preview (spec 5.6)
# ==========================================================================


@dataclass(frozen=True, slots=True)
class LabourImpactedItem:
    """One item's labour picture on both sides of a pending edit.

    Labour cost itself does not move -- a recipe edit changes what is in the cup, not
    how long it takes to make. TRUE margin and margin-per-minute both move, because
    both are net of the ingredient cost, and margin-per-minute is the one that can
    reorder the menu. That is why this is worth showing before the edit commits and
    not after.
    """

    before: ItemLabour
    after: ItemLabour

    @property
    def menu_item_id(self) -> int:
        return self.after.menu_item_id

    @property
    def label(self) -> str:
        return self.after.label

    @property
    def labour_cost_pence(self) -> Decimal | None:
        return self.after.labour.labour_cost_pence

    @property
    def true_margin_delta_pence(self) -> Decimal | None:
        before = self.before.labour.true_margin_pence
        after = self.after.labour.true_margin_pence
        if before is None or after is None:
            return None
        return after - before

    @property
    def margin_per_minute_delta_pence(self) -> Decimal | None:
        before = self.before.margin_per_minute_pence
        after = self.after.margin_per_minute_pence
        if before is None or after is None:
            return None
        return after - before

    @property
    def is_measurable(self) -> bool:
        return self.true_margin_delta_pence is not None


@dataclass(frozen=True, slots=True)
class LabourImpact:
    """An `ImpactPreview` with the labour consequences attached.

    Composed rather than merged: `ImpactPreview` is integrator-owned and has no
    labour fields, and inventing a parallel type that duplicated its five would
    guarantee the two drifted apart. `preview` IS the spec 5.5 object, unchanged, and
    everything beside it is spec 5.6.

    The two rankings here cover only the items the edit touches, not the whole menu.
    A preview cannot honestly rank the menu it has not loaded, and saying "rank 3 of
    9 affected items" is worth more than a menu-wide rank computed from a subset.
    """

    preview: ImpactPreview
    items: tuple[LabourImpactedItem, ...] = ()
    #: One figure when every affected item agrees on it, else None with the spread in
    #: the matching `_range` field -- the same rule
    #: `ImpactPreview.cost_delta_pence_per_item` follows, for the same reason. A range
    #: is a fact; an average across items that disagree is a number describing nothing.
    labour_cost_pence_per_item: Decimal | None = None
    labour_cost_pence_range: tuple[Decimal, Decimal] | None = None
    true_margin_delta_pence_per_item: Decimal | None = None
    true_margin_delta_pence_range: tuple[Decimal, Decimal] | None = None
    worst_true_margin_after: LabourImpactedItem | None = None
    worst_margin_per_minute_after: LabourImpactedItem | None = None
    ranking_before: MarginRanking | None = None
    ranking_after: MarginRanking | None = None
    #: (label, rank before, rank after) on margin-per-minute, for items the edit moves.
    rank_moves: tuple[tuple[str, int, int], ...] = ()
    #: Weighted labour over the preview window, before and after. Equal unless a prep
    #: time changed, and shown anyway so the reader can see the scale of it.
    labour_cost_window_pence: Decimal | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def untimed_count(self) -> int:
        return sum(1 for item in self.items if not item.after.prep.is_known)


def preview_labour_impact(
    candidates: Sequence[ItemImpact],
    *,
    window_days: int = 30,
    extra_warnings: Sequence[str] = (),
) -> LabourImpact:
    """The spec 5.5 preview and the spec 5.6 labour consequences, from one input.

    Calls `preview_impact` rather than reimplementing it, so there is exactly one
    definition of "affected" and of which items are excluded for a missing cost. An
    edit reviewed on two subtly different item sets is an edit nobody approved.
    """
    preview = preview_impact(candidates, window_days=window_days, extra_warnings=extra_warnings)
    affected_ids = {item.menu_item_id for item in preview.items}
    affected = [c for c in candidates if c.menu_item_id in affected_ids]

    warnings: list[str] = []
    items = tuple(
        LabourImpactedItem(before=c.labour(c.before.cost_pence), after=c.labour(c.after.cost_pence))
        for c in affected
    )

    untimed = [item.label for item in items if not item.after.prep.is_known]
    if untimed:
        warnings.append(
            f"{len(untimed)} affected item(s) have no prep time, so their labour cost, "
            "true margin and margin-per-minute are UNKNOWN rather than zero and they are "
            f"excluded from the labour figures: {', '.join(sorted(untimed)[:6])}"
            + (" ..." if len(untimed) > 6 else "")
        )
    no_rate = [item.label for item in items if item.after.labour.loaded_hourly_rate_pence is None]
    if no_rate:
        warnings.append(
            f"{len(no_rate)} affected item(s) have no loaded hourly rate, so no labour "
            "figure is reported for them -- a labour cost from a guessed rate is a guess "
            "wearing a number's clothes"
        )

    measurable = [item for item in items if item.is_measurable]
    ranking_before = rank_menu([item.before for item in items]) if items else None
    ranking_after = rank_menu([item.after for item in items]) if items else None

    moves: list[tuple[str, int, int]] = []
    if ranking_before is not None and ranking_after is not None:
        for entry in ranking_after.ranked:
            was = ranking_before.by_id(entry.item.menu_item_id)
            if was is None or was.margin_per_minute_rank == entry.margin_per_minute_rank:
                continue
            moves.append(
                (entry.item.label, was.margin_per_minute_rank, entry.margin_per_minute_rank)
            )

    labour_per_item, labour_range = _uniform(
        [item.labour_cost_pence for item in items if item.labour_cost_pence is not None]
    )
    true_margin_delta, true_margin_range = _uniform(
        [delta for item in measurable if (delta := item.true_margin_delta_pence) is not None]
    )

    window_labour: Decimal | None = None
    for item in items:
        cost = item.after.labour.labour_cost_pence
        if cost is None:
            continue
        window_labour = (window_labour or Decimal("0")) + cost * item.after.units_sold

    return LabourImpact(
        preview=preview,
        items=items,
        labour_cost_pence_per_item=labour_per_item,
        labour_cost_pence_range=labour_range,
        true_margin_delta_pence_per_item=true_margin_delta,
        true_margin_delta_pence_range=true_margin_range,
        worst_true_margin_after=_worst(measurable, lambda i: i.after.labour.true_margin_pence),
        worst_margin_per_minute_after=_worst(measurable, lambda i: i.after.margin_per_minute_pence),
        ranking_before=ranking_before,
        ranking_after=ranking_after,
        rank_moves=tuple(moves),
        labour_cost_window_pence=window_labour,
        warnings=tuple(warnings),
    )


def _uniform(values: Sequence[Decimal]) -> tuple[Decimal | None, tuple[Decimal, Decimal] | None]:
    """(the single agreed value, the range) -- exactly one of the two is not None.

    An average across items that disagree would describe no actual menu item and would
    be read as the per-item number it is named after, so a disagreement is reported as
    a RANGE instead. That is also why the comparison is on `set(values)` and not on
    formatted strings: `Decimal("-226.5")` and `Decimal("-226.500")` are the same
    number, and comparing their text would have reported a spurious range on every
    edit whose deltas happened to carry different trailing zeros.
    """
    if not values:
        return (None, None)
    distinct = sorted(set(values))
    if len(distinct) == 1:
        return (distinct[0], None)
    return (None, (distinct[0], distinct[-1]))


def _worst(
    items: Sequence[LabourImpactedItem],
    key: Callable[[LabourImpactedItem], Decimal | None],
) -> LabourImpactedItem | None:
    ranked = [(value, item) for item in items if (value := key(item)) is not None]
    if not ranked:
        return None
    return min(ranked, key=lambda pair: pair[0])[1]


# ==========================================================================
# Any edit, previewed the same way (back-office redesign, recipes spec A2-A4)
# ==========================================================================
#
# `ItemImpact` above has ONE price, which is right for a quantity edit and wrong for
# everything the redesign adds: a base-price edit moves the price, a new flavour has
# no "before" at all, and taking a flavour off the menu has no "after" worth pricing.
# `ItemChange` carries both sides of every figure, and `summarise_changes` is the one
# definition of "affected", "excluded for an unknown cost" and "thinnest margin" that
# every redesign preview uses -- recipe changesets, manual lines, sell prices and
# ingredient prices alike. One definition, so two previews cannot disagree about
# what the same edit does.

#: The order sizes are shown in everywhere, and the order the design adds them in.
SIZE_ORDER: tuple[SizeCode, ...] = (SizeCode.S, SizeCode.M, SizeCode.XL, SizeCode.ONE)

_UNIT_LABEL: dict[Unit, str] = {
    Unit.L: "L",
    Unit.ML: "ml",
    Unit.KG: "kg",
    Unit.G: "g",
    Unit.EACH: "unit",
}


def unit_label(unit: Unit | None) -> str:
    """The design's unit words: L, ml, kg, g, unit."""
    return "" if unit is None else _UNIT_LABEL[unit]


def gbp(pence: int | Decimal) -> str:
    """`£3.90`, or U+2212 then `£0.60`. The design's `gbp`, for server-built sentences."""
    value = Decimal(pence)
    sign = "\u2212" if value < 0 else ""
    return f"{sign}£{abs(value) / 100:.2f}"


def unit_gbp(pence: Decimal) -> str:
    """Pence per unit the design's way: £1.23, £0.734, £0.0450 (2/3/4 dp by size)."""
    pounds = abs(pence) / 100
    places = 2 if pounds >= 1 else 3 if pounds >= Decimal("0.1") else 4
    sign = "\u2212" if pence < 0 else ""
    return f"{sign}£{pounds:.{places}f}"


def qty_text(value: Decimal) -> str:
    """A quantity without trailing zeros or an exponent: 0.180 -> 0.18, 1E+2 -> 100."""
    if value == 0:
        return "0"
    text: str = f"{value.normalize():f}"
    return text


def _size_word(size: SizeCode | None) -> str:
    if size is None or size is SizeCode.ONE:
        return "One"
    return str(size.value)


@dataclass(frozen=True, slots=True)
class ItemChange:
    """One menu item on both sides of a pending edit, with every figure it moves.

    `exists_before=False` is an item the edit creates; `active_after=False` one it
    takes off the menu. `before`/`after` are None when there is no recipe to resolve on
    that side. A recipe with no lines has an UNKNOWN cost here, never zero: "nobody has
    told us what this contains" is not "this costs nothing" (invariant 8).
    """

    key: str
    menu_item_id: int | None
    name: str
    size_code: SizeCode | None
    before: ResolvedRecipe | None
    after: ResolvedRecipe | None
    price_before: int | None
    price_after: int | None
    exists_before: bool = True
    active_before: bool = True
    active_after: bool = True
    prep_before: PrepTime = UNTIMED
    prep_after: PrepTime = UNTIMED
    units_sold: Decimal = Decimal("0")
    loaded_hourly_rate_pence: int | None = None
    on_till: bool = True

    @property
    def label(self) -> str:
        return f"{self.name} {_size_word(self.size_code)}" if self.size_code else self.name

    @staticmethod
    def _cost(recipe: ResolvedRecipe | None) -> Decimal | None:
        if recipe is None or not recipe.lines:
            return None
        return recipe.cost_pence

    @property
    def cost_before(self) -> Decimal | None:
        return self._cost(self.before) if self.exists_before else None

    @property
    def cost_after(self) -> Decimal | None:
        return self._cost(self.after)

    @property
    def source_before(self) -> PriceSource | None:
        return None if self.before is None or not self.exists_before else self.before.cost_source

    @property
    def source_after(self) -> PriceSource | None:
        return None if self.after is None else self.after.cost_source

    @staticmethod
    def margin_pct(price: int | None, cost: Decimal | None) -> float | None:
        if price is None or price <= 0 or cost is None:
            return None
        return float((Decimal(price) - cost) / Decimal(price) * 100)

    @property
    def margin_before(self) -> float | None:
        return self.margin_pct(self.price_before, self.cost_before)

    @property
    def margin_after(self) -> float | None:
        return self.margin_pct(self.price_after, self.cost_after)

    def labour_before(self) -> LabourCostView:
        return _labour_view(
            self.menu_item_id, self.price_before, self.cost_before, self.prep_before, self
        )

    def labour_after(self) -> LabourCostView:
        return _labour_view(
            self.menu_item_id, self.price_after, self.cost_after, self.prep_after, self
        )

    @property
    def cost_delta(self) -> Decimal | None:
        if not self.exists_before or self.cost_before is None or self.cost_after is None:
            return None
        return self.cost_after - self.cost_before

    @property
    def status(self) -> str:
        """new | off | on | changed | unchanged -- what the edit does to this item."""
        if not self.exists_before:
            return "new"
        if self.active_before and not self.active_after:
            return "off"
        if not self.active_before and self.active_after:
            return "on"
        cost_moved = (
            self.cost_before != self.cost_after
            or self.source_before != self.source_after
            or _fingerprint_or_none(self.before) != _fingerprint_or_none(self.after)
        )
        if (
            cost_moved
            or self.price_before != self.price_after
            or self.prep_before != self.prep_after
        ):
            return "changed"
        return "unchanged"


@dataclass(frozen=True, slots=True)
class LabourCostView:
    """Labour, true margin and margin-per-minute for one side of an `ItemChange`."""

    labour_cost_pence: Decimal | None
    true_margin_pence: Decimal | None
    margin_per_minute_pence: Decimal | None
    prep_seconds: int | None
    prep_is_estimate: bool


def _labour_view(
    menu_item_id: int | None,
    price: int | None,
    cost: Decimal | None,
    prep: PrepTime,
    change: ItemChange,
) -> LabourCostView:
    if price is None:
        return LabourCostView(None, None, None, prep.seconds, prep.is_estimate)
    labour = labour_for(
        menu_item_id=menu_item_id or 0,
        price_pence=price,
        ingredient_cost_pence=cost,
        prep=prep,
        loaded_hourly_rate_pence=change.loaded_hourly_rate_pence,
    )
    return LabourCostView(
        labour_cost_pence=labour.labour_cost_pence,
        true_margin_pence=labour.true_margin_pence,
        margin_per_minute_pence=labour.margin_per_minute_pence,
        prep_seconds=prep.seconds,
        prep_is_estimate=prep.is_estimate,
    )


def _fingerprint_or_none(recipe: ResolvedRecipe | None) -> tuple[tuple[int, str, str], ...] | None:
    return None if recipe is None else _fingerprint(recipe)


@dataclass(frozen=True, slots=True)
class ChangeImpact:
    """What a pending edit does, summarised. Every figure excludes unknown costs.

    `cost_delta_per_item` is one figure when every priced, affected item agrees, else
    None with the spread in `cost_delta_range` -- an average across items that
    disagree describes no item (the rule `preview_impact` already follows).
    """

    items: tuple[ItemChange, ...]
    affected_count: int
    cost_delta_per_item: Decimal | None
    cost_delta_range: tuple[Decimal, Decimal] | None
    monthly_cogs_delta: Decimal | None
    revenue_delta: Decimal | None
    worst_margin_after: ItemChange | None
    untimed_count: int
    estimated_count: int
    window_days: int
    warnings: tuple[str, ...] = ()


def summarise_changes(
    changes: Sequence[ItemChange],
    *,
    window_days: int = 30,
    extra_warnings: Sequence[str] = (),
) -> ChangeImpact:
    """The one summary every redesign preview uses. Pure.

    INVARIANT 8: an item whose cost is unknown on either side is named in a warning
    and left out of every total -- never counted as zero.
    """
    warnings = list(extra_warnings)
    affected = [c for c in changes if c.status != "unchanged"]

    deltas = [d for c in affected if (d := c.cost_delta) is not None]
    # "Cost per item" describes the items whose cost MOVES; a price-only change on
    # its neighbours would otherwise drag a spurious "0 to" into every range.
    moved = [d for d in deltas if d != 0]
    per_item, spread = _uniform(moved)
    if not deltas:
        per_item = None
    elif not moved:
        per_item = Decimal("0")

    unknown = [c for c in affected if c.active_after and c.cost_after is None]
    if unknown:
        names = ", ".join(c.label for c in unknown[:6]) + (" ..." if len(unknown) > 6 else "")
        warnings.append(
            f"{len(unknown)} affected item(s) have an UNKNOWN cost and are left out of every "
            f"figure here, not counted as zero: {names}"
        )

    cogs: Decimal | None = None
    for change in affected:
        delta = change.cost_delta
        if delta is None:
            continue
        cogs = (cogs or Decimal("0")) + delta * change.units_sold
    existing = [c for c in affected if c.exists_before]
    if existing and all(c.units_sold == 0 for c in existing):
        warnings.append(
            f"None of these items sold in the last {window_days} days, so the figures over "
            "that time are 0 by absence of sales, not by absence of effect."
        )

    revenue: Decimal | None = None
    for change in affected:
        if (
            not change.exists_before
            or change.price_before is None
            or change.price_after is None
            or change.price_before == change.price_after
        ):
            continue
        revenue = (revenue or Decimal("0")) + Decimal(
            change.price_after - change.price_before
        ) * change.units_sold

    ranked = [
        (margin, c) for c in changes if c.active_after and (margin := c.margin_after) is not None
    ]
    worst = min(ranked, key=lambda pair: pair[0])[1] if ranked else None

    untimed = sum(1 for c in affected if not c.prep_after.is_known)
    estimated = sum(1 for c in affected if c.source_after is PriceSource.ESTIMATE)

    return ChangeImpact(
        items=tuple(affected),
        affected_count=len(affected),
        cost_delta_per_item=per_item,
        cost_delta_range=spread,
        monthly_cogs_delta=cogs,
        revenue_delta=revenue,
        worst_margin_after=worst,
        untimed_count=untimed,
        estimated_count=estimated,
        window_days=window_days,
        warnings=tuple(warnings),
    )


# --------------------------------------------------------------------------
# Template changesets: the draft, the operations, and what they do. Pure.
# --------------------------------------------------------------------------
#
# The service loads the live template into a `TemplateDraft`, the operations transform
# it into another `TemplateDraft`, and BOTH sides are turned into `MenuItemSpec`s by
# the same function (`item_spec_from_draft`) and resolved by the same resolver. So the
# before side of a preview is built exactly the way the after side is, and what a
# reviewer approved is what the apply writes: the apply walks the same two drafts.


@dataclass(frozen=True, slots=True)
class DraftAxis:
    axis_id: int
    name: str
    role: ComponentRole


@dataclass(frozen=True, slots=True)
class DraftComponent:
    """One slot. `component_id` None = added by this changeset (key `n<i>`)."""

    key: str
    component_id: int | None
    role: ComponentRole
    ingredient_id: int | None
    qty_by_size: Mapping[str, Decimal]
    is_substitutable: bool
    is_required: bool

    def qty(self, size: SizeCode | None) -> Decimal | None:
        return self.qty_by_size.get(size.value if size else SizeCode.ONE.value)


@dataclass(frozen=True, slots=True)
class DraftOption:
    """One flavour. `qty_by_size` None = inherit the slot's quantity."""

    key: str
    option_id: int | None
    axis_id: int
    name: str
    ingredient_id: int | None
    qty_by_size: Mapping[str, Decimal] | None
    price_delta_pence: int
    season_id: int | None
    removed: bool = False

    def qty(self, size: SizeCode | None) -> Decimal | None:
        if self.qty_by_size is None:
            return None
        return self.qty_by_size.get(size.value if size else SizeCode.ONE.value)


@dataclass(frozen=True, slots=True)
class DraftItem:
    """One generated menu item. `menu_item_id` None = created by this changeset."""

    key: str
    menu_item_id: int | None
    name: str
    size_code: SizeCode | None
    option_keys: Mapping[int, str]
    price_pence: int
    active: bool
    prep_seconds: int | None = None
    prep_is_estimate: bool | None = None
    on_till: bool = True


@dataclass(frozen=True, slots=True)
class TemplateDraft:
    template_id: int
    name: str
    category: str | None
    sizes: tuple[SizeCode, ...]
    axes: tuple[DraftAxis, ...]
    components: tuple[DraftComponent, ...]
    options: tuple[DraftOption, ...]
    items: tuple[DraftItem, ...]
    prep_seconds_by_size: Mapping[str, int]
    prep_is_estimate: bool | None

    def option(self, key: str) -> DraftOption | None:
        return next((o for o in self.options if o.key == key), None)

    def axis(self, axis_id: int) -> DraftAxis | None:
        return next((a for a in self.axes if a.axis_id == axis_id), None)


@dataclass(frozen=True, slots=True)
class OpComponentQty:
    component_id: int
    qty_by_size: Mapping[str, Decimal]


@dataclass(frozen=True, slots=True)
class OpComponentSet:
    component_id: int
    role: ComponentRole | None = None
    set_ingredient: bool = False
    ingredient_id: int | None = None
    is_substitutable: bool | None = None
    is_required: bool | None = None


@dataclass(frozen=True, slots=True)
class OpComponentAdd:
    role: ComponentRole
    ingredient_id: int | None
    qty_by_size: Mapping[str, Decimal]
    is_substitutable: bool = False
    is_required: bool = True


@dataclass(frozen=True, slots=True)
class OpComponentRemove:
    component_id: int


@dataclass(frozen=True, slots=True)
class OpPrepSet:
    prep_seconds_by_size: Mapping[str, int]
    is_estimate: bool


@dataclass(frozen=True, slots=True)
class OpBasePrice:
    base_price_pence_by_size: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class OpOptionSet:
    option_id: int
    name: str | None = None
    set_ingredient: bool = False
    ingredient_id: int | None = None
    set_qty: bool = False
    qty_by_size: Mapping[str, Decimal] | None = None
    price_delta_pence: int | None = None
    set_season: bool = False
    season_id: int | None = None


@dataclass(frozen=True, slots=True)
class OpOptionAdd:
    axis_id: int
    name: str
    ingredient_id: int | None
    qty_by_size: Mapping[str, Decimal] | None
    price_delta_pence: int
    season_id: int | None


@dataclass(frozen=True, slots=True)
class OpOptionActive:
    option_id: int
    active: bool


@dataclass(frozen=True, slots=True)
class OpOptionRemove:
    option_id: int


@dataclass(frozen=True, slots=True)
class OpTemplateRename:
    name: str


TemplateOp = (
    OpComponentQty
    | OpComponentSet
    | OpComponentAdd
    | OpComponentRemove
    | OpPrepSet
    | OpBasePrice
    | OpOptionSet
    | OpOptionAdd
    | OpOptionActive
    | OpOptionRemove
    | OpTemplateRename
)


@dataclass(frozen=True, slots=True)
class ChangesetContext:
    """What the operations need to know about the world outside the template."""

    ingredient_names: Mapping[int, str]
    ingredient_units: Mapping[int, Unit]
    retired_ingredient_ids: frozenset[int] = frozenset()
    season_names: Mapping[int, str] = field(default_factory=dict)
    #: Casefolded names of every OTHER template (template.name is unique).
    other_template_names: frozenset[str] = frozenset()
    #: (casefolded name, size key) of menu items NOT on this template.
    taken_item_names: frozenset[tuple[str, str]] = frozenset()
    #: How a new flavour's items are named: "{flavour} Latte". Derived by the service
    #: from the template's existing items, so a new flavour reads like its siblings.
    item_name_pattern: str = "{flavour}"
    #: SUBSTITUTE modifiers by the role they swap, for the "swaps now refused" warning.
    substitute_modifiers_by_role: Mapping[ComponentRole, tuple[str, ...]] = field(
        default_factory=dict
    )


@dataclass(frozen=True, slots=True)
class Refusal:
    op_index: int
    message: str


@dataclass(frozen=True, slots=True)
class ChangesetOutcome:
    before: TemplateDraft
    after: TemplateDraft
    diff: tuple[str, ...]
    refusals: tuple[Refusal, ...]
    warnings: tuple[str, ...]
    pos_actions: tuple[str, ...]


def base_prices(draft: TemplateDraft) -> dict[str, tuple[int | None, bool]]:
    """size -> (base price, items disagree).

    There is no template base price in the schema: each generated item has its own
    `price_pence` and each flavour a `price_delta_pence`. The base is derived as the
    most common `price - delta` at each size (ties to the lower); `True` says the items
    disagree, which the editor must show rather than hide.
    """
    out: dict[str, tuple[int | None, bool]] = {}
    for size in draft.sizes:
        values: list[int] = []
        for item in draft.items:
            if item.size_code != size:
                continue
            delta = sum(
                option.price_delta_pence
                for key in item.option_keys.values()
                if (option := draft.option(key)) is not None
            )
            values.append(item.price_pence - delta)
        if not values:
            out[size.value] = (None, False)
            continue
        counts: dict[int, int] = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        best = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        out[size.value] = (best, len(counts) > 1)
    return out


def item_spec_from_draft(draft: TemplateDraft, item: DraftItem) -> MenuItemSpec:
    """The resolver's input for one item, from a draft. Both preview sides use this."""
    components = tuple(
        ComponentSpec(
            component_id=c.component_id if c.component_id is not None else -(index + 1),
            role=c.role,
            ingredient_id=c.ingredient_id,
            qty=c.qty(item.size_code),
            is_substitutable=c.is_substitutable,
            is_required=c.is_required,
        )
        for index, c in enumerate(draft.components)
    )
    options: list[VariantOptionSpec] = []
    for index, (axis_id, key) in enumerate(sorted(item.option_keys.items())):
        option = draft.option(key)
        axis = draft.axis(axis_id)
        if option is None or option.removed or axis is None:
            continue
        options.append(
            VariantOptionSpec(
                option_id=option.option_id if option.option_id is not None else -(index + 1000),
                axis_id=axis_id,
                name=option.name,
                role=axis.role,
                ingredient_id=option.ingredient_id,
                qty=option.qty(item.size_code),
                price_delta_pence=option.price_delta_pence,
                season_id=option.season_id,
            )
        )
    return MenuItemSpec(
        menu_item_id=item.menu_item_id if item.menu_item_id is not None else -1,
        name=item.name,
        size_code=item.size_code,
        template_id=draft.template_id,
        price_pence=item.price_pence,
        manual_recipe=False,
        components=components,
        options=tuple(options),
    )


def item_prep(draft: TemplateDraft, item: DraftItem) -> PrepTime:
    return resolve_prep_time(
        template_prep_seconds_by_size=draft.prep_seconds_by_size,
        item_prep_seconds=item.prep_seconds,
        size_code=item.size_code,
        item_is_estimate=item.prep_is_estimate,
        template_is_estimate=draft.prep_is_estimate,
    )


class _Edit:
    """Mutable working copy used while applying operations. Never escapes."""

    def __init__(self, draft: TemplateDraft) -> None:
        self.name = draft.name
        self.components = list(draft.components)
        self.options = list(draft.options)
        self.items = list(draft.items)
        self.prep = dict(draft.prep_seconds_by_size)
        self.prep_est = draft.prep_is_estimate
        self.new_counter = 0

    def next_key(self, prefix: str) -> str:
        self.new_counter += 1
        return f"n{prefix}{self.new_counter}"

    def freeze(self, draft: TemplateDraft) -> TemplateDraft:
        return TemplateDraft(
            template_id=draft.template_id,
            name=self.name,
            category=draft.category,
            sizes=draft.sizes,
            axes=draft.axes,
            components=tuple(self.components),
            options=tuple(self.options),
            items=tuple(self.items),
            prep_seconds_by_size=dict(self.prep),
            prep_is_estimate=self.prep_est,
        )

    def component_index(self, component_id: int) -> int | None:
        return next(
            (i for i, c in enumerate(self.components) if c.component_id == component_id), None
        )

    def option_index(self, option_id: int) -> int | None:
        return next(
            (i for i, o in enumerate(self.options) if o.option_id == option_id and not o.removed),
            None,
        )


def apply_template_ops(
    draft: TemplateDraft, ops: Sequence[TemplateOp], ctx: ChangesetContext
) -> ChangesetOutcome:
    """Apply a changeset to a draft. Pure: refusals are collected, never raised.

    A refused operation is skipped and reported with its index, so the preview can
    show every problem at once; the apply service refuses the whole changeset if any
    operation was refused (all or nothing).
    """
    work = _Edit(draft)
    diff: list[str] = []
    refusals: list[Refusal] = []
    warnings: list[str] = []
    pos: list[str] = []
    sizes = {s.value for s in draft.sizes}

    def ing_name(ingredient_id: int | None) -> str:
        if ingredient_id is None:
            return "nothing"
        return ctx.ingredient_names.get(ingredient_id, f"ingredient #{ingredient_id}")

    def comp_label(c: DraftComponent) -> str:
        return ing_name(c.ingredient_id) if c.ingredient_id is not None else c.role.value.lower()

    def check_ingredient(index: int, ingredient_id: int | None) -> bool:
        if ingredient_id is None:
            return True
        if ingredient_id not in ctx.ingredient_names:
            refusals.append(Refusal(index, f"ingredient #{ingredient_id} does not exist"))
            return False
        if ingredient_id in ctx.retired_ingredient_ids:
            refusals.append(
                Refusal(index, f"{ing_name(ingredient_id)} is retired and cannot go into a recipe")
            )
            return False
        return True

    def check_sizes(index: int, keys: Sequence[str]) -> bool:
        bad = [k for k in keys if k not in sizes]
        if bad:
            refusals.append(
                Refusal(
                    index,
                    f"size(s) {', '.join(bad)} are not sizes of this recipe "
                    f"({', '.join(s.value for s in draft.sizes)})",
                )
            )
            return False
        return True

    def item_name(flavour: str) -> str:
        return ctx.item_name_pattern.replace("{flavour}", flavour).strip()

    for index, op in enumerate(ops):
        if isinstance(op, OpTemplateRename):
            name = op.name.strip()
            if not name:
                refusals.append(Refusal(index, "a recipe needs a name"))
            elif name.casefold() in ctx.other_template_names:
                refusals.append(Refusal(index, f"another recipe is already called {name!r}"))
            elif name != work.name:
                work.name = name
                diff.append(f"Renamed to {name}")

        elif isinstance(op, OpComponentQty):
            at = work.component_index(op.component_id)
            if at is None:
                refusals.append(
                    Refusal(index, f"component {op.component_id} is not in this recipe")
                )
                continue
            if not check_sizes(index, list(op.qty_by_size)):
                continue
            old = work.components[at]
            merged = dict(old.qty_by_size)
            for size_key, qty in op.qty_by_size.items():
                if qty < 0:
                    refusals.append(Refusal(index, f"a quantity cannot be negative ({qty})"))
                    break
                before = old.qty_by_size.get(size_key)
                if before is not None and before == qty:
                    continue
                merged[size_key] = qty
                unit = unit_label(ctx.ingredient_units.get(old.ingredient_id or -1))
                diff.append(
                    f"{comp_label(old)} {size_key}: "
                    f"{qty_text(before) if before is not None else 'none'} → {qty_text(qty)}"
                    + (f" {unit}" if unit else "")
                )
            work.components[at] = replace_component(old, qty_by_size=merged)

        elif isinstance(op, OpComponentSet):
            at = work.component_index(op.component_id)
            if at is None:
                refusals.append(
                    Refusal(index, f"component {op.component_id} is not in this recipe")
                )
                continue
            old = work.components[at]
            new = old
            if op.set_ingredient and op.ingredient_id != old.ingredient_id:
                if not check_ingredient(index, op.ingredient_id):
                    continue
                if op.ingredient_id is None and not any(
                    a.role is (op.role or old.role) for a in draft.axes
                ):
                    refusals.append(
                        Refusal(
                            index,
                            f"the {old.role.value.lower()} slot needs an ingredient: only a slot a "
                            "flavour fills can be left empty",
                        )
                    )
                    continue
                old_unit = ctx.ingredient_units.get(old.ingredient_id or -1)
                new_unit = ctx.ingredient_units.get(op.ingredient_id or -1)
                if old_unit is not None and new_unit is not None and old_unit is not new_unit:
                    warnings.append(
                        f"{ing_name(old.ingredient_id)} is measured in {unit_label(old_unit)} and "
                        f"{ing_name(op.ingredient_id)} in {unit_label(new_unit)}: the quantities "
                        "are kept as numbers, so check them before applying"
                    )
                new = replace_component(new, ingredient_id=op.ingredient_id)
                diff.append(f"{ing_name(old.ingredient_id)} → {ing_name(op.ingredient_id)}")
            if op.role is not None and op.role is not old.role:
                new = replace_component(new, role=op.role)
                diff.append(
                    f"{comp_label(old)}: {old.role.value.lower()} → {op.role.value.lower()}"
                )
            if op.is_substitutable is not None and op.is_substitutable != old.is_substitutable:
                new = replace_component(new, is_substitutable=op.is_substitutable)
                diff.append(
                    f"{comp_label(new)} can now be swapped"
                    if op.is_substitutable
                    else f"{comp_label(new)} can no longer be swapped"
                )
                swaps = ctx.substitute_modifiers_by_role.get(new.role, ())
                if not op.is_substitutable and swaps:
                    warnings.append(
                        f"{', '.join(swaps)} swap the {new.role.value.lower()} slot: after this, "
                        "a sale of this recipe with one of those swaps is refused at stock "
                        "expansion rather than guessed at"
                    )
            if op.is_required is not None and op.is_required != old.is_required:
                new = replace_component(new, is_required=op.is_required)
                diff.append(
                    f"{comp_label(new)} is now required"
                    if op.is_required
                    else f"{comp_label(new)} is now optional"
                )
            work.components[at] = new

        elif isinstance(op, OpComponentAdd):
            if not check_ingredient(index, op.ingredient_id):
                continue
            if op.ingredient_id is None and not any(a.role is op.role for a in draft.axes):
                refusals.append(
                    Refusal(index, "pick an ingredient: only a slot a flavour fills can be empty")
                )
                continue
            if not check_sizes(index, list(op.qty_by_size)):
                continue
            if any(q < 0 for q in op.qty_by_size.values()):
                refusals.append(Refusal(index, "a quantity cannot be negative"))
                continue
            added = DraftComponent(
                key=work.next_key("c"),
                component_id=None,
                role=op.role,
                ingredient_id=op.ingredient_id,
                qty_by_size=dict(op.qty_by_size),
                is_substitutable=op.is_substitutable,
                is_required=op.is_required,
            )
            work.components.append(added)
            diff.append(f"Added {comp_label(added)}")

        elif isinstance(op, OpComponentRemove):
            at = work.component_index(op.component_id)
            if at is None:
                refusals.append(
                    Refusal(index, f"component {op.component_id} is not in this recipe")
                )
                continue
            removed = work.components.pop(at)
            diff.append(f"Removed {comp_label(removed)}")

        elif isinstance(op, OpPrepSet):
            if not check_sizes(index, list(op.prep_seconds_by_size)):
                continue
            if any(s <= 0 for s in op.prep_seconds_by_size.values()):
                refusals.append(
                    Refusal(index, "a prep time must be more than 0 seconds; clear it instead")
                )
                continue
            for size_key, seconds in sorted(op.prep_seconds_by_size.items()):
                old_seconds = work.prep.get(size_key)
                if old_seconds != seconds:
                    diff.append(
                        f"Prep {size_key}: "
                        f"{old_seconds if old_seconds is not None else '—'}s → {seconds}s"
                    )
                    work.prep[size_key] = seconds
            if op.is_estimate != bool(work.prep_est) or work.prep_est is None:
                if work.prep_est is not None or not op.is_estimate:
                    diff.append(
                        "Prep times marked as estimates"
                        if op.is_estimate
                        else "Prep times marked as timed"
                    )
                work.prep_est = op.is_estimate

        elif isinstance(op, OpBasePrice):
            if not check_sizes(index, list(op.base_price_pence_by_size)):
                continue
            if any(p < 0 for p in op.base_price_pence_by_size.values()):
                refusals.append(Refusal(index, "a price cannot be negative"))
                continue
            current = base_prices(work.freeze(draft))
            for size_key, new_base in sorted(op.base_price_pence_by_size.items()):
                old_base, _disagree = current.get(size_key, (None, False))
                changed_any = False
                for i, item in enumerate(work.items):
                    if (item.size_code.value if item.size_code else "ONE") != size_key:
                        continue
                    delta = sum(
                        o.price_delta_pence
                        for k in item.option_keys.values()
                        if (o := next((x for x in work.options if x.key == k), None)) is not None
                    )
                    new_price = new_base + delta
                    if new_price != item.price_pence:
                        work.items[i] = replace_item(item, price_pence=new_price)
                        changed_any = True
                if changed_any or old_base != new_base:
                    diff.append(
                        f"Price {size_key}: "
                        f"{gbp(old_base) if old_base is not None else '—'} → {gbp(new_base)}"
                    )

        elif isinstance(op, OpOptionSet):
            at = work.option_index(op.option_id)
            if at is None:
                refusals.append(Refusal(index, f"flavour {op.option_id} is not in this recipe"))
                continue
            old_option = work.options[at]
            option = old_option
            if op.name is not None and op.name.strip() and op.name.strip() != old_option.name:
                name = op.name.strip()
                if any(
                    o.name.casefold() == name.casefold()
                    and o.axis_id == old_option.axis_id
                    and not o.removed
                    and o.key != old_option.key
                    for o in work.options
                ):
                    refusals.append(Refusal(index, f"there is already a flavour called {name!r}"))
                    continue
                diff.append(f"{old_option.name} renamed {name}")
                option = replace_option(option, name=name)
            if op.set_ingredient and op.ingredient_id != old_option.ingredient_id:
                if not check_ingredient(index, op.ingredient_id):
                    continue
                diff.append(
                    f"{option.name}: {ing_name(old_option.ingredient_id)} → "
                    f"{ing_name(op.ingredient_id)}"
                )
                option = replace_option(option, ingredient_id=op.ingredient_id)
            if op.set_qty:
                if op.qty_by_size is not None and not check_sizes(index, list(op.qty_by_size)):
                    continue
                old_q = dict(old_option.qty_by_size or {})
                new_q = dict(op.qty_by_size or {})
                if old_q != new_q:
                    for size in draft.sizes:
                        a, b = old_q.get(size.value), new_q.get(size.value)
                        if a != b:
                            diff.append(
                                f"{option.name} {size.value}: "
                                f"{qty_text(a) if a is not None else 'recipe'} → "
                                f"{qty_text(b) if b is not None else 'recipe'}"
                            )
                    option = replace_option(option, qty_by_size=new_q or None, clear_qty=not new_q)
            if (
                op.price_delta_pence is not None
                and op.price_delta_pence != old_option.price_delta_pence
            ):
                change = op.price_delta_pence - old_option.price_delta_pence
                diff.append(
                    f"{option.name} extra {gbp(old_option.price_delta_pence)} → "
                    f"{gbp(op.price_delta_pence)}"
                )
                option = replace_option(option, price_delta_pence=op.price_delta_pence)
                for i, item in enumerate(work.items):
                    if old_option.key in item.option_keys.values():
                        work.items[i] = replace_item(item, price_pence=item.price_pence + change)
            if op.set_season and op.season_id != old_option.season_id:
                if op.season_id is not None and op.season_id not in ctx.season_names:
                    refusals.append(Refusal(index, f"season {op.season_id} does not exist"))
                    continue
                label = (
                    ctx.season_names.get(op.season_id, "all year") if op.season_id else "all year"
                )
                diff.append(f"{option.name} season → {label}")
                option = replace_option(
                    option, season_id=op.season_id, clear_season=op.season_id is None
                )
            work.options[at] = option

        elif isinstance(op, OpOptionAdd):
            axis = draft.axis(op.axis_id)
            name = op.name.strip()
            if axis is None:
                refusals.append(Refusal(index, f"axis {op.axis_id} is not in this recipe"))
                continue
            if not name:
                refusals.append(Refusal(index, "a flavour needs a name"))
                continue
            if any(
                o.axis_id == op.axis_id and o.name.casefold() == name.casefold() and not o.removed
                for o in work.options
            ):
                refusals.append(Refusal(index, f"there is already a flavour called {name!r}"))
                continue
            if not check_ingredient(index, op.ingredient_id):
                continue
            if op.qty_by_size is not None and not check_sizes(index, list(op.qty_by_size)):
                continue
            if op.season_id is not None and op.season_id not in ctx.season_names:
                refusals.append(Refusal(index, f"season {op.season_id} does not exist"))
                continue
            new_name = item_name(name)
            taken = [
                s.value
                for s in draft.sizes
                if (new_name.casefold(), s.value) in ctx.taken_item_names
                or any(
                    it.name.casefold() == new_name.casefold() and it.size_code is s
                    for it in work.items
                )
            ]
            if taken:
                refusals.append(
                    Refusal(
                        index,
                        f"a menu item called {new_name!r} already exists ({', '.join(taken)}). "
                        "Moving an existing item onto this recipe would change what its past "
                        "sales are deemed to have used, so it is not done here -- name the "
                        "flavour differently, or confirm that item's recipe separately",
                    )
                )
                continue
            key = work.next_key("o")
            work.options.append(
                DraftOption(
                    key=key,
                    option_id=None,
                    axis_id=op.axis_id,
                    name=name,
                    ingredient_id=op.ingredient_id,
                    qty_by_size=dict(op.qty_by_size) if op.qty_by_size else None,
                    price_delta_pence=op.price_delta_pence,
                    season_id=op.season_id,
                )
            )
            base = base_prices(work.freeze(draft))
            for size in draft.sizes:
                base_price, _ = base.get(size.value, (None, False))
                work.items.append(
                    DraftItem(
                        key=work.next_key("i"),
                        menu_item_id=None,
                        name=new_name,
                        size_code=size,
                        option_keys={op.axis_id: key},
                        price_pence=(base_price or 0) + op.price_delta_pence,
                        active=True,
                        on_till=False,
                    )
                )
            diff.append(f"New flavour: {name}")
            pos.append(
                f"Add {new_name} ({', '.join(_size_word(s) for s in draft.sizes)}) in Lightspeed "
                "too, or their sales will not be counted."
            )

        elif isinstance(op, OpOptionActive):
            at = work.option_index(op.option_id)
            if at is None:
                refusals.append(Refusal(index, f"flavour {op.option_id} is not in this recipe"))
                continue
            option = work.options[at]
            touched = False
            for i, item in enumerate(work.items):
                if option.key in item.option_keys.values() and item.active != op.active:
                    work.items[i] = replace_item(item, active=op.active)
                    touched = True
            if touched:
                diff.append(
                    f"{option.name} back on the menu"
                    if op.active
                    else f"{option.name} off the menu"
                )

        elif isinstance(op, OpOptionRemove):
            at = work.option_index(op.option_id)
            if at is None:
                refusals.append(Refusal(index, f"flavour {op.option_id} is not in this recipe"))
                continue
            option = work.options[at]
            work.options[at] = replace_option(option, removed=True)
            for i, item in enumerate(work.items):
                if option.key in item.option_keys.values() and item.active:
                    work.items[i] = replace_item(item, active=False)
            diff.append(f"Removed flavour: {option.name}")

    after = work.freeze(draft)

    # Warnings about the state the changeset leaves, not about any one operation.
    flavour_axes = {a.axis_id for a in draft.axes}
    missing = [
        o.name
        for o in after.options
        if not o.removed
        and o.axis_id in flavour_axes
        and o.ingredient_id is None
        and any(it.active and o.key in it.option_keys.values() for it in after.items)
    ]
    if missing:
        warnings.append(
            f"{len(missing)} flavour{'s have' if len(missing) != 1 else ' has'} no flavour "
            f"ingredient, so {'their' if len(missing) != 1 else 'its'} cost is too low: "
            + ", ".join(missing)
        )
    if any(it.active and it.price_pence <= 0 for it in after.items):
        warnings.append("A size has no price.")
    if after.prep_is_estimate is not False and after.prep_seconds_by_size:
        warnings.append("Prep times are estimates, so staff-time figures are too.")

    return ChangesetOutcome(
        before=draft,
        after=after,
        diff=tuple(diff),
        refusals=tuple(refusals),
        warnings=tuple(dict.fromkeys(warnings)),
        pos_actions=tuple(pos),
    )


def replace_component(
    c: DraftComponent,
    *,
    role: ComponentRole | None = None,
    ingredient_id: int | object | None = ...,
    qty_by_size: Mapping[str, Decimal] | None = None,
    is_substitutable: bool | None = None,
    is_required: bool | None = None,
) -> DraftComponent:
    return DraftComponent(
        key=c.key,
        component_id=c.component_id,
        role=role if role is not None else c.role,
        ingredient_id=(c.ingredient_id if ingredient_id is ... else _opt_int(ingredient_id)),
        qty_by_size=qty_by_size if qty_by_size is not None else c.qty_by_size,
        is_substitutable=c.is_substitutable if is_substitutable is None else is_substitutable,
        is_required=c.is_required if is_required is None else is_required,
    )


def replace_option(
    o: DraftOption,
    *,
    name: str | None = None,
    ingredient_id: int | object | None = ...,
    qty_by_size: Mapping[str, Decimal] | None = None,
    clear_qty: bool = False,
    price_delta_pence: int | None = None,
    season_id: int | None = None,
    clear_season: bool = False,
    removed: bool | None = None,
) -> DraftOption:
    return DraftOption(
        key=o.key,
        option_id=o.option_id,
        axis_id=o.axis_id,
        name=name if name is not None else o.name,
        ingredient_id=o.ingredient_id if ingredient_id is ... else _opt_int(ingredient_id),
        qty_by_size=None
        if clear_qty
        else (qty_by_size if qty_by_size is not None else o.qty_by_size),
        price_delta_pence=o.price_delta_pence if price_delta_pence is None else price_delta_pence,
        season_id=None if clear_season else (season_id if season_id is not None else o.season_id),
        removed=o.removed if removed is None else removed,
    )


def replace_item(
    item: DraftItem, *, price_pence: int | None = None, active: bool | None = None
) -> DraftItem:
    return DraftItem(
        key=item.key,
        menu_item_id=item.menu_item_id,
        name=item.name,
        size_code=item.size_code,
        option_keys=item.option_keys,
        price_pence=item.price_pence if price_pence is None else price_pence,
        active=item.active if active is None else active,
        prep_seconds=item.prep_seconds,
        prep_is_estimate=item.prep_is_estimate,
        on_till=item.on_till,
    )


def _opt_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise TypeError(f"expected an int id or None, got {value!r}")
