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
    #: A batch reached expires_at with stock left. This is the honest waste figure
    #: the P&L needs and nobody currently has -- and it is what distinguishes
    #: "the recipe is wrong" from "we are over-ordering" in a drift report.
    EXPIRED = "EXPIRED"


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
    """How an order reaches a supplier. Spec 4.4.

    BROWSER_AGENT is retained alongside the spec's PORTAL: spec 9 puts browser
    automation behind a bounded tool interface, and a PORTAL supplier with no API
    is reached that way in practice. Keeping them distinct records whether we have
    a real integration or are driving a browser.
    """

    PORTAL = "PORTAL"
    EMAIL = "EMAIL"
    EDI = "EDI"
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


class Storage(enum.Enum):
    """Storage regime. Drives the transit buffer and the plausibility of a shelf life."""

    AMBIENT = "AMBIENT"
    CHILLED = "CHILLED"
    FROZEN = "FROZEN"


class SalesChannelName(enum.Enum):
    """Third-party marketplaces that are also ad platforms. Spec 4.6."""

    DELIVEROO = "DELIVEROO"
    JUST_EAT = "JUST_EAT"


class ChannelSourceKind(enum.Enum):
    """How a channel_metric row got here.

    Spec 4.6: partner APIs are gated to certified POS integrators, so assume we
    never get in. CSV is the path that works today from the owner's own portal
    exports; BROWSER_AGENT is the fallback and it will break.
    """

    CSV_UPLOAD = "CSV_UPLOAD"
    BROWSER_AGENT = "BROWSER_AGENT"
    PARTNER_API = "PARTNER_API"
    MANUAL = "MANUAL"


class AgentToolOutcome(enum.Enum):
    """Spec 9: every agent action is logged with inputs, output and the tool called."""

    OK = "OK"
    REFUSED = "REFUSED"
    FAILED = "FAILED"
    AWAITING_HUMAN = "AWAITING_HUMAN"


class CapKind(enum.StrEnum):
    """Why an order line is smaller than the forecast asked for. Spec 5.4, invariant 4.

    A STRUCTURED companion to `cap_reason`, which is an English sentence. The bot had to
    recover this by regex over prose that this codebase itself wrote, which meant a
    reword in `domain/ordering.py` silently broke the Russian output -- and a silently
    unexplained cap is the one thing invariant 4 cannot survive, because the owner raises
    the quantity and recreates exactly the waste the cap prevented.

    The sentence stays for humans reading a log. This is what code branches on.
    """

    SHELF_LIFE = "SHELF_LIFE"
    SEASON_END = "SEASON_END"
    OUT_OF_SEASON = "OUT_OF_SEASON"
    #: The line exists ONLY to clear the supplier's minimum order. Nobody asked for it.
    TOP_UP_MINIMUM = "TOP_UP_MINIMUM"
    #: ...or only to clear the free-delivery threshold. A different decision: one is a
    #: condition on ordering at all, the other is a price break.
    TOP_UP_FREE_DELIVERY = "TOP_UP_FREE_DELIVERY"
    #: Capped for a reason not in this enum. The days are still known and shown.
    OTHER = "OTHER"


class LowConfidenceKind(enum.StrEnum):
    """Why a forecast is not trustworthy. Invariant 9.

    Same reasoning as CapKind: invariant 9 says the reason stands IN PLACE OF the
    number, so the reason has to be renderable in another language, which means it
    cannot only exist as an English sentence.
    """

    #: Fewer than `min_history_days` of history.
    THIN_HISTORY = "THIN_HISTORY"
    #: No consumption at all in the window -- "nothing known", not "nothing needed".
    NO_HISTORY = "NO_HISTORY"
    #: A seasonal item with no previous occurrence to scale from.
    NO_PRIOR_SEASON = "NO_PRIOR_SEASON"
    #: Drift above tolerance, so the on-hand the forecast is netted against is suspect.
    UNTRUSTWORTHY_STOCK = "UNTRUSTWORTHY_STOCK"
    OTHER = "OTHER"


