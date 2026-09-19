"""Ingredients and their price history."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cafeops.db.base import Base
from cafeops.db.models._common import Qty, TimestampedMixin, UTCDateTime, enum_col
from cafeops.db.models.enums import PriceSource, Storage, Tier, Unit

if TYPE_CHECKING:
    from cafeops.db.models.batch import StockBatch
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

    # --- shelf life (spec 4.1) ------------------------------------------------
    # NOT in the legacy workbook. Seeded from defaults flagged ESTIMATE, exactly
    # like prices, and surfaced on the data-quality screen until a human confirms
    # them. These cap order size (spec 5.4, invariant 4): milk on a 7-day life must
    # never be ordered against a 9-day cover window however good the forecast is.
    storage: Mapped[Storage] = mapped_column(
        enum_col(Storage), nullable=False, default=Storage.AMBIENT, server_default="AMBIENT"
    )
    #: Unopened life. NULL means "does not expire" -- cups, lids, napkins. NULL is
    #: not "unknown": an unknown shelf life on a perishable is a data-quality flag,
    #: because a missing cap silently permits the waste the cap exists to prevent.
    shelf_life_days: Mapped[int | None] = mapped_column(Integer)
    #: Life after opening, which for oat milk is 5 days against 270 unopened.
    open_life_days: Mapped[int | None] = mapped_column(Integer)
    #: Days subtracted from shelf life to allow for transit and the fact that a
    #: delivery does not arrive fresh off the line.
    transit_buffer_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: How the shelf-life figures got here, so an estimate stays visible.
    shelf_life_source: Mapped[PriceSource | None] = mapped_column(enum_col(PriceSource))

    #: Spec 5.4: perishables must never be used for a min-order top-up (invariant 5).
    @property
    def is_perishable(self) -> bool:
        return self.shelf_life_days is not None

    # Provenance from the legacy workbook, kept so a human can see where a number
    # came from.
    source_note: Mapped[str | None] = mapped_column(String(400))

    prices: Mapped[list[IngredientPrice]] = relationship(
        back_populates="ingredient",
        cascade="all, delete-orphan",
        order_by="IngredientPrice.effective_from",
    )
    par_level: Mapped[ParLevel | None] = relationship(back_populates="ingredient", uselist=False)
    batches: Mapped[list[StockBatch]] = relationship(
        back_populates="ingredient", cascade="all, delete-orphan"
    )

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
    # Spec 4.1: a price belongs to a supplier. The same ingredient genuinely costs
    # different amounts from Booker and Tesco, and collapsing that loses the whole
    # point of multi-sourcing (spec 4.4). Nullable for legacy rows imported before
    # any supplier was known.
    supplier_id: Mapped[int | None] = mapped_column(ForeignKey("supplier.id"))
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
