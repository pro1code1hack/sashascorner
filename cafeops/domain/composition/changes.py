"""`cafeops.domain.composition`, the changes section.

Split from one 2,270-line module on 2026-09-29 (ARCHITECTURE 8Y). Import the public
names from the package; this module is an implementation detail of it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from cafeops.domain.composition.display import _size_word
from cafeops.domain.composition.impact import _fingerprint, _uniform
from cafeops.domain.labour import (
    UNTIMED,
    PrepTime,
    labour_for,
)
from cafeops.domain.types import (
    PriceSource,
    ResolvedRecipe,
    SizeCode,
)


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