class ReceiptWarningKind(enum.StrEnum):
    """Why a received delivery is not a clean one. `DeliveryReceipt.warnings`.

    Same reasoning as CapKind, one field further down the pipe. The bot used to
    recognise these warnings by SUBSTRING -- `"no expiry entered" in warning` -- against
    sentences `services/receive_delivery.py` writes itself, so rewording the sentence
    that explains an assumed expiry silently turned the Russian message into
    "N more notes were written to the log". An assumed expiry is the single number that
    decides a write-off; it is not allowed to arrive as a count.
    """

    #: No date was read off the carton, so the ESTIMATE shelf life was used.
    EXPIRY_ASSUMED = "EXPIRY_ASSUMED"
    #: A date was entered, but it is not after the delivery: this arrived expired.
    EXPIRY_NOT_AFTER_RECEIPT = "EXPIRY_NOT_AFTER_RECEIPT"
    #: The ingredient is configured non-perishable and a date was entered anyway.
    NON_PERISHABLE_WITH_DATE = "NON_PERISHABLE_WITH_DATE"
    #: More arrived than was ordered. Recorded, because the stock is on the shelf.
    OVER_DELIVERY = "OVER_DELIVERY"
    #: No price given, so the ingredient's cached unit cost was used. A retail
    #: emergency buy usually cost more, so a write-off against it understates the loss.
    PRICE_FROM_CACHE = "PRICE_FROM_CACHE"
    OTHER = "OTHER"


class ForecastNoteKind(enum.StrEnum):
    """Everything a forecast says about itself. `ForecastResult.confidence_reasons`.

    `LowConfidenceKind` answers "is this number usable" and is PERSISTED on
    `po_line.low_confidence_kind`. This answers the wider question "what does the
    forecast want to tell you", including caveats that do not disqualify the figure --
    a clamped weekday factor, days in the window the cafe is closed. Both are needed:
    a surface that only had the blocking codes would have to fall back to prose for the
    rest, which is the failure this pairing exists to end.

    `low_confidence_kind` maps the blocking members onto the persisted enum, so the two
    can never disagree about which reasons stand in place of a number (invariant 9).
    """

    #: No consumption at all in the window. "Nothing known", not "nothing needed".
    NO_HISTORY = "NO_HISTORY"
    #: Fewer than `min_history_days` of history: a flat mean, no weekday shape.
    THIN_HISTORY = "THIN_HISTORY"
    #: A seasonal item whose season has no previous occurrence to scale from.
    NO_PRIOR_SEASON = "NO_PRIOR_SEASON"
    #: ...and the current season has not started either, so even the flat rate is empty.
    SEASON_NOT_STARTED = "SEASON_NOT_STARTED"
    #: `deseasonalise=False`: the literal spec 5.3 formula, which double-counts the
    #: weekday effect (ARCHITECTURE.md 8C). Advisory -- the caller asked for it.
    LITERAL_SPEC_FORMULA = "LITERAL_SPEC_FORMULA"
    #: A day-of-week factor hit `dow_factor_min` / `max`. Advisory.
    DOW_FACTOR_CLAMPED = "DOW_FACTOR_CLAMPED"
    #: Out-of-season days were EXCLUDED from the baseline rather than read as zero.
    SEASON_HISTORY_EXCLUDED = "SEASON_HISTORY_EXCLUDED"
    #: Declared closed days inside the forecast window, forecast as zero.
    CLOSED_DAYS_IN_WINDOW = "CLOSED_DAYS_IN_WINDOW"
    #: Days in the window that fall outside the season, forecast as zero.
    OUT_OF_SEASON_DAYS_IN_WINDOW = "OUT_OF_SEASON_DAYS_IN_WINDOW"
    #: The seasonal path read last year's occurrence day by day.
    SEASONAL_FROM_PRIOR = "SEASONAL_FROM_PRIOR"
    #: The year-on-year growth factor, and whether it was clamped or refused.
    SEASONAL_GROWTH = "SEASONAL_GROWTH"
    #: Days of last season with no record, filled with the running mean, not zero.
    SEASONAL_GAP_FILLED = "SEASONAL_GAP_FILLED"
    OTHER = "OTHER"

    @property
    def low_confidence_kind(self) -> LowConfidenceKind | None:
        """The persisted verdict this note implies, or None when it is only a caveat.

        One mapping, read by `domain/forecast.py` when it builds the result and by
        nothing else, so "which reasons replace the number" is decided once.
        """
        return _LOW_CONFIDENCE_BY_FORECAST_NOTE.get(self)

    @property
    def blocks_the_number(self) -> bool:
        return self.low_confidence_kind is not None


_LOW_CONFIDENCE_BY_FORECAST_NOTE: dict[ForecastNoteKind, LowConfidenceKind] = {
    ForecastNoteKind.NO_HISTORY: LowConfidenceKind.NO_HISTORY,
    ForecastNoteKind.THIN_HISTORY: LowConfidenceKind.THIN_HISTORY,
    ForecastNoteKind.NO_PRIOR_SEASON: LowConfidenceKind.NO_PRIOR_SEASON,
    # A season that has not started has no flat rate behind it either, so the honest
    # code is "nothing is known", not "no prior season to scale from".
    ForecastNoteKind.SEASON_NOT_STARTED: LowConfidenceKind.NO_HISTORY,
}


