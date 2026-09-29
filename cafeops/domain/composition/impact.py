"""`cafeops.domain.composition`, the impact section.

Split from one 2,270-line module on 2026-09-29 (ARCHITECTURE 8Y). Import the public
names from the package; this module is an implementation detail of it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from cafeops.domain.labour import (
    UNTIMED,
    ItemLabour,
    MarginRanking,
    PrepTime,
    labour_for,
    rank_menu,
)
from cafeops.domain.types import (
    ImpactedItem,
    ImpactPreview,
    ResolvedRecipe,
    SizeCode,
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
