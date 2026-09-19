"""Turn sold lines into SALE ledger movements, and undo them when the data changes.

Four things are load-bearing here.

1. **Resolution uses the sale's own date.** `resolve_recipe(..., at=sale.sold_at)`,
   not `at=now`. Invariant 3: recomputing March's consumption with today's recipe
   produces wrong history and destroys the drift metric.
2. **Idempotency is `sale.expanded_at`**, set in the same transaction that writes
   the movements. A crash mid-run leaves the ledger and the flag consistent, so a
   re-run redoes the whole batch or none of it, never half.
3. **Refunds net out by sign.** A refund line carries a negative `qty`, so
   `depletion_movements` negates it into a POSITIVE movement and the stock goes back on
   the shelf. Nothing special-cases refunds, which is why nothing can forget to.
4. **A `SubstitutionError` sale is NOT marked expanded.** A modifier that tried to
   substitute a non-substitutable slot is a data defect a human must fix. Marking it
   expanded would drop consumption that really happened; writing the unmodified recipe
   would deplete the wrong ingredient. So it stays in the queue, is reported, and the
   next run picks it up once the data is corrected.

Corrections never amend the ledger (invariant 9). `reverse_expansion` appends
equal-and-opposite `ADJUSTMENT` rows instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models import Sale, StockMovement
from cafeops.db.repositories.composition import SqlCompositionRepository
from cafeops.db.repositories.ingredient import SqlIngredientRepository
from cafeops.db.repositories.sale import SqlSaleRepository
from cafeops.db.repositories.stock import SqlStockRepository
from cafeops.domain.composition import resolve_recipe
from cafeops.domain.stock import depletion_movements, reversal_movements
from cafeops.domain.types import MovementSpec, MovementType, SubstitutionError

#: `stock_movement.ref_type` values. A reversal is retyped rather than reusing "sale",
#: so `_already_reversed` can tell a correction from the thing it corrected and no
#: second run reverses the same sale twice.
SALE_REF = "sale"
SALE_REVERSAL_REF = "sale_reversal"


@dataclass
class ExpansionReport:
    sales_expanded: int = 0
    movements_written: int = 0
    skipped_no_recipe: int = 0
    substitution_errors: int = 0
    refund_lines: int = 0
    #: Sales left in the queue on purpose. A human has to fix the data behind these.
    blocked_sale_ids: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"expanded {self.sales_expanded} sale lines",
            f"wrote {self.movements_written} movements",
        ]
        if self.refund_lines:
            parts.append(f"{self.refund_lines} refund line(s) returned stock")
        if self.skipped_no_recipe:
            parts.append(f"{self.skipped_no_recipe} had no recipe")
        if self.substitution_errors:
            parts.append(
                f"{self.substitution_errors} substitution error(s) LEFT PENDING for a human"
            )
        return "; ".join(parts)


@dataclass
class ReversalReport:
    """The append-only correction path. Invariant 9: nothing is updated or deleted."""

    sales_reversed: int = 0
    adjustments_written: int = 0
    already_reversed: int = 0
    nothing_to_reverse: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"reversed {self.sales_reversed} sale(s)",
            f"wrote {self.adjustments_written} ADJUSTMENT movements",
        ]
        if self.already_reversed:
            parts.append(f"{self.already_reversed} already reversed")
        if self.nothing_to_reverse:
            parts.append(f"{self.nothing_to_reverse} had no movements to reverse")
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
            report.blocked_sale_ids.append(sale.sale_id)
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
        if sale.qty < 0:
            # A refund. The sign is authoritative and already inverted every movement;
            # counted here only so the report can say it happened rather than leaving a
            # run that put stock BACK looking identical to one that took stock off.
            report.refund_lines += 1
        report.movements_written += stock_repo.append_movements(movements)
        expanded_ids.append(sale.sale_id)

    sales_repo.mark_expanded(expanded_ids, datetime.now(UTC))
    report.sales_expanded = len(expanded_ids)
    return report


def reverse_expansion(
    session: Session,
    *,
    sale_ids: Sequence[int],
    reason: str,
    at: datetime | None = None,
) -> ReversalReport:
    """Undo the expansion of specific sales by APPENDING `ADJUSTMENT` movements.

    INVARIANT 9. The original `SALE` rows stay exactly as written -- a receipt that was
    voided after it was expanded, or a window expanded twice, is corrected by an equal
    and opposite entry, never by an `UPDATE` or a `DELETE`. The ledger sums to the right
    number and still records what actually happened and when it was fixed.

    `sale.expanded_at` is deliberately left set. Clearing it would put the sale back in
    the expansion queue, and the next run would re-write the movements the reversal just
    cancelled.

    The two queries here belong in `StockRepository` (`movements_for(ref_type, ref_id)`)
    and `SaleRepository`; raised for the integrator rather than editing another agent's
    module.
    """
    report = ReversalReport()
    if not sale_ids:
        return report

    at = at or datetime.now(UTC)
    stock_repo = SqlStockRepository(session)
    reversed_already = _already_reversed(session, sale_ids)
    by_sale = _sale_movements(session, sale_ids)

    for sale_id in sale_ids:
        if sale_id in reversed_already:
            report.already_reversed += 1
            continue
        originals = by_sale.get(sale_id, [])
        if not originals:
            report.nothing_to_reverse += 1
            continue
        adjustments = reversal_movements(
            originals, occurred_at=at, note=reason, ref_type=SALE_REVERSAL_REF
        )
        report.adjustments_written += stock_repo.append_movements(adjustments)
        report.sales_reversed += 1
    return report


def reverse_voided_expansions(
    session: Session, *, at: datetime | None = None, limit: int | None = None
) -> ReversalReport:
    """Reverse every sale that was voided AFTER it had been expanded.

    `SaleRepository.pending_expansion` already keeps voided lines out of the queue, but
    that only helps lines voided before expansion ran. A receipt voided a day later has
    real `SALE` movements sitting in the ledger depleting stock that was never sold, and
    nothing else in the system would ever notice.
    """
    stmt = (
        select(Sale.id)
        .where(Sale.voided.is_(True), Sale.expanded_at.is_not(None))
        .order_by(Sale.sold_at, Sale.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    sale_ids = list(session.scalars(stmt))
    return reverse_expansion(
        session,
        sale_ids=sale_ids,
        reason="receipt voided after expansion",
        at=at,
    )


def _sale_movements(session: Session, sale_ids: Sequence[int]) -> dict[int, list[MovementSpec]]:
    rows = session.scalars(
        select(StockMovement).where(
            StockMovement.ref_type == SALE_REF,
            StockMovement.ref_id.in_(list(sale_ids)),
            StockMovement.type == MovementType.SALE,
        )
    )
    grouped: dict[int, list[MovementSpec]] = {}
    for row in rows:
        if row.ref_id is None:  # pragma: no cover - ref_id is set by expansion
            continue
        grouped.setdefault(row.ref_id, []).append(
            MovementSpec(
                ingredient_id=row.ingredient_id,
                type=row.type,
                qty=row.qty,
                occurred_at=row.occurred_at,
                ref_type=row.ref_type,
                ref_id=row.ref_id,
                note=row.note,
            )
        )
    return grouped


def _already_reversed(session: Session, sale_ids: Sequence[int]) -> set[int]:
    rows = session.scalars(
        select(StockMovement.ref_id).where(
            StockMovement.ref_type == SALE_REVERSAL_REF,
            StockMovement.ref_id.in_(list(sale_ids)),
        )
    )
    return {r for r in rows if r is not None}
