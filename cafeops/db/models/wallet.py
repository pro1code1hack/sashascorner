"""Where a card's wallet passes live (docs/loyalty/CONTRACT.md §2 "B").

Neither table is the card: `loyalty_card` is the single source of truth and a pass is
only a rendering of it. These record *who to tell* when it changes.

`wallet_apple_registration` is written by Apple, through the PassKit web service
(`integrations/wallet/apple_webservice.py`), when a device adds the pass; it is removed
when the device deletes it or APNs answers 410 for the token.

`wallet_google_object` is written when a PATCH to Google succeeds, i.e. once the customer
has actually saved the pass (before that, Google answers 404 and there is nothing to
record). Its `last_error` is what `cafeops wallet doctor` and the admin list show.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from cafeops.db.base import Base
from cafeops.db.models._common import UTCDateTime, utcnow


class WalletAppleRegistration(Base):
    __tablename__ = "wallet_apple_registration"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Apple's per-device id; one device may hold many passes and vice versa.
    device_library_id: Mapped[str] = mapped_column(String(128), nullable=False)
    #: APNs token for that device. Apple may send a new one on re-registration.
    push_token: Mapped[str] = mapped_column(String(200), nullable=False)
    card_id: Mapped[str] = mapped_column(ForeignKey("loyalty_card.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("device_library_id", "card_id", name="uq_wallet_apple_registration"),
        Index("ix_wallet_apple_registration_card", "card_id"),
    )


class WalletGoogleObject(Base):
    __tablename__ = "wallet_google_object"

    card_id: Mapped[str] = mapped_column(ForeignKey("loyalty_card.id"), primary_key=True)
    #: "<issuer>.<card_id>"
    object_id: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    class_id: Mapped[str] = mapped_column(String(120), nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(String(400))


__all__ = ["WalletAppleRegistration", "WalletGoogleObject"]
