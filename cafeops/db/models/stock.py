"""Physical counts, the append-only ledger, and drift observations."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cafeops.db.base import Base
from cafeops.db.models._common import Qty, UTCDateTime, enum_col
from cafeops.db.models.enums import MovementType

if TYPE_CHECKING:
    from cafeops.db.models.ingredient import Ingredient


class StockCount(Base):
    """A physical count. The source of truth; everything else is theoretical."""

    __tablename__ = "stock_count"

    id: Mapped[int] = mapped_column(primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredient.id"), nullable=False)
    counted_qty: Mapped[Decimal] = mapped_column(Qty(), nullable=False)
    counted_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    counted_by: Mapped[str] = mapped_column(String(120), nullable=False)
    note: Mapped[str | None] = mapped_column(String(400))

    ingredient: Mapped[Ingredient] = relationship()

    __table_args__ = (Index("ix_stock_count_ing_at", "ingredient_id", "counted_at"),)

    def __repr__(self) -> str:
        return f"<StockCount {self.id} ing={self.ingredient_id} {self.counted_qty}>"


class StockMovement(Base):
    """Append-only ledger. INVARIANT 9: corrections are ADJUSTMENT rows, never
    updates or deletes."""

    __tablename__ = "stock_movement"

    id: Mapped[int] = mapped_column(primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredient.id"), nullable=False)
    # Which batch this movement drew from or added to (spec 4.1). Nullable: an
    # ADJUSTMENT or a COUNT_RESET is about the ingredient as a whole, not one lot,
    # and forcing a batch there would invent provenance.
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("stock_batch.id"))
    type: Mapped[MovementType] = mapped_column(enum_col(MovementType), nullable=False)
    # Signed: consumption negative, delivery positive.
    qty: Mapped[Decimal] = mapped_column(Qty(), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    # Provenance, e.g. ("sale", 1234). Deliberately not a real FK: the ledger
    # must outlive whatever produced a row.
    ref_type: Mapped[str | None] = mapped_column(String(40))
    ref_id: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(String(400))

    ingredient: Mapped[Ingredient] = relationship()

    __table_args__ = (
        Index("ix_stock_movement_ing_at", "ingredient_id", "occurred_at"),
        Index("ix_stock_movement_batch", "batch_id"),
        # The expiry-vs-measurement drift split (spec 5.2) filters by type over a
        # window, so the type needs to be indexed alongside the date.
        Index("ix_stock_movement_ing_type_at", "ingredient_id", "type", "occurred_at"),
        Index("ix_stock_movement_ref", "ref_type", "ref_id"),
    )

    def __repr__(self) -> str:
        return f"<StockMovement {self.id} {self.type.value} ing={self.ingredient_id} {self.qty}>"


class DriftObservation(Base):
    """One drift measurement, taken when a physical count lands.

    Spec 5.2 says to store drift without naming a table. Stored as an append-only
    observation rather than recomputed on demand: the gate reads drift HISTORY, and
    a recomputed value would silently change when waste_factor is retuned, so a
    decision made last week would stop meaning what it meant.
    """

    __tablename__ = "drift_observation"

    id: Mapped[int] = mapped_column(primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredient.id"), nullable=False)
    stock_count_id: Mapped[int] = mapped_column(ForeignKey("stock_count.id"), nullable=False)
    theoretical_qty: Mapped[Decimal] = mapped_column(Qty(), nullable=False)
    counted_qty: Mapped[Decimal] = mapped_column(Qty(), nullable=False)
    # A ratio, not money -- float is fine and keeps rolling stats cheap.
    drift_pct: Mapped[float] = mapped_column(Float, nullable=False)
    # The waste_factor in force at measurement time, so a later retune is auditable.
    waste_factor_at_count: Mapped[Decimal] = mapped_column(Qty(), nullable=False)
    # Spec 5.2's second diagnostic: how much of the gap is expiry write-off rather
    # than measurement error. The two have OPPOSITE fixes -- one is a recipe
    # correction, the other is ordering less -- so reporting a single
    # undifferentiated number tells the owner to do the wrong thing half the time.
    expired_qty_in_window: Mapped[Decimal | None] = mapped_column(Qty())
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    ingredient: Mapped[Ingredient] = relationship()
    stock_count: Mapped[StockCount] = relationship()

    __table_args__ = (Index("ix_drift_ing_at", "ingredient_id", "observed_at"),)

    def __repr__(self) -> str:
        return f"<DriftObservation ing={self.ingredient_id} {self.drift_pct:.1f}%>"
