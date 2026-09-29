"""What the bot shows, as structured data. **No user-facing text lives here.**

The split is the whole point. `views.py` reads the database and produces these;
`formatters.py` turns them into Russian sentences. Nothing else in the codebase is
allowed to hold a Russian string (agent brief), and nothing in here may hold one --
so every field below is a number, a date, a flag or an *enum member*, never a phrase.

That constraint is why the enums exist. The services and `domain/` modules explain
themselves in English prose: `SuggestedLine.cap_reason` is
`"capped at 5 days -- Whole milk shelf life"`, `ForecastResult.confidence_reasons` is a
paragraph. A Russian bot cannot render English prose, and translating prose at the
formatter is guesswork.

**Those codes are no longer invented here.** They are `domain/enums.py`'s -- `CapKind`,
`LowConfidenceKind`, `ReceiptWarningKind`, `OrderNoteKind`, `RevokeCause` -- produced by
the same comparison that writes each sentence and re-exported through `domain/types.py`.
This module used to declare its own `CapKind` and `LowConfidenceKind` with *different
members* from the persisted ones (`SHORT_HISTORY` here against `THIN_HISTORY` there),
which is the same failure one layer up: two names for one fact, kept in step by hand.
`views.py` now reads the stored code instead of parsing the stored prose, and the
formatter writes its own Russian from the code plus the numbers. The prose is never
shown.

Where a code is absent -- an order written before the columns existed -- the kind is
`OTHER` and the formatter says something true but general. It never falls back to
printing the English.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from cafeops.domain.drift import DriftCause
from cafeops.domain.tiers import GateAction
from cafeops.domain.types import (
    CapKind,
    DriftVerdict,
    GateAlertLevel,
    LowConfidenceKind,
    OrderChannel,
    OrderNoteKind,
    POStatus,
    ReceiptWarningKind,
    RevokeCause,
    SaleChannel,
    Storage,
    Tier,
    Unit,
)
from cafeops.services.transactions_csv import CsvKind

__all__ = [
    "BasketLineView",
    "BasketView",
    "CapKind",
    "CapNotice",
    "CashDayView",
    "ChecklistItemView",
    "CountItemView",
    "CountResultView",
    "CountSessionKind",
    "CsvKind",
    "DeliveryLineView",
    "DeliveryOrderView",
    "DigestView",
    "DispatchView",
    "DriftAlertView",
    "EmergencyDigestView",
    "ExpiryLineView",
    "ExportView",
    "ImportResultView",
    "IngredientRefView",
    "LowConfidenceKind",
    "LowConfidenceNotice",
    "MenuPageView",
    "MenuPickView",
    "OrderLineView",
    "OrderView",
    "ReceiptIssue",
    "ReceiptView",
    "RecordedSaleView",
    "RetailRunView",
    "RevocationView",
    "StockLineView",
    "WriteOffView",
]

#: The receipt codes come from `domain/enums.py` like every other one now. The alias is
#: kept because `ReceiptIssue` is what the formatter and the handlers already call it, and
#: renaming a local name adds no meaning.
ReceiptIssue = ReceiptWarningKind


# ==========================================================================
# Notices: a stored code plus the numbers its sentence needs
# ==========================================================================


@dataclass(frozen=True, slots=True)
class CapNotice:
    """INVARIANT 4 MADE VISIBLE.

    If the owner does not know a line was capped on purpose she raises it and creates
    exactly the waste the cap prevented, so the kind has to survive as far as the message.
    `kind` and `days` are read from `po_line.cap_kind` and `po_line.cover_days`; nothing is
    recovered from `cap_reason`.
    """

    kind: CapKind
    #: Effective cover days the line was sized on. None when unknown.
    days: int | None = None
    #: The ingredient or season the cap belongs to, for the sentence.
    subject: str | None = None


@dataclass(frozen=True, slots=True)
class LowConfidenceNotice:
    """Invariant 9: the reason is shown IN PLACE OF the number, so it must be sayable."""

    kind: LowConfidenceKind
    history_days: int | None = None
    needed_days: int | None = None


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
    #: How loudly this has to be said. A REVOKE is a NOTICE -- auto-ordering has just
    #: stopped, which is a change in behaviour -- and only >15% is an ALARM about the
    #: figures themselves (`ARCHITECTURE.md` 8A.3).
    alert_level: GateAlertLevel = GateAlertLevel.NONE
    #: WHY the grant was taken away. Set only on a revoke: "never earned it" and "just
    #: lost it" are different messages and the bot could not previously tell them apart.
    revoke_cause: RevokeCause | None = None


@dataclass(frozen=True, slots=True)
class RevocationView:
    """Auto-ordering was TAKEN AWAY from this ingredient, and when, and why.

    Separate from `DriftAlertView` because it is a different kind of statement and it
    survives differently. A drift alert is re-derived from today's history every time the
    digest runs; a revocation is an EVENT, and the gate is stateless -- once the flag is
    off, `evaluate_gate` reports HOLD and the event is invisible. So this is read from
    `par_level.auto_order_revoked_at` / `auto_order_revoke_cause` instead.

    It matters because losing auto-ordering changes what the system does: orders that were
    being drafted stop being drafted. `ARCHITECTURE.md` 8A.3 called that out as silent, and
    the count message alone only reaches whoever happened to be counting.
    """

    ingredient_id: int
    name: str
    unit: Unit
    revoked_at: datetime
    cause: RevokeCause
    #: The newest drift figure, when there is one. Withheld rather than guessed.
    drift_pct: float | None = None


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
    #: Non-None when a tier C checklist answer put this line here, naming who chose the
    #: quantity. Tier C is never calculated (spec 4.7), so this number is a person's
    #: decision and must never be shown as though it were a forecast.
    checklist_requested_by: str | None = None

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
    #: `purchase_order.note_codes`. The order's own explanations, as codes, so the
    #: formatter can write them in Russian instead of dropping `purchase_order.notes`
    #: because it is English (spec 5.4 forbids a silent adjustment, and a sentence she
    #: cannot read is silent). Empty for an order written before the column existed.
    notes: tuple[OrderNoteKind, ...] = ()
    #: Confirming stages a basket on the supplier's website (services/order_dispatch).
    stages_basket: bool = False

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

    @property
    def checklist_lines(self) -> tuple[OrderLineView, ...]:
        return tuple(line for line in self.lines if line.checklist_requested_by is not None)


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
    #: The supplier's website basket is staged by the browser worker (or a cart link).
    stages_basket: bool = False
    #: The staging job, when one was started. The order is NOT sent either way.
    staged_job_id: int | None = None
    #: A cart link that is ready to open now; None while the worker fills the basket.
    staged_basket_url: str | None = None
    #: Why staging could not start, verbatim from the service (English, operator-facing).
    staging_refused: str | None = None


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
    #: See `DriftAlertView`. A revoke at the count is the moment she most needs to be
    #: told, because she is standing at the shelf that caused it.
    alert_level: GateAlertLevel = GateAlertLevel.NONE
    revoke_cause: RevokeCause | None = None


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
# The retail emergency, and the morning digest
# ==========================================================================


@dataclass(frozen=True, slots=True)
class RetailRunView:
    """One logged trip to Tesco, from `tesco_routing`."""

    ingredient_name: str
    occurred_at: datetime
    #: `None` when either unit price was unknown. Never zero for unknown (invariant 8).
    premium_pence: int | None


@dataclass(frozen=True, slots=True)
class EmergencyDigestView:
    """The panic-buy log, summarised. Spec 4.4's most valuable report.

    `build_split` computes emergency lines and the ordering job counts them, but the
    morning digest never said a word about them -- so the one figure that argues for
    fixing the ordering cadence lived in a CLI report nobody runs at 07:00. Spec 4.4:
    "the accumulated log is the argument for fixing the ordering pattern", which means
    the accumulation has to be somewhere she reads.

    Two windows on purpose. `recent_*` is what happened lately, which is actionable;
    `total_*` is every routing on record, which is the argument. One without the other is
    either a shrug or a number with no trend behind it.
    """

    window_days: int
    recent_runs: int
    recent_premium_pence: int
    total_runs: int
    total_premium_pence: int
    #: How many of `total_runs` carried both unit prices. A premium summed over 9 of 14
    #: routings understates the case, and the only way to see that is to be told
    #: (invariant 8).
    total_priced_runs: int
    #: name -> (routings, premium_pence) over the whole log, worst first.
    by_ingredient: tuple[tuple[str, int, int], ...] = ()
    latest: tuple[RetailRunView, ...] = ()

    @property
    def any_runs(self) -> bool:
        return self.total_runs > 0

    @property
    def unpriced_runs(self) -> int:
        return self.total_runs - self.total_priced_runs


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
    #: The retail premium paid for being late, with its running total. Spec 4.4.
    emergency: EmergencyDigestView | None = None
    #: Ingredients that LOST auto-ordering recently. A material change in behaviour, and
    #: the digest is the one channel that reaches her whether or not she was counting.
    revocations: tuple[RevocationView, ...] = ()


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


# ==========================================================================
# Hand-typed transactions, cash, files (DECISIONS 28). Produced by `money_views`.
# ==========================================================================


@dataclass(frozen=True, slots=True)
class MenuPickView:
    """One sellable item on a picker button."""

    menu_item_id: int
    name: str
    size: str
    price_pence: int

    @property
    def label(self) -> str:
        return f"{self.name} {self.size}".strip()


@dataclass(frozen=True, slots=True)
class MenuPageView:
    """One page of items in a category, or the categories themselves when `category`
    is None and `items` is empty."""

    categories: tuple[str, ...]
    category_index: int | None
    items: tuple[MenuPickView, ...]
    page: int
    pages: int

    @property
    def category(self) -> str | None:
        if self.category_index is None or self.category_index >= len(self.categories):
            return None
        return self.categories[self.category_index]


@dataclass(frozen=True, slots=True)
class BasketLineView:
    menu_item_id: int
    name: str
    size: str
    qty: Decimal
    unit_price_pence: int
    gross_pence: int
    #: The person typed a price other than the menu's (a delivery app's own price).
    price_is_custom: bool

    @property
    def label(self) -> str:
        return f"{self.name} {self.size}".strip()


@dataclass(frozen=True, slots=True)
class BasketView:
    channel: SaleChannel
    sold_on: date | None
    lines: tuple[BasketLineView, ...]

    @property
    def total_pence(self) -> int:
        return sum(line.gross_pence for line in self.lines)


@dataclass(frozen=True, slots=True)
class RecordedSaleView:
    receipt_id: str
    first_sale_id: int
    channel: SaleChannel
    sold_at: datetime
    recorded_by: str | None
    voided: bool
    lines: tuple[BasketLineView, ...]

    @property
    def total_pence(self) -> int:
        return sum(line.gross_pence for line in self.lines)


@dataclass(frozen=True, slots=True)
class CashDayView:
    day: date
    #: What is on the day already, before this entry. None: nothing typed yet.
    cash_pence: int | None
    card_pence: int | None
    #: The day's cash came from an export and cannot be typed over. The source name.
    locked_by: str | None


@dataclass(frozen=True, slots=True)
class ImportResultView:
    """A dry run or a commit of one uploaded file. `written` says which."""

    kind: CsvKind
    filename: str
    written: bool
    #: The whole file was refused: why. Nothing else below is meaningful.
    refused: str | None = None
    #: CHANNEL_REPORT: which platform, once known.
    platform: str | None = None
    #: TRANSACTIONS.
    receipts: int = 0
    lines: int = 0
    gross_pence: int = 0
    already_recorded: int = 0
    by_channel: tuple[tuple[SaleChannel, int], ...] = ()
    since: date | None = None
    until: date | None = None
    #: PAYMENTS and CHANNEL_REPORT: day rows, and for a channel report item rows.
    days_inserted: int = 0
    days_updated: int = 0
    items_inserted: int = 0
    items_updated: int = 0
    #: Rows the importer refused, with the reason (English from the importer; the
    #: formatter shows them verbatim, quoted, because a rejected row must be findable).
    rejected: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExportView:
    filename: str
    data: bytes
    since: date
    until: date
    receipts: int
    lines: int
    gross_pence: int
    voided_lines: int
