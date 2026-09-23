from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cafeops.db.base import Base
from cafeops.db.models._common import Qty, UTCDateTime, enum_col
from cafeops.db.models.enums import RevokeCause

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
    #: WHY the last revocation happened, as a code. `auto_order_reason` is the sentence
    #: somebody reads months later; this is what a surface branches on.
    #:
    #: Persisted because the gate is STATELESS -- `gate_status` re-evaluates from current
    #: history, so once the flag is already off it returns HOLD and the fact that a
    #: revocation happened has vanished. The morning digest is the channel the owner
    #: actually reads, and without this column it could not tell her that auto-ordering
    #: stopped yesterday. Losing auto-ordering silently is the gap `ARCHITECTURE.md` 8A.3
    #: recorded, and the count message alone only reaches whoever was holding the clipboard.
    auto_order_revoke_cause: Mapped[RevokeCause | None] = mapped_column(enum_col(RevokeCause))

    ingredient: Mapped[Ingredient] = relationship(back_populates="par_level")

    def __repr__(self) -> str:
        return f"<ParLevel ing={self.ingredient_id} auto={self.auto_order_enabled}>"
