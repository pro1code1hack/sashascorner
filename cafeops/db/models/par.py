from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cafeops.db.base import Base
from cafeops.db.models._common import Qty, UTCDateTime

if TYPE_CHECKING:
    from cafeops.db.models.ingredient import Ingredient


class ParLevel(Base):
    __tablename__ = "par_level"

    id: Mapped[int] = mapped_column(primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(
        ForeignKey("ingredient.id"), unique=True, nullable=False
    )
    safety_days: Mapped[Decimal] = mapped_column(Qty(), nullable=False, default=Decimal("1"))
    min_qty: Mapped[Decimal] = mapped_column(Qty(), nullable=False, default=Decimal("0"))
    max_qty: Mapped[Decimal] = mapped_column(Qty(), nullable=False)

    # INVARIANT 2: earned through drift history, never set manually as a
    # shortcut. Only domain/tiers.py may flip this. The audit fields exist so
    # "who turned this on, and on what evidence" always has an answer.
    auto_order_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_order_granted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    auto_order_revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    auto_order_reason: Mapped[str | None] = mapped_column(String(400))

    ingredient: Mapped[Ingredient] = relationship(back_populates="par_level")

    def __repr__(self) -> str:
        return f"<ParLevel ing={self.ingredient_id} auto={self.auto_order_enabled}>"