class OrderNoteKind(enum.StrEnum):
    """Why an order says what it says. `OrderSuggestion.notes`.

    Spec 5.4 forbids silently inflating or shrinking an order, so every adjustment
    writes a sentence. Those sentences were English-only, which meant the Russian bot
    dropped `purchase_order.notes` entirely: the owner read a quantity with no account
    of the par ceiling that cut it or the perishables invariant 5 kept out of the
    top-up. A code per note is what lets the message be written in her language from
    the same data, instead of translated at the last moment or discarded.

    One member per DECISION, not per sentence. The sentence may be reworded freely.
    """

    # --- shelf life and season (invariant 4) ---------------------------------
    #: One line was sized on a shorter window than the cover asked for.
    CAP_LINE = "CAP_LINE"
    #: The order-level roll-up of every capped line.
    CAP_SUMMARY = "CAP_SUMMARY"
    #: Seasonal, out of season, not ordered at all.
    OUT_OF_SEASON_NOT_ORDERED = "OUT_OF_SEASON_NOT_ORDERED"

    # --- par levels -----------------------------------------------------------
    #: Raised to bring on-hand up to `min_qty`.
    PAR_FLOOR_RAISED = "PAR_FLOOR_RAISED"
    #: The floor wanted more than the usable window holds, so invariant 4 won.
    PAR_FLOOR_BELOW_CAP = "PAR_FLOOR_BELOW_CAP"
    #: Cut by the `max_qty` ceiling.
    PAR_CEILING_CUT = "PAR_CEILING_CUT"
    #: ...and the ceiling is below one cover window of demand: a planned stockout.
    PAR_CEILING_BELOW_DEMAND = "PAR_CEILING_BELOW_DEMAND"
    #: Under the par floor with nothing forecast to move it. Reported, never ordered.
    BELOW_PAR_FLOOR_NOT_ORDERED = "BELOW_PAR_FLOOR_NOT_ORDERED"
    #: The order-level roll-up of the above.
    BELOW_PAR_FLOOR_SUMMARY = "BELOW_PAR_FLOOR_SUMMARY"
    #: Forecast, on-hand and open POs already cover the window.
    NOTHING_NEEDED = "NOTHING_NEEDED"
    #: `min_qty > max_qty`: the par level cannot produce an honest size.
    PAR_DATA_ERROR = "PAR_DATA_ERROR"
    #: A supplier product whose pack size is not positive.
    PACK_DATA_ERROR = "PACK_DATA_ERROR"
    #: No par level at all, so the ingredient was skipped.
    SKIPPED_NO_PAR = "SKIPPED_NO_PAR"

    # --- the two top-up decisions (spec 5.4, invariant 5) ---------------------
    TOP_UP_APPLIED = "TOP_UP_APPLIED"
    TOP_UP_IMPOSSIBLE = "TOP_UP_IMPOSSIBLE"
    TOP_UP_SHORT_OF_TARGET = "TOP_UP_SHORT_OF_TARGET"
    #: INVARIANT 5 made visible: perishables were available and were refused.
    TOP_UP_EXCLUDED_PERISHABLE = "TOP_UP_EXCLUDED_PERISHABLE"
    TOP_UP_EXCLUDED_SHELF_LIFE_UNKNOWN = "TOP_UP_EXCLUDED_SHELF_LIFE_UNKNOWN"
    TOP_UP_EXCLUDED_NO_VELOCITY = "TOP_UP_EXCLUDED_NO_VELOCITY"
    TOP_UP_POOL_EMPTY = "TOP_UP_POOL_EMPTY"
    #: Nothing needed, so the supplier's minimum does not apply.
    MINIMUM_NOT_APPLICABLE = "MINIMUM_NOT_APPLICABLE"
    FREE_DELIVERY_CLEARED = "FREE_DELIVERY_CLEARED"
    #: The fee is cheaper than the stock it would take to clear the threshold.
    FREE_DELIVERY_FEE_PAID = "FREE_DELIVERY_FEE_PAID"

    # --- facts about the run rather than about a line -------------------------
    #: Six of eight suppliers' terms are invented (ARCHITECTURE.md 8F.4).
    PLACEHOLDER_TERMS = "PLACEHOLDER_TERMS"
    #: A line left this supplier for a cheaper source.
    SOURCING_LINE_MOVED = "SOURCING_LINE_MOVED"
    #: The order was placed after this supplier's cutoff, so delivery slips a day.
    CUTOFF_MISSED = "CUTOFF_MISSED"
    #: Sized against a minimum supplied on the command line, not the stored one.
    WHAT_IF_MINIMUM = "WHAT_IF_MINIMUM"
    #: A tier C checklist answer put a line on this order at a human-chosen quantity.
    CHECKLIST_REQUEST = "CHECKLIST_REQUEST"
    OTHER = "OTHER"


