"""What the bot shows, as structured data. **No user-facing text lives here.**

The split is the whole point. `views.py` reads the database and produces these;
`formatters.py` turns them into Russian sentences. Nothing else in the codebase is
allowed to hold a Russian string (agent brief), and nothing in here may hold one --
so every field below is a number, a date, a flag or an *enum member*, never a phrase.

That constraint is why the enums exist. The services and `domain/` modules explain
themselves in English prose: `SuggestedLine.cap_reason` is
`"capped at 5 days -- Whole milk shelf life"`, `ForecastResult.confidence_reasons` is a
paragraph. A Russian bot cannot render English prose, and translating prose at the
formatter is guesswork. So `views.py` *classifies* those facts into `CapKind`,
`LowConfidenceKind` and `ReceiptIssue` here, and the formatter writes its own Russian
from the classification plus the numbers. The prose is never shown.

Where a classification fails, the kind is `OTHER` and the formatter says something true
but general. It never falls back to printing the English.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from cafeops.domain.drift import DriftCause
from cafeops.domain.tiers import GateAction
from cafeops.domain.types import (
    DriftVerdict,
    OrderChannel,
    POStatus,
    Storage,
    Tier,
    Unit,
)

__all__ = [
    "CapKind",
    "CapNotice",
    "ChecklistItemView",
    "CountItemView",
    "CountResultView",
    "CountSessionKind",
    "DeliveryLineView",
    "DeliveryOrderView",
    "DigestView",
    "DispatchView",
    "DriftAlertView",
    "ExpiryLineView",
    "IngredientRefView",
    "LowConfidenceKind",
    "LowConfidenceNotice",
    "OrderLineView",
    "OrderView",
    "ReceiptIssue",
    "ReceiptView",
    "StockLineView",
    "WriteOffView",
]


# ==========================================================================
# Classifications: prose in, enum out
# ==========================================================================


class CapKind(enum.StrEnum):
    """Why a line is smaller than the forecast asked for. Spec 5.4, invariant 4.

    INVARIANT 4 MADE VISIBLE. If the owner does not know a line was capped on purpose,
    she raises it and creates exactly the waste the cap prevented -- so the kind has to
    survive as far as the message, not just the database column.
    """

    SHELF_LIFE = "SHELF_LIFE"
    SEASON_END = "SEASON_END"
    OUT_OF_SEASON = "OUT_OF_SEASON"
    #: The line exists ONLY to clear the supplier's minimum order. Nobody asked for it.
    TOP_UP_MINIMUM = "TOP_UP_MINIMUM"
    #: ...or only to clear the free-delivery threshold.
    TOP_UP_FREE_DELIVERY = "TOP_UP_FREE_DELIVERY"
    #: Capped, but the reason did not classify. The days are still known and shown.
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class CapNotice:
    kind: CapKind
    #: Effective cover days the line was sized on. None when unknown.
    days: int | None = None
    #: The ingredient or season the cap belongs to, for the sentence.
    subject: str | None = None


class LowConfidenceKind(enum.StrEnum):
    """Invariant 9: the reason is shown IN PLACE OF the number, so it must be sayable."""

    NO_HISTORY = "NO_HISTORY"
    SHORT_HISTORY = "SHORT_HISTORY"
    FIRST_SEASON = "FIRST_SEASON"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class LowConfidenceNotice:
    kind: LowConfidenceKind
    history_days: int | None = None
    needed_days: int | None = None


class ReceiptIssue(enum.StrEnum):
    """Something worth saying about a received delivery, as a code rather than prose."""

    EXPIRY_ASSUMED = "EXPIRY_ASSUMED"
    EXPIRY_BEFORE_RECEIPT = "EXPIRY_BEFORE_RECEIPT"
    OVER_DELIVERY = "OVER_DELIVERY"
    PRICE_FROM_CACHE = "PRICE_FROM_CACHE"
    NON_PERISHABLE_WITH_DATE = "NON_PERISHABLE_WITH_DATE"
    #: Counted, never printed as English. See the module docstring.
    UNCLASSIFIED = "UNCLASSIFIED"


# ==========================================================================
# Stock (invariant 6)
# ==========================================================================


@dataclass(frozen=True, slots=True)
class StockLineView:
    """One ingredient's theoretical on-hand plus the evidence behind it.

    `has_count_basis` is the whole of invariant 6 at this level: False means the figure
    is a bare movement sum with no physical anchor, and the formatter must say so rather
    than printing a number that looks counted.
    """

    ingredient_id: int
    name: str
    unit: Unit
    tier: Tier
    qty: Decimal
    #: Always True. `OnHand.is_theoretical` is always True and this carries it forward
    #: so no formatter can print a figure without knowing which kind it is.
    is_theoretical: bool
    has_count_basis: bool
    basis_count_qty: Decimal | None
    basis_counted_at: datetime | None
    movement_count: int
    days_since_count: int | None
    soonest_expiry_days: int | None
    batch_qty: Decimal
    unbatched_qty: Decimal


@dataclass(frozen=True, slots=True)
class ExpiryLineView:
    ingredient_name: str
    unit: Unit
    qty: Decimal
    expires_at: datetime
    days_left: int
    value_pence: Decimal | None


@dataclass(frozen=True, slots=True)
class WriteOffView:
    ingredient_name: str
    unit: Unit
    qty: Decimal
    expired_at: datetime
    days_overdue: int
    loss_pence: Decimal | None
    expired_after_opening: bool
    expiry_was_assumed: bool


@dataclass(frozen=True, slots=True)
class DriftAlertView:
    ingredient_id: int
    name: str
    unit: Unit
    drift_pct: float | None
    verdict: DriftVerdict | None
    gate_action: GateAction
    auto_order_enabled: bool
    alert: bool
    clean_streak: int
    required_streak: int
    cause: DriftCause | None = None
    expiry_share: float | None = None


# ==========================================================================
# Orders (invariants 1, 4, 9 and the top-up)
# ==========================================================================


@dataclass(frozen=True, slots=True)
class OrderLineView:
    po_line_id: int
    ingredient_id: int
    ingredient_name: str
    unit: Unit
    suggested_packs: int
    packs: int
    pack_size: Decimal
    pack_unit: Unit
    unit_price_pence: int
    need_qty: Decimal | None
    #: True when this line exists only to clear a minimum or a delivery threshold.
    is_top_up: bool
    cap: CapNotice | None
    #: Invariant 9. When set, the quantity is still shown -- it is what will be bought --
    #: but the FORECAST behind it is not, and the reason takes its place.
    low_confidence: LowConfidenceNotice | None

    @property
    def line_total_pence(self) -> int:
        return self.packs * self.unit_price_pence

    @property
    def adjusted(self) -> bool:
        return self.packs != self.suggested_packs


@dataclass(frozen=True, slots=True)
class OrderView:
    po_id: int
    status: POStatus
    supplier_id: int
    supplier_name: str
    channel: OrderChannel
    target_delivery_date: date
    lines: tuple[OrderLineView, ...]
    delivery_fee_pence: int
    min_order_pence: int
    free_delivery_threshold_pence: int | None
    lead_time_days: int
    delivery_weekdays: tuple[int, ...]
    #: Six of eight suppliers' terms are invented (`ARCHITECTURE.md` 8F.4). Every order
    #: built on them has to say so on its face.
    terms_are_placeholders: bool
    min_order_topped_up: bool
    confirmed_by: str | None
    confirmed_at: datetime | None
    #: BROWSER_AGENT and MANUAL stop at a filled basket / a shopping list. The message
    #: must say "basket ready, press the button", never "ordered".
    requires_human_completion: bool
    routing_reason_present: bool

    @property
    def goods_pence(self) -> int:
        return sum(line.line_total_pence for line in self.lines)

    @property
    def total_pence(self) -> int:
        fee = self.delivery_fee_pence
        threshold = self.free_delivery_threshold_pence
        if threshold is not None and self.goods_pence >= threshold:
            fee = 0
        return self.goods_pence + fee

    @property
    def meets_minimum(self) -> bool:
        return self.goods_pence >= self.min_order_pence

    @property
    def top_up_lines(self) -> tuple[OrderLineView, ...]:
        return tuple(line for line in self.lines if line.is_top_up)

    @property
    def capped_lines(self) -> tuple[OrderLineView, ...]:
        return tuple(
            line
            for line in self.lines
            if line.cap is not None
            and line.cap.kind not in (CapKind.TOP_UP_MINIMUM, CapKind.TOP_UP_FREE_DELIVERY)
        )

    @property
    def low_confidence_lines(self) -> tuple[OrderLineView, ...]:
        return tuple(line for line in self.lines if line.low_confidence is not None)


@dataclass(frozen=True, slots=True)
class DispatchView:
    """The outcome of handing a CONFIRMED order to its channel adapter."""

    po_id: int
    supplier_name: str
    channel: OrderChannel
    succeeded: bool
    requires_human_completion: bool
    target_url: str | None
    #: Number of numbered steps in a BROWSER_AGENT script. The script itself is English
    #: supplier-facing text and is not shown in the chat.
    instruction_steps: int
    items: int
    total_pence: int


# ==========================================================================
# Counting
# ==========================================================================


class CountSessionKind(enum.StrEnum):
    #: Weekly, every tracked ingredient.
    FULL = "FULL"
    #: Twice-weekly, tier A only.
    EXPRESS_A = "EXPRESS_A"


@dataclass(frozen=True, slots=True)
class CountItemView:
    ingredient_id: int
    name: str
    unit: Unit
    tier: Tier
    #: The theoretical figure, carried with its basis so the prompt can be honest about
    #: what the person is being asked to check against (invariant 6).
    stock: StockLineView


@dataclass(frozen=True, slots=True)
class CountResultView:
    ingredient_id: int
    name: str
    unit: Unit
    counted_qty: Decimal
    theoretical_qty: Decimal
    #: None for an ingredient's first count: there is no anchor, so there is no drift.
    drift_pct: float | None
    verdict: DriftVerdict | None
    gate_action: GateAction
    auto_order_enabled: bool
    alert: bool
    clean_streak: int
    required_streak: int
    suggested_waste_factor: Decimal | None
    current_waste_factor: Decimal
    cause: DriftCause | None
    back_dated: bool


@dataclass(frozen=True, slots=True)
class ChecklistItemView:
    """Tier C: yes/no, no numbers. Spec 4.7."""

    ingredient_id: int
    name: str
    unit: Unit
    #: Whether the last answer was LOW, and when. None when never answered.
    last_was_low: bool | None
    last_answered_at: datetime | None


# ==========================================================================
# Deliveries
# ==========================================================================


@dataclass(frozen=True, slots=True)
class DeliveryLineView:
    po_line_id: int
    ingredient_id: int
    ingredient_name: str
    unit: Unit
    ordered_packs: int
    pack_size: Decimal
    pack_unit: Unit
    expected_qty: Decimal | None
    already_received_qty: Decimal
    storage: Storage
    #: What the expiry would be assumed as, and whether that default is an ESTIMATE.
    default_shelf_life_days: int | None
    shelf_life_is_estimate: bool
    open_life_days: int | None

    @property
    def outstanding(self) -> bool:
        if self.expected_qty is None:
            return self.already_received_qty <= 0
        return self.already_received_qty < self.expected_qty


@dataclass(frozen=True, slots=True)
class DeliveryOrderView:
    po_id: int
    supplier_name: str
    status: POStatus
    target_delivery_date: date
    lines: tuple[DeliveryLineView, ...]

    @property
    def outstanding_lines(self) -> tuple[DeliveryLineView, ...]:
        return tuple(line for line in self.lines if line.outstanding)


@dataclass(frozen=True, slots=True)
class ReceiptView:
    batch_id: int
    ingredient_name: str
    unit: Unit
    qty: Decimal
    received_at: datetime
    expires_at: datetime | None
    expiry_was_assumed: bool
    days_left: int | None
    value_pence: Decimal
    order_completed: bool
    open_life_days: int | None
    issues: tuple[ReceiptIssue, ...]
    unclassified_issues: int = 0


# ==========================================================================
# The morning digest
# ==========================================================================


@dataclass(frozen=True, slots=True)
class DigestView:
    as_of: datetime
    local_date: date
    tracked_count: int
    #: Tier A first -- these are the figures the ordering path leans on.
    headline: tuple[StockLineView, ...]
    unanchored: tuple[StockLineView, ...]
    negative: tuple[StockLineView, ...]
    unbatched: tuple[StockLineView, ...]
    short_dated: tuple[ExpiryLineView, ...]
    #: From a DRY-RUN sweep. The digest reports; the sweep job writes.
    pending_write_offs: tuple[WriteOffView, ...]
    already_written_off: int
    drafts: tuple[OrderView, ...]
    deliveries_expected: tuple[DeliveryOrderView, ...]
    counts_due: tuple[StockLineView, ...]
    count_overdue_days: int
    drift_alerts: tuple[DriftAlertView, ...]
    checklist_due: tuple[ChecklistItemView, ...]
    #: Tier C items whose MOST RECENT answer was «running low». Without this a
    #: checklist answer goes nowhere: tier C has no par level and no forecast, so the
    #: only thing that can act on a LOW is a person reading it next to the orders.
    checklist_low: tuple[ChecklistItemView, ...]
    telegram_configured: bool


@dataclass(frozen=True, slots=True)
class IngredientRefView:
    """Just enough to name an ingredient and ask for a quantity in the right unit."""

    ingredient_id: int
    name: str
    unit: Unit
    tier: Tier
    storage: Storage
    shelf_life_days: int | None
    shelf_life_is_estimate: bool
