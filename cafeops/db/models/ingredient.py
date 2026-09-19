"""Ingredients and their price history."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cafeops.db.base import Base
from cafeops.db.models._common import Qty, TimestampedMixin, UTCDateTime, enum_col
from cafeops.db.models.enums import PriceSource, Tier, Unit

if TYPE_CHECKING:
    from cafeops.db.models.par import ParLevel


class Ingredient(Base, TimestampedMixin):
    __tablename__ = "ingredient"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    unit: Mapped[Unit] = mapped_column(enum_col(Unit), nullable=False)
    category: Mapped[str | None] = mapped_column(String(80))
    tier: Mapped[Tier] = mapped_column(enum_col(Tier), nullable=False, default=Tier.C)
    tracking_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Absorbs real-world loss: foaming, pitcher residue, purge. Tuned from
    # observed drift (spec 5.1), never guessed once and forgotten.
    # INVARIANT 5: this affects stock depletion ONLY, never menu cost.
    waste_factor: Mapped[Decimal] = mapped_column(Qty(), nullable=False, default=Decimal("0"))

    # Denormalised cache of the latest ingredient_price row. Never written by
    # hand -- services/edit_composition.py and the price importer maintain it.
    current_cost_pence_per_unit: Mapped[Decimal | None] = mapped_column(Qty())
    # Carried alongside the cache so invariant 6 survives: an aggregate built on
    # this column can still tell an invoice from a guess.
    current_cost_source: Mapped[PriceSource | None] = mapped_column(enum_col(PriceSource))

    # Provenance from the legacy workbook, kept so a human can see where a number
    # came from.
    source_note: Mapped[str | None] = mapped_column(String(400))

    prices: Mapped[list[IngredientPrice]] = relationship(
        back_populates="ingredient",
        cascade="all, delete-orphan",
        order_by="IngredientPrice.effective_from",
    )
    par_level: Mapped[ParLevel | None] = relationship(back_populates="ingredient", uselist=False)

    def __repr__(self) -> str:
        return f"<Ingredient {self.id} {self.name!r} {self.tier.value}/{self.unit.value}>"


class IngredientPrice(Base):
    """Effective-dated cost history. Spec 4.1.

    Append-only in practice: a price change opens a new row rather than editing
    the old one, so a cost computed for March stays computable in June.
    """

    __tablename__ = "ingredient_price"

    id: Mapped[int] = mapped_column(primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredient.id", ondelete="CASCADE"), nullable=False
    )
    pack_size: Mapped[Decimal] = mapped_column(Qty(), nullable=False)
    pack_unit: Mapped[Unit] = mapped_column(enum_col(Unit), nullable=False)
    pack_cost_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    # Derived from pack_cost/pack_size, stored so historical costs are exact
    # rather than re-divided with today's rounding.
    cost_per_unit_pence: Mapped[Decimal] = mapped_column(Qty(), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(UTCDateTime)
    source: Mapped[PriceSource] = mapped_column(enum_col(PriceSource), nullable=False)
    note: Mapped[str | None] = mapped_column(String(400))

    ingredient: Mapped[Ingredient] = relationship(back_populates="prices")

    __table_args__ = (Index("ix_ingredient_price_ing_from", "ingredient_id", "effective_from"),)

    def __repr__(self) -> str:
        return (
            f"<IngredientPrice ing={self.ingredient_id} "
            f"{self.cost_per_unit_pence}p/unit {self.source.value}>"
        )