class GateReasonKind(enum.StrEnum):
    """Which branch of the auto-order gate produced a decision. Spec 5.2, invariant 2.

    `GateDecision.reason` is a sentence written for `par_level.auto_order_reason`, where
    somebody reads it months later. This is the same branch as a value, so the bot can
    say WHY auto-ordering is off without matching English.
    """

    #: No drift observation at all: eligibility cannot be earned yet.
    NO_OBSERVATION = "NO_OBSERVATION"
    #: Above `warn_max_pct` (>15%): the ledger no longer describes the shelf.
    DRIFT_ABOVE_TOLERANCE = "DRIFT_ABOVE_TOLERANCE"
    #: Not tier A. Tier B is calculated but never auto-ordered (spec 4.7).
    NOT_TIER_A = "NOT_TIER_A"
    #: No par_level row, so nothing defines min/max and nothing can be sized.
    NO_PAR_LEVEL = "NO_PAR_LEVEL"
    #: In the 10-15% tuning band: stays manual, tune waste_factor and count again.
    TUNING_BAND = "TUNING_BAND"
    #: Clean, but fewer than `required_consecutive` counts in a row.
    STREAK_INCOMPLETE = "STREAK_INCOMPLETE"
    #: Two consecutive counts under the bar on a tier A ingredient with a par level.
    STREAK_EARNED = "STREAK_EARNED"


class RevokeCause(enum.StrEnum):
    """Why an EXISTING auto-order grant was taken away. `GateDecision.revoke_cause`.

    `ARCHITECTURE.md` 8A.3 queued this: a revocation is a material change in system
    behaviour -- orders that were being drafted stop being drafted -- and `reason` being
    prose meant the bot could only say "auto-ordering is off", never whether that was
    drift above tolerance or the ingredient dropping out of tier A. Those have different
    answers: one is "count again", the other is "this was never eligible".
    """

    #: Above `warn_max_pct`. The stock figures themselves are not trustworthy.
    DRIFT_ABOVE_TOLERANCE = "DRIFT_ABOVE_TOLERANCE"
    #: In the 10-15% band. The grant rested on two consecutive counts under 10% and one
    #: of them no longer holds, so the condition that justified it has expired.
    DRIFT_IN_TUNING_BAND = "DRIFT_IN_TUNING_BAND"
    #: Clean latest count, but the unbroken run is shorter than the gate requires.
    STREAK_BROKEN = "STREAK_BROKEN"
    #: No longer tier A. Tier B and C are never auto-ordered.
    TIER_NOT_A = "TIER_NOT_A"
    #: The par level is gone, so there is no min/max to size against.
    PAR_LEVEL_REMOVED = "PAR_LEVEL_REMOVED"
    #: The drift history is empty -- typically a reseed without `drift --backfill`.
    NO_OBSERVATION = "NO_OBSERVATION"


class GateAlertLevel(enum.StrEnum):
    """How loudly a gate decision has to reach the owner.

    `alert` was a bool, which forced one decision to carry two different meanings.
    Spec 5.2 only demands an alarm above 15%, and a 10-15% count that REVOKES an
    existing grant was therefore silent -- the gap `ARCHITECTURE.md` 8A.3 recorded.
    Losing auto-ordering is a behaviour change she has to hear about, but it is not the
    same statement as "your stock figures are untrustworthy", so it gets its own level
    instead of being promoted into the alarm.
    """

    NONE = "NONE"
    #: A material change in behaviour: auto-ordering has just been taken away.
    NOTICE = "NOTICE"
    #: The theoretical figures themselves cannot be trusted (>15%).
    ALARM = "ALARM"


class PaymentMethod(enum.Enum):
    """How the money arrived. `OTHER` exists so an unmapped method is kept and
    labelled rather than dropped or silently folded into CARD."""

    CASH = "CASH"
    CARD = "CARD"
    VOUCHER = "VOUCHER"
    ACCOUNT = "ACCOUNT"
    OTHER = "OTHER"


class PaymentSourceKind(enum.Enum):
    """Where a payment figure came from.

    Only `CSV_UPLOAD` is reachable today: the Lightspeed payments endpoint has
    never been probed and its shape is unknown, so `POS_API` exists as a label for
    when it is -- not as a claim that anything uses it.
    """

    CSV_UPLOAD = "CSV_UPLOAD"
    POS_API = "POS_API"
    MANUAL = "MANUAL"
