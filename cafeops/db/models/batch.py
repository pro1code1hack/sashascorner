"""Stock batches: FIFO depletion and expiry. Spec 4.1.

Shelf life is not decoration. It **caps order size** (spec 5.4, invariant 4) and it
is the thing that turns a clever ordering system into a waste generator when it is
missing. Milk with a 7-day shelf life must never be ordered on a 9-day cover window,
however good the forecast is.

A batch reaching `expires_at` with `qty_remaining > 0` produces an `EXPIRED` movement
and an alert. That number is the honest waste figure the P&L needs and nobody
currently has -- and it is what lets a drift report distinguish "the recipe is wrong"
from "we are over-ordering", which have opposite fixes.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cafeops.db.base import Base
from cafeops.db.models._common import Qty, UTCDateTime, enum_col
from cafeops.domain.enums import ExpirySource

if TYPE_CHECKING:
    from cafeops.db.models.ingredient import Ingredient


class StockBatch(Base):
    """One received lot of one ingredient, with its own expiry and unit cost.

    Depletion is FIFO by `expires_at` -- not by `received_at`. A delivery with a
    short date must go out before older stock with a longer one, which is what a
    person standing at the fridge actually does.
    """

    __tablename__ = "stock_batch"

    id: Mapped[int] = mapped_column(primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredient.id"), nullable=False)
    # Nullable: an opening count or an adjustment creates stock with no purchase
    # order behind it, and refusing to model that would push those quantities
    # outside the batch system where they could never expire.
    po_line_id: Mapped[int | None] = mapped_column(ForeignKey("po_line.id"))

    qty_received: Mapped[Decimal] = mapped_column(Qty(), nullable=False)
    qty_remaining: Mapped[Decimal] = mapped_column(Qty(), nullable=False)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    # Nullable: cups and napkins do not expire, and inventing a date for them
    # would put them in the expiry sweep's way forever.
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Set when the pack is opened; `open_life_days` then shortens the effective
    # expiry. A 270-day carton of oat milk lasts 5 days once opened.
    opened_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    unit_cost_pence: Mapped[Decimal] = mapped_column(Qty(), nullable=False)

    # Set when the expiry sweep has written this batch off, so the sweep is
    # idempotent and a late run cannot write the same loss twice.
    expired_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    note: Mapped[str | None] = mapped_column(String(400))
    #: ENTERED (read off the carton) or ASSUMED (received_at + shelf life). Replaces
    #: parsing the `EXPIRY ASSUMED` note prefix; the migration backfills ASSUMED from
    #: it. NULL = not recorded (no expiry, or a row predating the column).
    expiry_source: Mapped[ExpirySource | None] = mapped_column(enum_col(ExpirySource))
    #: Who signed for it (DECISIONS.md 6). Previously only inside `note` prose.
    received_by: Mapped[str | None] = mapped_column(String(120))

    ingredient: Mapped[Ingredient] = relationship(back_populates="batches")

    __table_args__ = (
        # The FIFO index: "oldest-expiring batch with stock left", the one query
        # depletion runs for every ingredient of every sold line.
        Index("ix_stock_batch_ing_expires", "ingredient_id", "expires_at"),
        Index("ix_stock_batch_open", "ingredient_id", "expired_at"),
        Index("ix_stock_batch_po_line", "po_line_id"),
    )

    @property
    def is_depleted(self) -> bool:
        return self.qty_remaining <= 0

    def __repr__(self) -> str:
        return (
            f"<StockBatch {self.id} ing={self.ingredient_id} "
            f"{self.qty_remaining}/{self.qty_received} expires={self.expires_at}>"
        )


class Season(Base):
    """A trading season. Spec 4.3.

    Two consequences, both mandatory:

    1. **Forecasting excludes out-of-season history.** Pumpkin syrup consumption in
       October must not inflate the July baseline, and must not be read as "we have
       not used this in 9 months, drop it".
    2. **Ordering respects the season window.** Do not order a seasonal syrup with
       3 weeks left on a 6-week cover. Cap at remaining season days and warn when a
       season ends with stock on hand.
    """

    __tablename__ = "season"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    # Calendar dates, not instants: a season is a human period, and timestamps would
    # invite timezone bugs at both ends for no benefit. When `is_recurring_annually`
    # is set, only month and day are meaningful and the year is the first
    # occurrence -- the seasonal forecast path projects it forward.
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    is_recurring_annually: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[str | None] = mapped_column(String(400))

    def __repr__(self) -> str:
        return f"<Season {self.id} {self.name!r}>"
