"""Ingredient and price queries. No business logic."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from cafeops.db.models import Ingredient, IngredientPrice
from cafeops.domain.types import IngredientSnapshot, PriceSource, Tier, Unit
from cafeops.domain.units import convert


def _snapshot(row: Ingredient) -> IngredientSnapshot:
    return IngredientSnapshot(
        id=row.id,
        name=row.name,
        unit=row.unit,
        tier=row.tier,
        tracking_enabled=row.tracking_enabled,
        waste_factor=row.waste_factor,
        cost_per_unit_pence=row.current_cost_pence_per_unit,
        cost_source=row.current_cost_source,
    )


class SqlIngredientRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, ingredient_id: int) -> IngredientSnapshot | None:
        row = self.session.get(Ingredient, ingredient_id)
        return None if row is None else _snapshot(row)

    def get_by_name(self, name: str) -> IngredientSnapshot | None:
        row = self.session.scalar(select(Ingredient).where(Ingredient.name == name))
        return None if row is None else _snapshot(row)

    def list_all(self) -> list[IngredientSnapshot]:
        stmt = select(Ingredient).order_by(Ingredient.tier, Ingredient.name)
        return [_snapshot(r) for r in self.session.scalars(stmt)]

    def list_tracked(self, *, tiers: Sequence[Tier] | None = None) -> list[IngredientSnapshot]:
        stmt = select(Ingredient).where(Ingredient.tracking_enabled.is_(True))
        if tiers:
            stmt = stmt.where(Ingredient.tier.in_(list(tiers)))
        return [
            _snapshot(r)
            for r in self.session.scalars(stmt.order_by(Ingredient.tier, Ingredient.name))
        ]

    def snapshots_by_id(self) -> dict[int, IngredientSnapshot]:
        """Every ingredient, keyed by id. Resolution needs waste factors and costs."""
        return {s.id: s for s in self.list_all()}

    def set_waste_factor(self, ingredient_id: int, waste_factor: Decimal) -> None:
        row = self.session.get(Ingredient, ingredient_id)
        if row is None:
            raise LookupError(f"ingredient {ingredient_id} not found")
        row.waste_factor = waste_factor

    def cost_per_unit_at(
        self, ingredient_id: int, at: datetime
    ) -> tuple[Decimal, PriceSource] | None:
        """Effective-dated cost. None when unpriced at that date -- never zero.

        Invariant 6 depends on the difference between "costs nothing" and "we do
        not know what it costs".
        """
        row = self.session.execute(
            select(IngredientPrice.cost_per_unit_pence, IngredientPrice.source)
            .where(
                IngredientPrice.ingredient_id == ingredient_id,
                IngredientPrice.effective_from <= at,
                or_(
                    IngredientPrice.effective_to.is_(None),
                    IngredientPrice.effective_to > at,
                ),
            )
            .order_by(IngredientPrice.effective_from.desc(), IngredientPrice.id.desc())
            .limit(1)
        ).first()
        if row is None:
            return None
        return row[0], row[1]

    def add_price(
        self,
        ingredient_id: int,
        *,
        pack_size: Decimal,
        pack_unit: Unit,
        pack_cost_pence: int,
        effective_from: datetime,
        source: PriceSource,
        note: str | None = None,
    ) -> int:
        """Open a new price row, close the previous one, refresh the cache."""
        ingredient = self.session.get(Ingredient, ingredient_id)
        if ingredient is None:
            raise LookupError(f"ingredient {ingredient_id} not found")

        current = self.session.scalar(
            select(IngredientPrice)
            .where(
                IngredientPrice.ingredient_id == ingredient_id,
                IngredientPrice.effective_to.is_(None),
            )
            .order_by(IngredientPrice.effective_from.desc())
            .limit(1)
        )
        if current is not None:
            current.effective_to = effective_from

        if pack_size <= 0:
            # Not a price: a zero cost would read as "free" in every rollup (invariant 8).
            raise ValueError(f"pack size must be positive, got {pack_size}")
        # Per INGREDIENT unit, not per pack unit: a 1 kg bag of an ingredient measured in
        # grams costs pack/1000 per g. Across dimensions this raises rather than 1:1.
        pack_in_ingredient_units = convert(pack_size, pack_unit, ingredient.unit)
        cost_per_unit = Decimal(pack_cost_pence) / pack_in_ingredient_units
        row = IngredientPrice(
            ingredient_id=ingredient_id,
            pack_size=pack_size,
            pack_unit=pack_unit,
            pack_cost_pence=pack_cost_pence,
            cost_per_unit_pence=cost_per_unit,
            effective_from=effective_from,
            source=source,
            note=note,
        )
        self.session.add(row)
        self.session.flush()
        ingredient.current_cost_pence_per_unit = cost_per_unit
        ingredient.current_cost_source = source
        return row.id
