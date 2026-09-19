"""Turn sold lines into SALE ledger movements.

PHASE 0 SCOPE NOTE: owned by Agent C. Phase 0 implements the straightforward path
because `seed --demo` and `stock --as-of` are meaningless without it.

Two things worth keeping whatever Agent C does to the rest:

1. **Resolution uses the sale's own date.** `resolve_recipe(..., at=sale.sold_at)`,
   not `at=now`. Invariant 3: recomputing March's consumption with today's recipe
   produces wrong history and destroys the drift metric.
2. **Idempotency is `sale.expanded_at`**, set in the same transaction that writes
   the movements. A crash mid-run leaves the ledger and the flag consistent, so a
   re-run redoes the whole batch or none of it, never half.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from cafeops.db.repositories.composition import SqlCompositionRepository
from cafeops.db.repositories.ingredient import SqlIngredientRepository
from cafeops.db.repositories.sale import SqlSaleRepository
from cafeops.db.repositories.stock import SqlStockRepository
from cafeops.domain.composition import resolve_recipe
from cafeops.domain.stock import depletion_movements
from cafeops.domain.types import SubstitutionError


@dataclass
class ExpansionReport:
    sales_expanded: int = 0
    movements_written: int = 0
    skipped_no_recipe: int = 0
    substitution_errors: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"expanded {self.sales_expanded} sale lines",
            f"wrote {self.movements_written} movements",
        ]
        if self.skipped_no_recipe:
            parts.append(f"{self.skipped_no_recipe} had no recipe")
        if self.substitution_errors:
            parts.append(f"{self.substitution_errors} substitution error(s)")
        return "; ".join(parts)


def expand_pending(
    session: Session, *, limit: int | None = None, only_tracked: bool = True
) -> ExpansionReport:
    """Expand every unexpanded, non-voided sale line into the ledger."""
    report = ExpansionReport()

    sales_repo = SqlSaleRepository(session)
    composition_repo = SqlCompositionRepository(session)
    ingredient_repo = SqlIngredientRepository(session)
    stock_repo = SqlStockRepository(session)

    sales = sales_repo.pending_expansion(limit=limit)
    if not sales:
        return report

    snapshots = ingredient_repo.snapshots_by_id()
    waste_factors = {i: s.waste_factor for i, s in snapshots.items()}
    tracked_ids = {s.id for s in snapshots.values() if s.tracking_enabled} if only_tracked else None

    expanded_ids: list[int] = []
    for sale in sales:
        # Resolution is per-sale because it is date-dependent. Grouping by
        # (menu_item, date) would be the optimisation if this ever gets slow.
        spec = composition_repo.item_spec(sale.menu_item_id, sale.sold_at)
        if spec is None:
            report.skipped_no_recipe += 1
            expanded_ids.append(sale.sale_id)
            continue

        modifiers = composition_repo.modifiers(list(sale.modifier_ids))
        try:
            recipe = resolve_recipe(spec, modifiers, sale.sold_at, ingredients=snapshots)
        except SubstitutionError as exc:
            # Do NOT mark expanded: this is a data error a human must fix, and
            # silently dropping the sale would lose real consumption.
            report.substitution_errors += 1
            report.warnings.append(f"sale {sale.sale_id}: {exc}")
            continue

        if not recipe.lines:
            # No recipe at this date: a gift card, a meal deal, an unconfigured
            # one-off. Mark expanded anyway -- it depletes nothing, and leaving it
            # pending would make the queue grow without bound.
            report.skipped_no_recipe += 1
            expanded_ids.append(sale.sale_id)
            continue

        movements = depletion_movements(
            sale_id=sale.sale_id,
            sold_qty=sale.qty,
            sold_at=sale.sold_at,
            # depletion_lines already carry waste; pass the recipe lines and let
            # the ledger apply waste once, from a single place.
            lines=recipe.lines,
            waste_factors=waste_factors,
            tracked_ingredient_ids=tracked_ids,
        )
        report.movements_written += stock_repo.append_movements(movements)
        expanded_ids.append(sale.sale_id)

    sales_repo.mark_expanded(expanded_ids, datetime.now(UTC))
    report.sales_expanded = len(expanded_ids)
    return report
