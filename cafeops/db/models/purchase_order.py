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
    #: Who marked it sent (web "Mark sent", DECISIONS.md 1/6). The bot's dispatch path
    #: may leave it NULL, so no CHECK.
    sent_by: Mapped[str | None] = mapped_column(String(120))
    #: Cancellation (spec 4.2). Allowed from DRAFT/PENDING_CONFIRM/CONFIRMED, refused
    #: from SENT/RECEIVED (service rule). CHECK: CANCELLED needs a name and a time.
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    cancelled_by: Mapped[str | None] = mapped_column(String(120))
    cancel_reason: Mapped[str | None] = mapped_column(String(400))
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
    #: `OrderNoteKind` member names, comma-separated and deduplicated, in the order the
    #: decisions were taken. `notes` above is the English prose those decisions wrote.
    #:
    #: Without this the Russian bot had to DROP the notes entirely -- it cannot render
    #: English, and translating prose at the formatter is guesswork -- so the owner read
    #: a quantity with no account of the par ceiling that cut it or the perishables
    #: invariant 5 kept out of the top-up. Spec 5.4 forbids a silent adjustment, and a
    #: sentence she cannot read is silent. A comma-separated list rather than JSON because
    #: it is a set of enum names, it is greppable in a database browser, and nothing about
    #: it is nested.
    note_codes: Mapped[str | None] = mapped_column(String(600))
    #: Photo of the delivery note / till receipt (owner, 2026-09-26). The order is an
    #: expense, and the receipt is its evidence. `SET NULL` for the same reason as
    #: `menu_item.photo_asset_id`.
    receipt_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_asset.id", ondelete="SET NULL")
    )
    receipt_uploaded_by: Mapped[str | None] = mapped_column(String(120))

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
        CheckConstraint(
            "(status <> 'CANCELLED') OR (cancelled_by IS NOT NULL AND cancelled_at IS NOT NULL)",
            name="cancelled_requires_human",
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
    #: `SuggestedLine.cover_days` -- the EFFECTIVE window this line was sized on, after
    #: the shelf-life and season caps. Persisted because "capped at 4 days" is the whole
    #: of the invariant 4 message and the number was previously recovered by regex out of
    #: `cap_reason`; a reword left the Russian sentence with no figure in it.
    cover_days: Mapped[int | None] = mapped_column(Integer)
    #: How many days of history stood behind the forecast, and how many it wanted.
    #: Invariant 9 replaces the quantity with a reason, and "6 days of history, 14 needed"
    #: is a reason; "history is thin" is a shrug. Stored rather than read back from config
    #: at render time: the requirement is what it was when the order was sized, and a
    #: later change to `min_history_days` must not rewrite the explanation.
    forecast_history_days: Mapped[int | None] = mapped_column(Integer)
    forecast_needed_days: Mapped[int | None] = mapped_column(Integer)
    #: Non-NULL when a tier C checklist answer put this line here, naming the person who
    #: chose the quantity. Tier C is never calculated (spec 4.7) -- so this line's number
    #: is somebody's decision, and it must never be shown as though it were a forecast.
    checklist_requested_by: Mapped[str | None] = mapped_column(String(120))

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
