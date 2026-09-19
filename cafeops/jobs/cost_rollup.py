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

v2 adds **labour** (spec 5.6). Three more columns get written --
`labour_cost_pence`, `prep_seconds` and `loaded_hourly_rate_pence` -- and two rules
come with them:

- **The rate is stored per row**, not looked up at read time. When the owner puts the
  loaded rate up, last month's reported margins must not silently change; the audit
  trail is the point of the column.
- **All three are NULL together** when prep time or the rate is unset. The rate is
  read from `config` here, at the edge, and handed to `domain/` as an argument --
  `domain/` never imports config, and a labour figure computed from a guessed rate is
  a guess wearing a number's clothes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.repositories.composition import SqlCompositionRepository
from cafeops.db.repositories.ingredient import SqlIngredientRepository
from cafeops.db.repositories.menu_cost import SqlMenuCostRepository
from cafeops.domain.composition import resolve_recipe
from cafeops.domain.labour import UNTIMED
from cafeops.domain.types import (
    IngredientSnapshot,
    PriceSource,
    ResolvedRecipe,
    SizeCode,
    SubstitutionError,
)

__all__ = [
    "RollupReport",
    "configured_rate_pence",
    "rollup_all",
    "rollup_for_ingredient",
    "rollup_for_template",
    "rollup_menu_items",
    "snapshots_at",
]


def configured_rate_pence() -> int | None:
    """The loaded hourly rate from config, with 0 or absent read as UNSET.

    This is the only place in the cost path that reads the rate from settings, and it
    is deliberately at the edge: `domain/labour.py` takes the rate as an argument so
    it can be asked "and what if it were GBP 15.50?".

    A rate of 0 is treated as missing rather than as free labour. There is no way to
    ask a rollup to "pretend the rate is unset" -- clear
    `CAFEOPS_LOADED_HOURLY_RATE_PENCE` instead. A rate is either configured or it is
    not, and a flag that made it conditionally invisible would let one caller write
    NULL labour into a cache every other caller reads as authoritative.
    """
    rate = settings.loaded_hourly_rate_pence
    return rate if rate is not None and rate > 0 else None


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
    # --- labour (spec 5.6) ---------------------------------------------------
    with_labour: int = 0
    without_prep_time: int = 0
    loaded_hourly_rate_pence: int | None = None
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
        if self.loaded_hourly_rate_pence is None:
            # Not "labour 0" -- unknown. The same wording invariant 6 needs for cost.
            parts.append(
                "NO loaded hourly rate configured, so every labour figure is UNKNOWN "
                "(set CAFEOPS_LOADED_HOURLY_RATE_PENCE)"
            )
        else:
            parts.append(
                f"{self.with_labour} with labour at GBP "
                f"{self.loaded_hourly_rate_pence / 100:.2f}/hr"
            )
            if self.without_prep_time:
                parts.append(f"{self.without_prep_time} with NO prep time (labour UNKNOWN)")
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
    rate = configured_rate_pence()
    report = RollupReport(
        trigger=trigger,
        at=at,
        considered=len(menu_item_ids),
        loaded_hourly_rate_pence=rate,
    )
    if not menu_item_ids:
        return report

    composition = SqlCompositionRepository(session)
    costs = SqlMenuCostRepository(session)
    snapshots = snapshots if snapshots is not None else snapshots_at(session, at)

    ids = list(menu_item_ids)
    specs = composition.item_specs(ids, at)
    prep_times = composition.prep_times(ids)
    # Seasons reach resolution as a side channel rather than as fields on
    # MenuItemSpec, which is integrator-owned. They only ever add a warning -- see
    # `domain/composition.py` for why an out-of-season item must still resolve.
    option_seasons = composition.option_seasons_for_items(ids)
    item_seasons = composition.item_seasons(ids)
    computed_at = datetime.now(UTC)

    for menu_item_id in menu_item_ids:
        spec = specs.get(menu_item_id)
        if spec is None:
            report.skipped_no_spec += 1
            report.warnings.append(f"menu item {menu_item_id} no longer exists; not recosted")
            continue
        try:
            recipe = resolve_recipe(
                spec,
                (),
                at,
                ingredients=snapshots,
                option_seasons=option_seasons.get(menu_item_id),
                item_season=item_seasons.get(menu_item_id),
            )
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

        prep = prep_times.get(menu_item_id, UNTIMED)
        costs.upsert(
            menu_item_id,
            recipe,
            computed_at,
            prep=prep,
            loaded_hourly_rate_pence=rate,
        )
        report.costed += 1
        if prep.is_known and rate is not None:
            report.with_labour += 1
        elif not prep.is_known:
            report.without_prep_time += 1
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
