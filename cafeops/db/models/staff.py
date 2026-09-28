"""Staff users, the devices the scanner runs on, and their 12-hour sessions.

docs/loyalty/CONTRACT.md §2. This is NOT a second back-office login: the dashboard is
still one shared password (CLAUDE.md §1). These rows exist for the till -- the scanner at
`/staff`, manager-PIN approvals (manual adjust, cooldown override) and the bot's
`telegram_id` -- because the spec wants every stamp to say which person on which device
gave it, and a shared password cannot say that.

Two keys, deliberately separate:

- **the device token** proves "this is a tablet the café registered". It is issued once,
  against a 6-digit pairing code the back office shows for 15 minutes, and lives in the
  scanner's storage until revoked.
- **the session token** proves "this person typed their PIN on it, within 12 hours".

Both are stored as sha256 only. A PIN is 4-6 digits, hashed with the same scrypt format
as `auth_credential`, and identifies its owner: PINs are unique among active users, so the
till never asks "who are you" as well as "what is your PIN".
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cafeops.db.base import Base
from cafeops.db.models._common import UTCDateTime, enum_col, utcnow


class StaffRole(enum.Enum):
    """SPEC: "One user table: roles staff / manager / owner"."""

    STAFF = "STAFF"
    #: May approve a cooldown override, undo anyone's stamp, and make later corrections.
    MANAGER = "MANAGER"
    OWNER = "OWNER"


class StaffUser(Base):
    __tablename__ = "staff_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    role: Mapped[StaffRole] = mapped_column(enum_col(StaffRole), nullable=False)
    #: `scrypt$n$r$p$salt$hash`, the `services/auth.py` format.
    pin_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    @property
    def is_manager(self) -> bool:
        return self.role in (StaffRole.MANAGER, StaffRole.OWNER)


class StaffDevice(Base):
    """A till tablet or phone. Unpaired while `token_hash` is NULL.

    `token_hash` is nullable (the contract lists it without NOT NULL) because a device is
    created with only a pairing code; the token exists from the moment it is paired.
    """

    __tablename__ = "staff_device"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    pairing_code_hash: Mapped[str | None] = mapped_column(String(64))
    pairing_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    registered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    __table_args__ = (Index("ix_staff_device_pairing", "pairing_code_hash"),)


class StaffSession(Base):
    """A PIN login on a registered device. 12 hours, absolute; no idle extension."""

    __tablename__ = "staff_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    staff_user_id: Mapped[int] = mapped_column(ForeignKey("staff_user.id"), nullable=False)
    device_id: Mapped[int] = mapped_column(ForeignKey("staff_device.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    user: Mapped[StaffUser] = relationship()
    device: Mapped[StaffDevice] = relationship()

    __table_args__ = (Index("ix_staff_session_device", "device_id", "revoked_at"),)


__all__ = ["StaffDevice", "StaffRole", "StaffSession", "StaffUser"]
