"""Transaction boundary for Lightspeed ingestion.

Idempotent on `sale.lightspeed_line_id` (already unique in the schema -- see
`cafeops/db/models/sale.py`). This module owns the `sale` table only; it never
writes `stock_movement` rows for a brand-new sale -- that stays
`services/expand_recipes.py`'s job, run afterwards by `cafeops expand`.

The one case where this module DOES touch the ledger is a sale that was
already expanded and is then corrected by a re-sync (spec: voided upstream
after the fact, a quantity correction, a modifier correction). INVARIANT 9:
the ledger is append-only. The original SALE movements are never mutated or
deleted; the difference between what was depleted and what should now be
depleted is written as one `ADJUSTMENT` movement per affected ingredient.

Kept deliberately free of SQLAlchemy repository classes with mutating
signatures this module would otherwise have to add to `db/repositories/` --
`db/repositories/sale.py` is shared infrastructure this agent does not own, so
the (small) amount of session/query code needed for the upsert lives here
instead of a new repository method.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models import Sale, StockMovement
from cafeops.db.repositories.composition import SqlCompositionRepository
from cafeops.db.repositories.ingredient import SqlIngredientRepository
from cafeops.db.repositories.stock import SqlStockRepository
from cafeops.domain.composition import resolve_recipe
from cafeops.domain.stock import depletion_movements
from cafeops.domain.types import MovementSpec, MovementType, SubstitutionError
from cafeops.integrations.lightspeed.mapper import MappedSaleLine

__all__ = ["IngestReport", "ingest_sale_lines"]


#: How far ahead of `now` a `sold_at` may be and still be believed. A till whose
#: clock is a few minutes fast is ordinary; refusing those sales would drop real
#: trade. Beyond this the timestamp is not a skew, it is wrong, and a sale dated
#: into next week would sit in the ledger depleting stock on a day that has not
#: happened -- invisible to today's drift and to today's order.
CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)


@dataclass
class IngestReport:
    inserted: int = 0
    unchanged: int = 0
    corrected_before_expansion: int = 0
    corrected_after_expansion: int = 0
    adjustment_movements: int = 0
    skew_refused: int = 0
    unresolved_items: list[str] = field(default_factory=list)
    unresolved_modifiers: list[str] = field(default_factory=list)
    substitution_errors: list[str] = field(default_factory=list)
    #: Lines dated further into the future than `CLOCK_SKEW_TOLERANCE`. Not written.
    clock_skew_refused: list[str] = field(default_factory=list)
    #: Lines inside the tolerance. Written, but said out loud once per run: a till
    #: clock drifting is worth knowing about before it drifts past the tolerance.
    clock_skew_tolerated: list[str] = field(default_factory=list)
    #: The same `lightspeed_line_id` twice in ONE call. Collapsed to the first copy
    #: when identical, refused when not -- see `ingest_sale_lines`.
    duplicate_line_ids: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"inserted {self.inserted}",
            f"unchanged {self.unchanged}",
        ]
        if self.corrected_before_expansion:
            parts.append(f"corrected (pre-expansion) {self.corrected_before_expansion}")
        if self.corrected_after_expansion:
            parts.append(
                f"corrected (post-expansion) {self.corrected_after_expansion}, "
                f"{self.adjustment_movements} ADJUSTMENT movement(s)"
            )
        if self.unresolved_items:
            parts.append(f"{len(self.unresolved_items)} unresolved item(s)")
        if self.unresolved_modifiers:
            parts.append(f"{len(self.unresolved_modifiers)} unresolved modifier reference(s)")
        if self.substitution_errors:
            parts.append(f"{len(self.substitution_errors)} substitution error(s)")
        if self.clock_skew_refused:
            parts.append(f"{len(self.clock_skew_refused)} future-dated line(s) REFUSED")
        if self.clock_skew_tolerated:
            parts.append(f"{len(self.clock_skew_tolerated)} line(s) within clock-skew tolerance")
        if self.duplicate_line_ids:
            parts.append(f"{len(self.duplicate_line_ids)} duplicate line id(s) in one payload")
        return "; ".join(parts)


def ingest_sale_lines(
    session: Session,
    lines: Sequence[MappedSaleLine],
    *,
    now: datetime,
    skew_tolerance: timedelta = CLOCK_SKEW_TOLERANCE,
) -> IngestReport:
    """Upsert `MappedSaleLine`s into `sale`, keyed on `lightspeed_line_id`.

    A line whose `menu_item_id` is None (the matcher could not resolve it) is
    never written -- an unmatched item silently depleting nothing would look
    like success. It is reported instead, loudly.

    Two guards on the payload itself, before anything is written:

    * **a line id twice in one call** -- collapsed when the copies agree (overlapping
      pages do this), refused when they do not (see `_collapse_duplicate_lines`);
    * **a `sold_at` in the future** -- tolerated up to `skew_tolerance` and refused
      beyond it. A till clock minutes fast is ordinary; a sale dated next Tuesday is
      not, and it would sit in the ledger depleting stock on a day that has not
      happened, where neither today's drift nor today's order can see it.
    """
    report = IngestReport()
    lines = _collapse_duplicate_lines(lines, report)
    horizon = now + skew_tolerance

    for line in lines:
        if line.sold_at > horizon:
            report.clock_skew_refused.append(
                f"{line.lightspeed_receipt_id}/{line.lightspeed_line_id}: sold_at "
                f"{line.sold_at.isoformat()} is {_ahead(line.sold_at, now)} ahead of now "
                f"({now.isoformat()}), past the {_pretty(skew_tolerance)} tolerance. NOT "
                "written: a future-dated sale depletes stock on a day that has not happened, "
                "so it is invisible to today's drift and today's order. Check the till clock, "
                "then re-run the window -- sync is idempotent."
            )
            report.skew_refused += 1
            continue
        if line.sold_at > now:
            report.clock_skew_tolerated.append(
                f"{line.lightspeed_receipt_id}/{line.lightspeed_line_id}: sold_at is "
                f"{_ahead(line.sold_at, now)} ahead of now -- inside the "
                f"{_pretty(skew_tolerance)} tolerance, so it was written. The till clock is "
                "drifting; it will start losing sales when it passes the tolerance."
            )

        for name in line.unmatched_modifier_names:
            report.unresolved_modifiers.append(
                f"{line.lightspeed_line_id}: modifier {name!r} has no matching cafeops "
                "modifier by name -- ignored, not applied"
            )

        if line.menu_item_id is None:
            report.unresolved_items.append(
                f"{line.lightspeed_receipt_id}/{line.lightspeed_line_id}: "
                f"{line.raw_item_name!r} [{line.raw_size or '-'}] -- no matching menu_item; "
                "sale line NOT written"
            )
            continue

        existing = session.scalar(
            select(Sale).where(Sale.lightspeed_line_id == line.lightspeed_line_id)
        )
        if existing is None:
            session.add(
                Sale(
                    lightspeed_receipt_id=line.lightspeed_receipt_id,
                    lightspeed_line_id=line.lightspeed_line_id,
                    menu_item_id=line.menu_item_id,
                    qty=line.qty,
                    gross_pence=line.gross_pence,
                    sold_at=line.sold_at,
                    channel=line.channel,
                    applied_modifiers=list(line.applied_modifier_ids),
                    voided=line.voided,
                    is_refund=line.is_refund,
                )
            )
            report.inserted += 1
            continue

        changed = (
            existing.voided != line.voided
            or existing.is_refund != line.is_refund
            or existing.qty != line.qty
            or existing.gross_pence != line.gross_pence
            or set(existing.applied_modifiers or []) != set(line.applied_modifier_ids)
        )
        if not changed:
            report.unchanged += 1
            continue

        if existing.expanded_at is None:
            _apply_correction_fields(existing, line)
            report.corrected_before_expansion += 1
            continue

        _emit_correction_adjustment(session, existing, line, report, now)
        report.corrected_after_expansion += 1

    return report


def _collapse_duplicate_lines(
    lines: Sequence[MappedSaleLine], report: IngestReport
) -> list[MappedSaleLine]:
    """One entry per `lightspeed_line_id` within this call.

    Identical copies are collapsed silently-but-counted: a window whose pages overlap
    re-delivers lines, and `jobs/daily_sync` re-reads three days on purpose.

    Copies that DISAGREE are dropped entirely. Letting them through would send the
    same line twice through the upsert, where the second copy looks exactly like a
    genuine between-runs correction and would emit an `ADJUSTMENT` against a sale
    whose first version was written moments earlier in the same transaction --
    polluting an append-only ledger with an adjustment for a change that never
    happened on the till. The correction path is for a real before and after, across
    runs, not for a payload that contradicts itself.
    """
    first: dict[str, MappedSaleLine] = {}
    conflicts: set[str] = set()
    for line in lines:
        seen = first.get(line.lightspeed_line_id)
        if seen is None:
            first[line.lightspeed_line_id] = line
            continue
        if seen == line:
            report.duplicate_line_ids.append(
                f"{line.lightspeed_receipt_id}/{line.lightspeed_line_id}: identical copy in the "
                "same payload, collapsed"
            )
            continue
        conflicts.add(line.lightspeed_line_id)
    for line_id in sorted(conflicts):
        report.duplicate_line_ids.append(
            f"{line_id}: two copies in ONE payload that DISAGREE -- both dropped rather than "
            "reconciled. Page order is not settlement order, so picking one would be a guess."
        )
    return [line for line_id, line in first.items() if line_id not in conflicts]


def _ahead(later: datetime, earlier: datetime) -> str:
    return _pretty(later - earlier)


def _pretty(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    if abs(total) < 120:
        return f"{total}s"
    if abs(total) < 7200:
        return f"{total // 60}m"
    if abs(total) < 172800:
        return f"{total // 3600}h"
    return f"{total // 86400}d"


def _apply_correction_fields(existing: Sale, line: MappedSaleLine) -> None:
    existing.qty = line.qty
    existing.gross_pence = line.gross_pence
    existing.voided = line.voided
    existing.is_refund = line.is_refund
    existing.applied_modifiers = list(line.applied_modifier_ids)


def _emit_correction_adjustment(
    session: Session,
    existing: Sale,
    line: MappedSaleLine,
    report: IngestReport,
    now: datetime,
) -> None:
    """INVARIANT 9: never mutate the SALE movements this sale already produced.

    Instead: sum what was already depleted for this sale (from the ledger,
    `ref_type='sale'`, `ref_id=sale.id`), resolve what SHOULD be depleted given
    the corrected facts, and write one ADJUSTMENT movement per ingredient for
    the difference. The `sale` row itself is corrected (it is not the ledger).
    """
    composition_repo = SqlCompositionRepository(session)
    ingredient_repo = SqlIngredientRepository(session)
    stock_repo = SqlStockRepository(session)

    snapshots = ingredient_repo.snapshots_by_id()
    waste_factors = {i: s.waste_factor for i, s in snapshots.items()}
    tracked_ids = {i for i, s in snapshots.items() if s.tracking_enabled}

    already_depleted = _existing_sale_movement_totals(session, existing.id)

    new_totals: dict[int, Decimal] = {}
    if not line.voided:
        spec = composition_repo.item_spec(existing.menu_item_id, existing.sold_at)
        if spec is not None:
            modifiers = composition_repo.modifiers(
                list(line.applied_modifier_ids), existing.sold_at
            )
            try:
                recipe = resolve_recipe(spec, modifiers, existing.sold_at, ingredients=snapshots)
            except SubstitutionError as exc:
                report.substitution_errors.append(f"sale {existing.lightspeed_line_id}: {exc}")
                recipe = None
            if recipe is not None:
                new_movements = depletion_movements(
                    sale_id=existing.id,
                    sold_qty=line.qty,
                    sold_at=existing.sold_at,
                    lines=recipe.lines,
                    waste_factors=waste_factors,
                    tracked_ingredient_ids=tracked_ids,
                )
                for movement in new_movements:
                    new_totals[movement.ingredient_id] = (
                        new_totals.get(movement.ingredient_id, Decimal("0")) + movement.qty
                    )

    adjustments: list[MovementSpec] = []
    for ingredient_id in set(already_depleted) | set(new_totals):
        delta = new_totals.get(ingredient_id, Decimal("0")) - already_depleted.get(
            ingredient_id, Decimal("0")
        )
        if delta == 0:
            continue
        adjustments.append(
            MovementSpec(
                ingredient_id=ingredient_id,
                type=MovementType.ADJUSTMENT,
                qty=delta,
                occurred_at=now,
                ref_type="sale_correction",
                ref_id=existing.id,
                note=(
                    f"re-sync correction of Lightspeed line {existing.lightspeed_line_id}: "
                    "original SALE movements left untouched (invariant 9)"
                ),
            )
        )
    if adjustments:
        report.adjustment_movements += stock_repo.append_movements(adjustments)

    _apply_correction_fields(existing, line)


def _existing_sale_movement_totals(session: Session, sale_id: int) -> dict[int, Decimal]:
    rows = session.execute(
        select(StockMovement.ingredient_id, StockMovement.qty).where(
            StockMovement.ref_type == "sale",
            StockMovement.ref_id == sale_id,
            StockMovement.type == MovementType.SALE,
        )
    ).all()
    out: dict[int, Decimal] = {}
    for ingredient_id, qty in rows:
        out[ingredient_id] = out.get(ingredient_id, Decimal("0")) + qty
    return out
