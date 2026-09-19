"""Portable column types.

Two problems this file solves, both of which would otherwise leak SQLite
specifics into application code:

1. SQLite has no native NUMERIC. SQLAlchemy's sqlite dialect round-trips Numeric
   through float, which silently destroys exactness -- unacceptable for stock
   quantities. `Qty` therefore stores a **scaled integer** on SQLite (the value
   times 10**QTY_SCALE) and a real NUMERIC everywhere else.
2. SQLite has no timezone-aware timestamp. `UTCDateTime` enforces the spec rule
   that every stored timestamp is tz-aware UTC, on the way in and on the way out.

### Why scaled integers and not TEXT

The first implementation stored the decimal as TEXT, which is exact but breaks
every comparison. SQLite type ordering places TEXT above all numbers, so

    SELECT '0.0000' > 0;   ->   1

`WHERE qty_remaining > 0` therefore matched **every** depleted batch, and the demo
reported milk 54 days overdue while listing batches with nothing in them. Python-side
comparisons on loaded Decimals were fine, which is exactly what makes the bug
dangerous: it only appears in SQL, only on some values, and never raises.

A scaled integer is exact *and* orders correctly, so `WHERE qty > 0` means what it
says. The cost is that Postgres and SQLite hold different representations, so moving
between them is a converting Alembic branch rather than a dump-and-load. That is the
right trade: a migration is a one-off with a human watching, whereas a silently wrong
comparison is forever.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import BigInteger, DateTime, Dialect, Numeric
from sqlalchemy.types import TypeDecorator

# Stock quantities: 18 digits, 6 decimal places. Enough for 0.000001 KG and for
# a pallet of cups, with room to spare.
QTY_PRECISION = 18
QTY_SCALE = 6


#: The integer factor a value is multiplied by for SQLite storage.
_SCALE_FACTOR = Decimal(10) ** QTY_SCALE


class Qty(TypeDecorator[Decimal]):
    """An exact Decimal quantity that survives SQLite AND compares correctly."""

    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int = QTY_PRECISION, scale: int = QTY_SCALE) -> None:
        super().__init__(precision=precision, scale=scale, asdecimal=True)
        self._scale = scale
        self._factor = Decimal(10) ** scale

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "sqlite":
            # BigInteger, not Integer: 18 digits of precision at scale 6 needs more
            # than 32 bits, and SQLite's INTEGER is 64-bit anyway.
            return dialect.type_descriptor(BigInteger())
        return dialect.type_descriptor(Numeric(self.precision, self.scale, asdecimal=True))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, Decimal):
            # Accept int and str. Never float -- a float here is a bug upstream,
            # and quietly accepting it is how exactness dies.
            if isinstance(value, float):
                raise TypeError(
                    "float assigned to a Qty column; use Decimal(str(x)) at the boundary"
                )
            value = Decimal(str(value))
        if dialect.name != "sqlite":
            return value
        # Quantize to the column's declared scale. This is a rounding, not a loss of
        # exactness: the scale IS the contract (spec 4: "quantities as Decimal with
        # explicit scale"), and a value carrying more precision than the column
        # declares was never going to be stored faithfully by any backend.
        scaled = (value * self._factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return int(scaled)

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        if isinstance(value, int):
            return Decimal(value) / self._factor
        return Decimal(str(value))


class UTCDateTime(TypeDecorator[datetime]):
    """A timestamp that is always tz-aware UTC in Python."""

    impl = DateTime
    cache_ok = True

    def __init__(self, timezone: bool = True) -> None:
        # `timezone` is accepted because Alembic autogeneration renders the
        # impl's kwargs back into migration files. It is always True here.
        super().__init__(timezone=True)

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"expected datetime, got {type(value)!r}")
        if value.tzinfo is None:
            raise ValueError(
                "naive datetime assigned to a UTCDateTime column; "
                "all timestamps must be tz-aware UTC"
            )
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
