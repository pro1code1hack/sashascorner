from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from cafeops.db.base import Base
from cafeops.db.models._common import Qty, UTCDateTime, enum_col
from cafeops.db.models.enums import SaleChannel, SaleSource

if TYPE_CHECKING:
    from cafeops.db.models.menu import MenuItem


class Sale(Base):
    """One line of one receipt. Ingestion is idempotent on lightspeed_line_id.

    Not every row came from Lightspeed any more (DECISIONS 28): a cash sale typed into
    the bot, a Deliveroo order entered by hand, a loyalty redemption. Those keep the
    same two id columns -- `manual:<uuid>` / `manual:<uuid>:<n>` -- so one receipt still
    groups its lines, and say where they came from in `source` and `recorded_by`.
    """

    __tablename__ = "sale"

    id: Mapped[int] = mapped_column(primary_key=True)
    lightspeed_receipt_id: Mapped[str] = mapped_column(String(80), nullable=False)
    lightspeed_line_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    menu_item_id: Mapped[int] = mapped_column(ForeignKey("menu_item.id"), nullable=False)
    # Signed: a refund line carries a negative qty so the ledger nets out.
    qty: Mapped[Decimal] = mapped_column(Qty(), nullable=False)
    gross_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    sold_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    channel: Mapped[SaleChannel] = mapped_column(
        enum_col(SaleChannel), nullable=False, default=SaleChannel.EPOS
    )
    # JSON list of modifier ids (spec 4.4). Resolution reads these to rewrite the
    # recipe: an oat latte consumes oat milk and no whole milk.
    applied_modifiers: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)

    # A voided receipt must not deplete stock. Kept rather than deleted so a
    # re-sync of the same window is a no-op instead of a resurrection.
    voided: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_refund: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Set once expansion has written this line's SALE movements, so the nightly
    # job picks up only what it has not already expanded.
    expanded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # Where the row came from (DECISIONS 28). `POS_API` is the Lightspeed sync and
    # the only writer of `EPOS`; the rest were typed or uploaded by a person and say
    # who in `recorded_by`. A read that mixes the two without labelling them is a bug.
    source: Mapped[SaleSource] = mapped_column(
        enum_col(SaleSource),
        nullable=False,
        default=SaleSource.POS_API,
        server_default=SaleSource.POS_API.value,
    )
    recorded_by: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(String(400))

    menu_item: Mapped[MenuItem] = relationship()

    __table_args__ = (
        Index("ix_sale_sold_at", "sold_at"),
        Index("ix_sale_receipt", "lightspeed_receipt_id"),
        Index("ix_sale_expanded_at", "expanded_at"),
        Index("ix_sale_source", "source"),
    )

    def __repr__(self) -> str:
        return f"<Sale {self.id} line={self.lightspeed_line_id} qty={self.qty}>"
