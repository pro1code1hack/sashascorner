"""The cost cascade. Spec 5.5.

    ingredient_price change -> template_component using it
                            -> menu_item resolving through those templates
                            -> menu_item_cost -> margin, P&L COGS

`menu_item_cost` is a cache, and this module is the only thing that writes it. It
runs on every composition edit and on a schedule, because 318 resolutions must never
happen inside a request handler.

Three rules the rest of the system depends on:

1. **Costs come from `ResolvedRecipe.lines`, never `depletion_lines`** (invariant 5).
   Waste is a stock concern. A latte does not cost more because milk foams; more milk
   leaves the fridge, which is a different number in a different table.
2. **`cost_source` is the WEAKEST source among the ingredients** -- one estimated
   price makes the whole item an estimate, and one missing price makes the whole item
   unknown. `ResolvedRecipe.cost_source` already computes that; this module stores it
   without softening it (invariant 6).
3. **Prices are read effective-dated at `at`**, not from the denormalised cache on
   `ingredient`. Rolling up "as of last month" with today's prices would produce a
   number that matches no invoice anyone has.

Nothing here commits. The caller owns the transaction, so a rollup triggered by an
edit lands in the same unit of work as the edit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from cafeops.db.repositories.composition import SqlCompositionRepository
from cafeops.db.repositories.ingredient import SqlIngredientRepository
from cafeops.db.repositories.menu_cost import SqlMenuCostRepository
from cafeops.domain.composition import resolve_recipe
from cafeops.domain.types import (
    IngredientSnapshot,
    PriceSource,
    ResolvedRecipe,
    SizeCode,
    SubstitutionError,
)

__all__ = [
    "RollupReport",
    "rollup_all",
    "rollup_for_ingredient",
    "rollup_for_template",
    "rollup_menu_items",
    "snapshots_at",
]


@dataclass
class RollupReport:
    """What one rollup did, in enough detail to distrust it if it deserves it."""

    trigger: str
    at: datetime
    considered: int = 0
    costed: int = 0
    priced: int = 0
    missing_cost: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    skipped_no_spec: int = 0
    substitution_errors: int = 0
    templates_in_scope: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def estimated(self) -> int:
        return self.by_source.get(PriceSource.ESTIMATE.value, 0)

    @property
    def invoiced(self) -> int:
        return self.by_source.get(PriceSource.INVOICE.value, 0)

    def summary(self) -> str:
        parts = [
            f"{self.trigger}: recosted {self.costed} of {self.considered} menu item(s)",
            f"{self.priced} fully priced",
        ]
        if self.missing_cost:
            # Not "0 cost" -- unknown. Invariant 6 lives or dies on this wording.
            parts.append(f"{self.missing_cost} with an UNKNOWN cost (excluded from aggregates)")
        if self.estimated:
            parts.append(f"{self.estimated} priced from ESTIMATEs")
        if self.invoiced:
            parts.append(f"{self.invoiced} from invoices")
        if self.skipped_no_spec:
            parts.append(f"{self.skipped_no_spec} had no resolvable recipe")
        if self.substitution_errors:
            parts.append(f"{self.substitution_errors} substitution error(s)")
        return "; ".join(parts)


def snapshots_at(session: Session, at: datetime) -> dict[int, IngredientSnapshot]:
    """Every ingredient with the price that was in force at `at`.

    `IngredientSnapshot` normally carries the denormalised current cost. Here it is
    overwritten with the effective-dated one, so a rollup at a past date costs the
    recipe the way it actually cost then. An unpriced ingredient keeps
    `cost_per_unit_pence=None` -- never zero (invariant 6).
    """
    ingredients = SqlIngredientRepository(session)
    out: dict[int, IngredientSnapshot] = {}
    for snapshot in ingredients.list_all():
        priced = ingredients.cost_per_unit_at(snapshot.id, at)
        out[snapshot.id] = replace(
            snapshot,
            cost_per_unit_pence=None if priced is None else priced[0],
            cost_source=None if priced is None else priced[1],
        )
    return out


def rollup_menu_items(
    session: Session,
    menu_item_ids: Sequence[int],
    *,
    at: datetime | None = None,
    trigger: str = "explicit",
    snapshots: dict[int, IngredientSnapshot] | None = None,
) -> RollupReport:
    """Re-resolve each item and write its cost through to `menu_item_cost`.

    Resolution is WITHOUT modifiers: `menu_item_cost` is the cost of the item as the
    menu sells it. An oat latte's cost differs, and that difference belongs to the
    sale, not to the menu item.
    """
    at = at or datetime.now(UTC)
    report = RollupReport(trigger=trigger, at=at, considered=len(menu_item_ids))
    if not menu_item_ids:
        return report

    composition = SqlCompositionRepository(session)
    costs = SqlMenuCostRepository(session)
    snapshots = snapshots if snapshots is not None else snapshots_at(session, at)

    specs = composition.item_specs(list(menu_item_ids), at)
    computed_at = datetime.now(UTC)

    for menu_item_id in menu_item_ids:
        spec = specs.get(menu_item_id)
        if spec is None:
            report.skipped_no_spec += 1
            report.warnings.append(f"menu item {menu_item_id} no longer exists; not recosted")
            continue
        try:
            recipe = resolve_recipe(spec, (), at, ingredients=snapshots)
        except SubstitutionError as exc:
            # Cannot happen without modifiers, but a raise here would abort a whole
            # nightly rollup over one bad row. Record it and keep going.
            report.substitution_errors += 1
            report.warnings.append(f"{spec.name}: {exc}")
            continue

        if not recipe.lines:
            # No recipe at this date: a gift card, an unconfirmed one-off. Its cached
            # cost is deleted rather than written as zero -- "this item costs nothing"
            # and "nobody has told us what this contains" are different claims.
            costs.delete(menu_item_id)
            report.skipped_no_spec += 1
            continue

        costs.upsert(menu_item_id, recipe, computed_at)
        report.costed += 1
        if recipe.has_missing_cost:
            report.missing_cost += 1
            report.warnings.append(
                f"{_label(spec.name, spec.size_code)}: cost UNKNOWN -- no price at "
                f"{at.date().isoformat()} for {', '.join(_unpriced_names(recipe))}"
            )
        else:
            report.priced += 1
        source = recipe.cost_source
        if source is not None:
            report.by_source[source.value] = report.by_source.get(source.value, 0) + 1

    return report


def rollup_all(session: Session, *, at: datetime | None = None) -> RollupReport:
    """Every active menu item. The scheduled entry point."""
    composition = SqlCompositionRepository(session)
    return rollup_menu_items(
        session,
        composition.all_menu_item_ids(),
        at=at,
        trigger="scheduled full rollup",
    )


def rollup_for_template(
    session: Session,
    template_id: int,
    *,
    at: datetime | None = None,
    trigger: str = "template edit",
) -> RollupReport:
    composition = SqlCompositionRepository(session)
    item_ids = composition.menu_item_ids_for_template(template_id)
    report = rollup_menu_items(session, item_ids, at=at, trigger=trigger)
    report.templates_in_scope = 1
    return report


def rollup_for_ingredient(
    session: Session,
    ingredient_id: int,
    *,
    at: datetime | None = None,
    trigger: str = "ingredient price change",
) -> RollupReport:
    """The cascade, walked in one direction only.

    ingredient -> the templates whose components or variant options use it -> the
    menu items resolving through those templates, plus the manual-recipe items that
    name it directly. Items reached by neither route cannot have changed cost, so
    recosting them would be work that proves nothing.
    """
    composition = SqlCompositionRepository(session)
    template_ids = composition.template_ids_using_ingredient(ingredient_id)

    item_ids: set[int] = set()
    for template_id in template_ids:
        item_ids.update(composition.menu_item_ids_for_template(template_id))
    item_ids.update(composition.menu_item_ids_using_ingredient_manually(ingredient_id))

    report = rollup_menu_items(session, sorted(item_ids), at=at, trigger=trigger)
    report.templates_in_scope = len(template_ids)
    return report


# --------------------------------------------------------------------------


def _unpriced_names(recipe: ResolvedRecipe) -> list[str]:
    return sorted({line.ingredient_name for line in recipe.cost_breakdown if line.is_missing_cost})


def _label(name: str, size_code: SizeCode | None) -> str:
    return f"{name} {size_code.value}" if size_code is not None else name
