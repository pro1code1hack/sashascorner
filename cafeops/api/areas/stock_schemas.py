"""Request and response models for the Stock, Orders and Suppliers writes.

docs/design/specs/stock-orders-suppliers.md section 6, reconciled with DECISIONS.md: the
web never creates or confirms a purchase order, so there is no `SendOrderIn`.

Conventions are `api/encoding.py`'s: quantities are decimal STRINGS, money is integer
pence where it is stored as an integer and an exact decimal string where it is derived,
a missing cost is null. Every request body forbids unknown fields (`In`), and every write
that a person makes carries that person's name (DECISIONS 6) -- the service refuses a
blank one, so the name field is required here rather than defaulted.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field

from cafeops.api.schemas import (
    Cost,
    DriftAttributionOut,
    In,
    OnHand,
    Out,
    PersistedOrderOut,
    SupplierOut,
)

#: A person's name, as typed on this device ("who's using this").
Name = Annotated[str, Field(min_length=1, max_length=120)]
Qty = str
UnitName = Literal["L", "KG", "ML", "G", "EACH"]


# ==========================================================================
# Stock
# ==========================================================================


class CountIn(In):
    counted_qty: Qty = Field(description="Decimal string in the ingredient's unit.")
    counted_by: Name
    counted_at: datetime | None = Field(default=None, description="Defaults to now.")
    note: str | None = Field(default=None, max_length=400)


class CountOut(Out):
    ingredient_id: int
    ingredient_name: str
    unit: str
    stock_count_id: int
    counted_qty: Qty
    counted_at: datetime
    counted_by: str
    theoretical_before: OnHand = Field(
        description="The 'expected' figure: theoretical on-hand before this count re-anchored it."
    )
    drift_pct: float | None = Field(description="Null on a first count: nothing to compare.")
    verdict: str | None
    trust_label: str
    auto_order_enabled: bool
    gate_reason: str
    alert_level: str
    attribution: DriftAttributionOut | None
    reconciliation_note: str | None
    notes: tuple[str, ...]


class DeliveryIn(In):
    qty: Qty
    received_by: Name
    expires_on: date | None = Field(
        default=None,
        description="The use-by date on the carton. Omit and it is assumed from shelf life.",
    )
    unit_cost_pence: str | None = Field(
        default=None, description="Exact pence per stocking unit, if known."
    )
    note: str | None = Field(default=None, max_length=300)


class DeliveryOut(Out):
    batch_id: int
    ingredient_id: int
    ingredient_name: str
    qty: Qty
    unit: str
    received_at: datetime
    expires_at: datetime | None
    expiry_assumed: bool
    warnings: tuple[str, ...]
    order_completed: bool = False


WriteOffReasonName = Literal["WENT_OFF", "SPILLED", "STAFF", "OTHER"]


class WriteOffIn(In):
    qty: Qty
    reason: WriteOffReasonName
    recorded_by: Name
    note: str | None = Field(
        default=None, max_length=300, description="Required when reason is OTHER."
    )


class DrawnBatchOut(Out):
    batch_id: int
    qty: Qty


class WriteOffOut(Out):
    ingredient_id: int
    ingredient_name: str
    unit: str
    qty: Qty
    reason: str
    movement_type: str = Field(description="WASTE, or STAFF for staff drinks. Never EXPIRED.")
    movement_ids: tuple[int, ...]
    batches_drawn: tuple[DrawnBatchOut, ...]
    shortfall_qty: Qty = Field(description="Not covered by any batch; recorded unbatched.")
    value: Cost


class ChecklistIn(In):
    status: Literal["OK", "LOW"]
    responded_by: Name


class ChecklistOut(Out):
    ingredient_id: int
    ingredient_name: str
    status: str
    responded_at: datetime
    responded_by: str


class ParIn(In):
    min_qty: Qty
    changed_by: Name


class ParChangeOut(Out):
    ingredient_id: int
    ingredient_name: str
    min_qty_before: Qty
    min_qty_after: Qty
    max_qty: Qty
    auto_order_enabled: bool = Field(description="Reported, never changed by this write.")
    set_by: str
    set_at: datetime


class TierIn(In):
    tier: Literal["A", "B", "C"]
    changed_by: Name
    reason: str = Field(min_length=1, max_length=2000)


class TierOut(Out):
    ingredient_id: int
    ingredient_name: str
    tier_before: str
    tier_after: str
    would_clear_gate: bool
    clean_streak: int
    required_streak: int
    auto_order_enabled: bool = Field(description="Reported, never changed by this write.")
    tracking_enabled: bool
    note: str


# ==========================================================================
# Orders
# ==========================================================================

OrderAction = Literal["cancel", "mark_sent", "receive"]


class PurchaseOrderOut(PersistedOrderOut):
    supplier_id: int
    supplier_name: str
    supplier_archived: bool = False
    terms_are_placeholders: bool = Field(
        description="The supplier's terms are still unconfirmed today."
    )
    cancelled_at: datetime | None = None
    cancelled_by: str | None = None
    cancel_reason: str | None = None
    routing_reason: str | None = None
    actions: tuple[OrderAction, ...] = Field(
        description="What the web may do next. Never confirm: that happens in Telegram."
    )


class OrderCounts(Out):
    open: int = Field(description="DRAFT + PENDING_CONFIRM + CONFIRMED + SENT.")
    waiting: int = Field(description="DRAFT + PENDING_CONFIRM: waiting for someone in Telegram.")


class OrdersListResponse(Out):
    orders: tuple[PurchaseOrderOut, ...]
    counts: OrderCounts


class CancelIn(In):
    cancelled_by: Name
    reason: str | None = Field(default=None, max_length=400)


class MarkSentIn(In):
    sent_by: Name


class ReceiveLineIn(In):
    po_line_id: int
    received_packs: int | None = Field(default=None, ge=1)
    received_qty: Qty | None = None
    expires_on: date | None = None


class ReceiveIn(In):
    received_by: Name
    lines: list[ReceiveLineIn] = Field(min_length=1)


class ReceiveOut(Out):
    order: PurchaseOrderOut
    receipts: tuple[DeliveryOut, ...]


class ShopRunLineIn(In):
    ingredient_id: int
    qty: Qty
    paid_pence: int | None = Field(default=None, description="What was paid for this line.")
    expires_on: date | None = None


class ShopRunIn(In):
    bought_by: Name
    where: str = Field(min_length=1, max_length=80)
    reason: str | None = Field(default=None, max_length=300)
    lines: list[ShopRunLineIn] = Field(min_length=1)


class ShopRunOut(Out):
    routing_ids: tuple[int, ...]
    receipts: tuple[DeliveryOut, ...]
    paid_pence: int | None = Field(description="Null when any line's price was not recorded.")
    premium_pence: int | None = Field(description="Null when any line could not be priced.")


class ShopRunRowOut(Out):
    occurred_at: datetime
    where: str
    note: str
    ingredient_name: str | None
    amount_pence: int | None = Field(description="What it cost. Null = not recorded, never 0.")
    premium_pence: int | None
    source: Literal["tesco_routing", "expense"]
    bought_by: str | None = None


class ShopRunMonthOut(Out):
    month: str = Field(description="YYYY-MM")
    amount_pence: int | None = Field(description="Null when any run that month is unpriced.")
    priced_amount_pence: int = Field(description="The priced part, shown beside the gap.")
    runs: int


class ShopRunsResponse(Out):
    runs: tuple[ShopRunRowOut, ...]
    by_month: tuple[ShopRunMonthOut, ...]
    total_pence: int | None
    priced_total_pence: int
    priced_runs: int
    runs_count: int
    premium_total_pence: int
    notes: tuple[str, ...] = ()


# ==========================================================================
# Suppliers
# ==========================================================================

ChannelName = Literal["PORTAL", "EMAIL", "EDI", "MANUAL", "BROWSER_AGENT"]


class SupplierCreateIn(In):
    name: str = Field(min_length=1, max_length=160)
    created_by: Name
    order_channel: ChannelName = "MANUAL"
    kind: str | None = Field(default=None, max_length=40)
    contact: str | None = Field(default=None, max_length=400)
    order_url: str | None = Field(default=None, max_length=500)
    notes: str | None = None


class SupplierPatchIn(In):
    """Profile only. Terms are confirmed all together at /confirm, never field by field."""

    changed_by: Name
    name: str | None = Field(default=None, max_length=160)
    order_channel: ChannelName | None = None
    kind: str | None = Field(default=None, max_length=40)
    contact: str | None = Field(default=None, max_length=400)
    order_url: str | None = Field(default=None, max_length=500)
    notes: str | None = None


class SupplierArchiveIn(In):
    archived_by: Name


class SupplierWriteOut(Out):
    supplier: SupplierOut
    changed: tuple[str, ...] = ()
    restarred: tuple[str, ...] = Field(
        default=(),
        description="Ingredients whose preferred link moved to another supplier as a result.",
    )


class VsOtherOut(Out):
    supplier_name: str
    unit_price_pence: str
    diff_pct: float = Field(description="(this - other) / other x 100. Negative = cheaper.")


class SupplierProductOut(Out):
    supplier_product_id: int
    supplier_id: int
    ingredient_id: int
    ingredient_name: str
    ingredient_unit: str
    sku: str
    pack_size: Qty
    pack_unit: str
    price_pence: int
    unit_price_pence: str | None = Field(description="Per ingredient unit; null across units.")
    price_source: str | None
    is_preferred: bool
    moq_packs: int
    last_seen_price_at: datetime | None
    vs_best_other: VsOtherOut | None = Field(description="Null = the only supplier.")


class IngredientOptionOut(Out):
    ingredient_id: int
    name: str
    unit: str
    category: str | None


class SupplierProductsResponse(Out):
    supplier: SupplierOut
    products: tuple[SupplierProductOut, ...]
    ingredients: tuple[IngredientOptionOut, ...] = Field(
        description="Every active ingredient, A-Z: the picker for '+ Link an ingredient'."
    )


class ProductLinkIn(In):
    ingredient_id: int
    sku: str = Field(default="", max_length=80)
    pack_size: Qty
    pack_unit: UnitName
    price_pence: int = Field(ge=1)
    changed_by: Name


class ProductPatchIn(In):
    changed_by: Name
    sku: str | None = Field(default=None, max_length=80)
    pack_size: Qty | None = None
    pack_unit: UnitName | None = None
    price_pence: int | None = Field(default=None, ge=1)


class ProductActorIn(In):
    changed_by: Name


class ProductArchiveIn(In):
    archived_by: Name


class ProductWriteOut(Out):
    product: SupplierProductOut
    changed: tuple[str, ...] = ()
    recosted_items: int = Field(
        default=0, description="Menu items re-costed because the recipe price moved."
    )
    price_row_id: int | None = None
    restarred: str | None = Field(
        default=None, description="The ingredient re-starred to another link, if any."
    )
