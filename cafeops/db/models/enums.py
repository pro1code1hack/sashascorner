"""Enums shared by models and re-exported through cafeops.domain.types.

Stored by member name as VARCHAR with a CHECK constraint, so adding a member is
an Alembic migration but never a data rewrite.
"""

from __future__ import annotations

import enum


class Unit(enum.Enum):
    """Stocking units. Spec 4.1 keeps ML and G as first-class.

    The legacy workbook genuinely stocks some things in ml and g -- syrups are
    bought by the litre but counted and costed per ml. Forcing everything into
    L/KG/EACH would mean rewriting every recipe quantity at import and losing the
    unit the owner actually thinks in. Conversion happens in domain/units.py when
    two compatible units meet.
    """

    L = "L"
    KG = "KG"
    ML = "ML"
    G = "G"
    EACH = "EACH"


class Tier(enum.Enum):
    """Spec 4.5. Membership in A is earned via the drift gate, never assigned."""

    A = "A"
    B = "B"
    C = "C"


class ComponentRole(enum.Enum):
    """The slot a component fills in a template. Spec 4.2 -- an enum, not a table.

    Roles are what make substitution possible: a MILK modifier knows which slot
    to replace without naming an ingredient.
    """

    COFFEE = "COFFEE"
    MILK = "MILK"
    BASE = "BASE"
    FLAVOUR = "FLAVOUR"
    TOPPING = "TOPPING"
    PACKAGING = "PACKAGING"
    SUNDRY = "SUNDRY"


class SizeCode(enum.Enum):
    """Sizes in use across the legacy menu. Spec 2."""

    S = "S"
    M = "M"
    XL = "XL"
    ONE = "ONE"


class MovementType(enum.Enum):
    SALE = "SALE"
    DELIVERY = "DELIVERY"
    WASTE = "WASTE"
    ADJUSTMENT = "ADJUSTMENT"
    STAFF = "STAFF"
    COUNT_RESET = "COUNT_RESET"


class PriceSource(enum.Enum):
    """Where a cost came from. Spec 6 pass 1 and invariant 6.

    ESTIMATE must stay visibly flagged through every rollup, aggregate and
    export -- it is why the legacy 46% COGS figure is untrustworthy.
    """

    INVOICE = "INVOICE"
    ESTIMATE = "ESTIMATE"
    SUPPLIER_FEED = "SUPPLIER_FEED"


class ModifierAction(enum.Enum):
    """Spec 4.2. Applied in the order SUBSTITUTE -> SCALE -> ADD."""

    SUBSTITUTE = "SUBSTITUTE"
    ADD = "ADD"
    SCALE = "SCALE"


class OrderChannel(enum.Enum):
    """How an order reaches a supplier.

    BROWSER_AGENT extends the spec's EMAIL|PORTAL|MANUAL. Sasha buys from Tesco
    (walk-in), CakeSmiths (wholesale) and Cups Direct (web shop, no API), so
    "drive a browser to a filled basket and stop" is a real channel here.
    """

    EMAIL = "EMAIL"
    PORTAL = "PORTAL"
    MANUAL = "MANUAL"
    BROWSER_AGENT = "BROWSER_AGENT"


class POStatus(enum.Enum):
    DRAFT = "DRAFT"
    PENDING_CONFIRM = "PENDING_CONFIRM"
    CONFIRMED = "CONFIRMED"
    SENT = "SENT"
    RECEIVED = "RECEIVED"
    CANCELLED = "CANCELLED"


class ChecklistStatus(enum.Enum):
    OK = "OK"
    LOW = "LOW"


class SaleChannel(enum.Enum):
    """Spec 1: also sells through Deliveroo and Just Eat."""

    EPOS = "EPOS"
    DELIVEROO = "DELIVEROO"
    JUST_EAT = "JUST_EAT"
    OTHER = "OTHER"
