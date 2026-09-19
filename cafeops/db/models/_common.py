from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from cafeops.db.types import Qty, UTCDateTime

ZERO = Decimal("0")


def utcnow() -> datetime:
    return datetime.now(UTC)


def enum_col(py_enum: type, **kw: object) -> SAEnum:
    """Store enums as their member name, with a CHECK constraint. Portable."""
    return SAEnum(py_enum, native_enum=False, validate_strings=True, **kw)  # type: ignore[arg-type]


class TimestampedMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


__all__ = ["ZERO", "Qty", "TimestampedMixin", "UTCDateTime", "enum_col", "utcnow"]
