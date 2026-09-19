from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from cafeops.db.base import Base
from cafeops.db.models._common import Qty, UTCDateTime, enum_col
from cafeops.db.models.enums import OrderChannel, Unit

if TYPE_CHECKING:
    from cafeops.db.models.ingredient import Ingredient


class Supplier(Base):
    __tablename__ = "supplier"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # ISO weekdays, Mon=1..Sun=7. EMPTY LIST means "any day" -- walk-in retail.
    delivery_weekdays: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    min_order_pence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    order_channel: Mapped[OrderChannel] = mapped_column(
        enum_col(OrderChannel), nullable=False, default=OrderChannel.MANUAL
    )
    contact: Mapped[str | None] = mapped_column(String(400))

    # --- spec 4.4 ordering terms ---------------------------------------------
    #: Local time by which an order must be placed to make the next delivery. A
    #: cutoff missed by ten minutes costs a whole delivery cycle, which is exactly
    #: the situation that ends in a Tesco run.
    cutoff_time: Mapped[time | None] = mapped_column(Time)
    delivery_fee_pence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: Order above this and the delivery fee is waived. Distinct from
    #: `min_order_pence`: one is a condition on ordering at all, the other is a
    #: price break, and topping up to reach them are different decisions.
    free_delivery_threshold_pence: Mapped[int | None] = mapped_column(Integer)

    #: True when lead time, delivery days, cutoff, minimum or threshold are
    #: INVENTED. Every order built from this supplier must say so -- a cover window
    #: is only as good as these numbers, and a confident quantity derived from a
    #: guess is worse than no quantity.
    terms_are_placeholders: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    # BROWSER_AGENT support: Cups Direct and Tesco have no ordering API. The
    # adapter opens this URL and follows these instructions, stopping at the
    # basket. Invariant 1 holds: a human presses the last button.
    order_url: Mapped[str | None] = mapped_column(String(500))
    agent_instructions: Mapped[str | None] = mapped_column(Text)
    channel_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    products: Mapped[list[SupplierProduct]] = relationship(
        back_populates="supplier", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Supplier {self.id} {self.name!r} {self.order_channel.value}>"


class SupplierProduct(Base):
    __tablename__ = "supplier_product"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        ForeignKey("supplier.id", ondelete="CASCADE"), nullable=False
    )
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredient.id"), nullable=False)
    sku: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    pack_size: Mapped[Decimal] = mapped_column(Qty(), nullable=False)
    pack_unit: Mapped[Unit] = mapped_column(enum_col(Unit), nullable=False)
    price_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    is_preferred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    product_url: Mapped[str | None] = mapped_column(String(500))
    #: Minimum order quantity in packs. Some wholesale lines cannot be bought singly,
    #: and rounding up to it is a real cost the sizing algorithm must see.
    moq_packs: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    #: When this price was last confirmed. A price nobody has checked in a year is a
    #: different kind of fact from one off last week's invoice.
    last_seen_price_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    supplier: Mapped[Supplier] = relationship(back_populates="products")
    ingredient: Mapped[Ingredient] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "supplier_id", "ingredient_id", "sku", name="uq_supplier_product_sup_ing_sku"
        ),
    )

    def __repr__(self) -> str:
        return f"<SupplierProduct {self.id} sku={self.sku!r} pack={self.pack_size}>"
