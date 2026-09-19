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
)
from cafeops.domain.stock import apply_waste
from cafeops.domain.types import (
    ComponentRole,
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
    VariantOptionSpec,
)

__all__ = [
    "Availability",
    "ItemAvailability",
    "ItemImpact",
    "LabourImpact",
    "LabourImpactedItem",
    "OptionSeason",
    "availability_at",
    "preview_impact",
    "preview_labour_impact",
    "resolve_recipe",
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
