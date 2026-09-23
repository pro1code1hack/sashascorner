from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cafeops.db.base import Base
from cafeops.db.models._common import Qty, UTCDateTime, enum_col, utcnow
from cafeops.db.models.enums import CapKind, ChecklistStatus, LowConfidenceKind, POStatus

if TYPE_CHECKING:
    from cafeops.db.models.ingredient import Ingredient
    from cafeops.db.models.supplier import Supplier, SupplierProduct


class PurchaseOrder(Base):
    __tablename__ = "purchase_order"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("supplier.id"), nullable=False)
    status: Mapped[POStatus] = mapped_column(
        enum_col(POStatus), nullable=False, default=POStatus.DRAFT
    )
    target_delivery_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    confirmed_by: Mapped[str | None] = mapped_column(String(120))
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    total_pence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Spec 5.4: report that a min-order top-up happened and why. Never silently
    # inflate an order.
    min_order_topped_up: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    #: Why this order exists at this supplier -- spec 4.4/5.5. For a Tesco MANUAL
    #: order this is the panic-buy reason, and it is the input to the report that
    #: argues for fixing the ordering cadence.
    routing_reason: Mapped[str | None] = mapped_column(Text)
    delivery_fee_pence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Invariant 7: low-confidence forecasts say so IN PLACE OF the number. This
    # carries the reason through to the Telegram message.
    confidence_notes: Mapped[str | None] = mapped_column(Text)

    supplier: Mapped[Supplier] = relationship()
    lines: Mapped[list[POLine]] = relationship(back_populates="po", cascade="all, delete-orphan")

    __table_args__ = (
        # INVARIANT 1, in the schema rather than only in a service: CONFIRMED,
        # SENT and RECEIVED are unreachable without a recorded human.
        CheckConstraint(
            "(status NOT IN ('CONFIRMED', 'SENT', 'RECEIVED')) "
            "OR (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)",
            name="ck_po_confirmed_requires_human",
        ),
        CheckConstraint(
            "(status <> 'SENT') OR (sent_at IS NOT NULL)",
            name="ck_po_sent_requires_sent_at",
        ),
        Index("ix_po_supplier_status", "supplier_id", "status"),
        Index("ix_po_target_delivery", "target_delivery_date"),
    )

    def __repr__(self) -> str:
        return f"<PurchaseOrder {self.id} {self.status.value} sup={self.supplier_id}>"


class POLine(Base):
    __tablename__ = "po_line"

    id: Mapped[int] = mapped_column(primary_key=True)
    po_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_order.id", ondelete="CASCADE"), nullable=False
    )
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredient.id"), nullable=False)
    supplier_product_id: Mapped[int] = mapped_column(
        ForeignKey("supplier_product.id"), nullable=False
    )
    suggested_packs: Mapped[int] = mapped_column(Integer, nullable=False)
    # What the human settled on after inline +/- adjustment in Telegram.
    final_packs: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    received_qty: Mapped[Decimal | None] = mapped_column(Qty())
    #: Entered at delivery receipt. Creates the stock_batch's expires_at, which is
    #: what makes FIFO and the expiry sweep possible at all.
    received_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # Why this line is here: the computed need, and whether it only appeared to
    # satisfy the supplier minimum.
    need_qty: Mapped[Decimal | None] = mapped_column(Qty())
    is_top_up: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Set when effective_cover < cover_days (spec 5.4). The user must know the
    #: system chose to under-order deliberately, or they will override it and create
    #: exactly the waste the cap was preventing.
    #:
    #: This is an English SENTENCE, for a human reading a log. Code must branch on
    #: `cap_kind` instead: the Telegram bot previously recovered the kind by regex over
    #: this column, so a reword in `domain/ordering.py` silently broke the Russian
    #: explanation -- and an unexplained cap is the one thing invariant 4 cannot
    #: survive, because the owner then raises the quantity.
    cap_reason: Mapped[str | None] = mapped_column(String(80))
    cap_kind: Mapped[CapKind | None] = mapped_column(enum_col(CapKind))

    #: Invariant 9 per line. `purchase_order.confidence_notes` holds one prose blob for
    #: the whole order, which cannot say WHICH line's number is untrustworthy.
    low_confidence: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    low_confidence_kind: Mapped[LowConfidenceKind | None] = mapped_column(
        enum_col(LowConfidenceKind)
    )
    confidence_reason: Mapped[str | None] = mapped_column(Text)

    po: Mapped[PurchaseOrder] = relationship(back_populates="lines")
    ingredient: Mapped[Ingredient] = relationship()
    supplier_product: Mapped[SupplierProduct] = relationship()

    __table_args__ = (Index("ix_po_line_po", "po_id"),)


class ChecklistResponse(Base):
    """Tier C: yes/no, no numbers."""

    __tablename__ = "checklist_response"

    id: Mapped[int] = mapped_column(primary_key=True)
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredient.id"), nullable=False)
    status: Mapped[ChecklistStatus] = mapped_column(enum_col(ChecklistStatus), nullable=False)
    responded_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    responded_by: Mapped[str] = mapped_column(String(120), nullable=False)

    ingredient: Mapped[Ingredient] = relationship()

    __table_args__ = (Index("ix_checklist_ing_at", "ingredient_id", "responded_at"),)
