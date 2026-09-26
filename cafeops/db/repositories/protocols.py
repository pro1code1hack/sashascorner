"""Repository protocols. Signatures only -- no implementations, no business logic.

The five Phase 1 agents code against these; the bot mocks them. Implementations
land in sibling modules and may change freely as long as these signatures hold.

All SYNC on purpose (spec 3). SQLite has one writer; async repositories would buy
nothing at 40 transactions a day and make reasoning worse. Async callers reach
them through `asyncio.to_thread`.

THIS FILE IS INTEGRATOR-OWNED. An agent that needs a change raises it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from cafeops.db.models.enums import ExpirySource
from cafeops.domain.types import (
    BatchSpec,
    ChecklistStatus,
    ConsumptionPoint,
    DepletionAllocation,
    ExpiryLoss,
    IngredientSnapshot,
    MenuItemSpec,
    ModifierSpec,
    MovementSpec,
    MovementType,
    PackChoice,
    ParSpec,
    PriceSource,
    ResolvedRecipe,
    SaleLine,
    SeasonSpec,
    ShelfLifeSpec,
    SourcingOption,
    SupplierSpec,
    SupplierTerms,
    Tier,
)


@runtime_checkable
class IngredientRepository(Protocol):
    def get(self, ingredient_id: int) -> IngredientSnapshot | None: ...

    def get_by_name(self, name: str) -> IngredientSnapshot | None: ...

    def list_all(self) -> list[IngredientSnapshot]: ...

    def list_tracked(self, *, tiers: Sequence[Tier] | None = None) -> list[IngredientSnapshot]: ...

    def set_waste_factor(self, ingredient_id: int, waste_factor: Decimal) -> None: ...

    def cost_per_unit_at(
        self, ingredient_id: int, at: datetime
    ) -> tuple[Decimal, PriceSource] | None:
        """Effective-dated cost. None when the ingredient has no price at that date.

        Never return zero for "unknown" -- invariant 6 depends on the difference.
        """
        ...

    def add_price(
        self,
        ingredient_id: int,
        *,
        pack_size: Decimal,
        pack_unit: object,
        pack_cost_pence: int,
        effective_from: datetime,
        source: PriceSource,
        note: str | None = None,
    ) -> int:
        """Opens a new price row, closes the previous one, refreshes the cache."""
        ...


@runtime_checkable
class CompositionRepository(Protocol):
    """Everything `resolve_recipe` needs, already narrowed to a date."""

    def item_spec(self, menu_item_id: int, at: datetime) -> MenuItemSpec | None:
        """Assemble a MenuItemSpec valid at `at`.

        Effective dating lives here: the returned components and options are those
        in force at `at`, so resolving a March sale uses March's recipe.
        """
        ...

    def item_specs(self, menu_item_ids: Sequence[int], at: datetime) -> dict[int, MenuItemSpec]: ...

    def modifiers(
        self, modifier_ids: Sequence[int], at: datetime | None = None
    ) -> list[ModifierSpec]:
        """As they behaved at `at` (`modifier_version`); the `modifier` cache when None."""
        ...

    def menu_item_ids_for_template(self, template_id: int) -> list[int]: ...

    def template_ids_using_ingredient(self, ingredient_id: int) -> list[int]: ...

    def close_and_open_component(
        self,
        component_id: int,
        *,
        qty_by_size: dict[str, str],
        effective_from: datetime,
    ) -> int:
        """Effective-dated edit: close the old row, open a new one.

        INVARIANT 3: never an in-place update. Returns the new component id.
        """
        ...


@runtime_checkable
class MenuCostRepository(Protocol):
    def get(self, menu_item_id: int) -> tuple[Decimal | None, PriceSource | None, bool] | None:
        """(cost_pence, source, has_missing_cost), or None when no row exists.

        The cost and source are themselves OPTIONAL inside the tuple. "A row exists
        and we know this item's cost is unknown" is a different fact from "no row
        has been computed", and invariant 8 depends on being able to say which.
        """
        ...

    def units_sold_bulk(
        self, menu_item_ids: Sequence[int], *, since: date, until: date
    ) -> dict[int, Decimal]:
        """One query for a batch -- the impact preview needs 300 of these."""
        ...

    def upsert(self, menu_item_id: int, recipe: ResolvedRecipe, computed_at: datetime) -> None: ...

    def units_sold(self, menu_item_id: int, *, since: date, until: date) -> Decimal: ...


@runtime_checkable
class SaleRepository(Protocol):
    def upsert_many(self, lines: Iterable[object]) -> tuple[int, int]:
        """Idempotent on lightspeed_line_id. Returns (inserted, skipped)."""
        ...

    def pending_expansion(self, *, limit: int | None = None) -> list[SaleLine]: ...

    def mark_expanded(self, sale_ids: Sequence[int], at: datetime) -> None: ...

    def latest_sold_at(self) -> datetime | None: ...


@runtime_checkable
class StockRepository(Protocol):
    def latest_count(
        self, ingredient_id: int, *, before: datetime
    ) -> tuple[Decimal, datetime] | None:
        """(counted_qty, counted_at) of the most recent count at or before `before`."""
        ...

    def movement_sum_between(
        self, ingredient_id: int, *, after: datetime | None, until: datetime
    ) -> tuple[Decimal, int]:
        """Signed sum and row count over (after, until]."""
        ...

    def consumption_between(
        self,
        ingredient_id: int,
        *,
        after: datetime | None,
        until: datetime,
        movement_types: Sequence[MovementType] = (MovementType.SALE,),
    ) -> Decimal:
        """Positive consumption magnitude over an INSTANT window (after, until].

        Distinct from `daily_consumption`, which buckets by local calendar day.
        Waste-factor tuning needs count-to-count, and a calendar-day series cannot
        express "between 21:04 on Tuesday and 20:58 three Tuesdays later".
        """
        ...

    def movements_for(self, ref_type: str, ref_id: int) -> list[MovementSpec]:
        """Every movement written for one source row. Used to reverse an expansion."""
        ...

    def expired_qty_between(
        self, ingredient_id: int, *, after: datetime | None, until: datetime
    ) -> Decimal:
        """EXPIRED magnitude in the window.

        Spec 5.2's second diagnostic: if write-offs explain most of a drift gap, the
        problem is over-ordering, not a bad recipe. The two fixes are opposite, so
        one undifferentiated number tells the owner to do the wrong thing.
        """
        ...

    def append_movements(self, movements: Iterable[MovementSpec]) -> int: ...

    def append_movement(self, movement: MovementSpec) -> int: ...

    def record_count(
        self,
        ingredient_id: int,
        counted_qty: Decimal,
        counted_at: datetime,
        counted_by: str,
        note: str | None = None,
    ) -> int:
        """Returns the new stock_count id."""
        ...

    def daily_consumption(
        self,
        ingredient_id: int,
        *,
        since: date,
        until: date,
        movement_types: Sequence[MovementType] = (MovementType.SALE,),
    ) -> list[ConsumptionPoint]:
        """Positive consumption magnitudes per LOCAL calendar day.

        Local, not UTC: a 23:30 BST sale belongs to that trading day, and UTC
        bucketing would smear the weekday pattern across midnight.
        """
        ...


@runtime_checkable
class DriftRepository(Protocol):
    def record(
        self,
        ingredient_id: int,
        stock_count_id: int,
        theoretical_qty: Decimal,
        counted_qty: Decimal,
        drift_pct: float,
        waste_factor_at_count: Decimal,
        observed_at: datetime,
    ) -> int: ...

    def recent_drift_pcts(self, ingredient_id: int, *, limit: int = 2) -> list[float]:
        """Most recent first. The auto-order gate reads this."""
        ...

    def history(self, ingredient_id: int, *, limit: int = 20) -> list[object]:
        """Recent DriftObservation rows, newest first, for the rolling view."""
        ...

    def counts_missing_observations(self, *, limit: int | None = None) -> list[int]:
        """stock_count ids with no drift observation -- the backfill work queue."""
        ...


@runtime_checkable
class ParLevelRepository(Protocol):
    def get(self, ingredient_id: int) -> ParSpec | None: ...

    def revoke_auto_order(self, ingredient_id: int, *, reason: str, at: datetime) -> None:
        """Turn auto-ordering OFF. Callable from anywhere -- the safe direction.

        There is deliberately NO symmetric `enable`. Invariant 2 says eligibility is
        earned through drift history, and a protocol method that can grant it is an
        invitation to take the shortcut. Granting goes through
        `apply_gate_decision` with a decision the gate actually produced.
        """
        ...

    def apply_gate_decision(self, decision: object, *, at: datetime) -> bool:
        """Apply a GateDecision from domain/tiers. The ONLY path that can enable.

        Returns True when the stored flag changed. Implementations must refuse a
        decision that did not come from the gate.
        """
        ...

    def audit(self, ingredient_id: int) -> object | None:
        """Who turned this on, on what evidence -- and why it was last taken away.

        Returns an object carrying `auto_order_enabled`, `granted_at`, `revoked_at`,
        `reason` and `revoke_cause` (`repositories/par.AutoOrderAudit`). Declared as
        `object` for the same reason `apply_gate_decision` takes one: the concrete
        dataclass lives in the implementation, and this file may not import it.

        **`revoke_cause` is why this method exists rather than a bare flag read.** The gate
        is stateless -- `domain/tiers.evaluate_gate` re-derives its verdict from today's
        drift history, so once `auto_order_enabled` is already False it reports HOLD and
        the fact that a revocation HAPPENED is gone. `par_level` is the only record, and a
        revocation is a material change in behaviour: orders that were being drafted stop
        being drafted (`ARCHITECTURE.md` 8A.3). The morning digest reads this.

        The previous signature claimed a 3-tuple and the implementation had always
        returned a dataclass. Corrected rather than preserved: a protocol nobody can
        satisfy is worse than none.
        """
        ...


@runtime_checkable
class SupplierRepository(Protocol):
    def get(self, supplier_id: int) -> SupplierSpec | None: ...

    def list_all(self) -> list[SupplierSpec]: ...

    def preferred_pack(self, ingredient_id: int, supplier_id: int) -> PackChoice | None: ...

    def packs_for_supplier(self, supplier_id: int) -> list[PackChoice]: ...


@runtime_checkable
class PurchaseOrderRepository(Protocol):
    def open_qty_for(self, ingredient_id: int) -> Decimal:
        """Quantity already on POs that are neither RECEIVED nor CANCELLED."""
        ...

    def create_draft(self, suggestion: object) -> int: ...

    def confirm(
        self, po_id: int, *, confirmed_by: str, at: datetime, final_packs: dict[int, int]
    ) -> None:
        """Must refuse to advance a PO without `confirmed_by` (invariant 1)."""
        ...

    def mark_sent(self, po_id: int, *, at: datetime) -> None: ...

    def add_checklist_line(
        self,
        *,
        ingredient_id: int,
        supplier_id: int,
        supplier_product_id: int,
        packs: int,
        unit_price_pence: int,
        requested_by: str,
        target_delivery_date: date,
    ) -> tuple[int, int, bool, date]:
        """Put a tier C checklist request on a DRAFT. `(po_id, line_id, new, delivery on)`.

        Its own method rather than a `create_draft` with an invented `OrderSuggestion`,
        because there is no suggestion: spec 4.7 says tier C is never calculated, so there
        is no forecast, no par level and no cover window. `packs` is a number a PERSON
        typed, and `requested_by` is recorded on the line so no surface can render it as a
        calculation.

        `DRAFT` and nothing further, like `create_draft` (invariant 1). The delivery date
        is RETURNED rather than assumed: a line joining an existing draft arrives on that
        order's date, and reporting the freshly computed one would be a promise the order
        does not make.
        """
        ...


@runtime_checkable
class ChecklistRepository(Protocol):
    """Tier C: yes/no, never a number (spec 4.7)."""

    def record(
        self,
        ingredient_id: int,
        status: ChecklistStatus,
        responded_at: datetime,
        responded_by: str,
    ) -> int: ...

    def latest_low(self, *, since: datetime) -> list[int]:
        """Ingredient ids whose MOST RECENT answer since `since` was LOW.

        Most recent, not "any LOW": an item answered LOW on Monday and OK on Thursday is
        not low, and reporting it would send somebody to buy what is already there.
        """
        ...

    def latest_by_ingredient(
        self, ingredient_ids: Sequence[int] | None = None
    ) -> dict[int, tuple[ChecklistStatus, datetime]]:
        """(status, responded_at) of the newest answer per ingredient. ONE query.

        Declared because the roster is 59 items and a per-ingredient lookup would be 59
        queries to render one message. Added by the bot agent and adopted here, not
        invented now.
        """
        ...

    def checklist_ingredients(self) -> list[tuple[int, str]]:
        """Every tier C ingredient, by name. The checklist roster.

        Deliberately NOT filtered on `tracking_enabled`: tier C exists *because* these are
        not tracked numerically, so requiring tracking would return an empty checklist.
        """
        ...


@runtime_checkable
class BatchRepository(Protocol):
    """Batches, FIFO and expiry. Spec 4.1."""

    def open_batches(self, ingredient_id: int, *, at: datetime) -> list[BatchSpec]:
        """Batches with stock left, ORDERED BY effective expiry, soonest first.

        The ordering is the contract, not an implementation detail: depletion is FIFO
        by expiry rather than by receipt, because a short-dated delivery must go out
        before older stock with a longer date.
        """
        ...

    def create_batch(
        self,
        ingredient_id: int,
        *,
        qty: Decimal,
        received_at: datetime,
        expires_at: datetime | None,
        unit_cost_pence: Decimal,
        po_line_id: int | None = None,
        note: str | None = None,
        expiry_source: ExpirySource | None = None,
        received_by: str | None = None,
    ) -> int: ...

    def apply_allocations(
        self, allocations: Sequence[DepletionAllocation], *, at: datetime
    ) -> None:
        """Decrement `qty_remaining` for each allocation. Never below zero."""
        ...

    def due_for_expiry(self, *, at: datetime) -> list[BatchSpec]:
        """Batches past their effective expiry with stock left and not yet written off."""
        ...

    def mark_expired(self, losses: Sequence[ExpiryLoss], *, at: datetime) -> int:
        """Write off expired batches. Must be idempotent -- a late sweep must not
        double-count a loss."""
        ...

    def shelf_life(self, ingredient_id: int) -> ShelfLifeSpec | None: ...


@runtime_checkable
class SeasonRepository(Protocol):
    def get(self, season_id: int) -> SeasonSpec | None: ...

    def active_on(self, day: date) -> list[SeasonSpec]: ...

    def for_menu_item(self, menu_item_id: int) -> SeasonSpec | None: ...

    def for_ingredient(self, ingredient_id: int) -> SeasonSpec | None:
        """The season of any variant option that uses this ingredient, if any.

        Spec 4.3: a seasonal syrup must not be ordered on a cover window longer than
        its remaining season.
        """
        ...


@runtime_checkable
class SourcingRepository(Protocol):
    """Multi-supplier sourcing. Spec 4.4."""

    def options_for(self, ingredient_id: int) -> list[SourcingOption]:
        """Every way to buy this ingredient, preferred first."""
        ...

    def terms(self, supplier_id: int) -> SupplierTerms | None: ...

    def all_terms(self) -> list[SupplierTerms]: ...

    def record_emergency_routing(
        self,
        ingredient_id: int,
        *,
        reason: str,
        at: datetime,
        retail_unit_price_pence: int | None = None,
        preferred_unit_price_pence: int | None = None,
        would_be_supplier_id: int | None = None,
        po_line_id: int | None = None,
        qty: Decimal | None = None,
        premium_pence: int | None = None,
    ) -> int:
        """Log a Tesco run. The accumulated log is the argument for fixing the
        ordering cadence (spec 4.4), so it is data, not a note.

        `qty` and `premium_pence` were missing here while the implementation already took
        both. They are not optional detail: unit prices are integer pence, so deriving the
        premium from them rounds twice before multiplying (82.5p against 69.71p over 4 L is
        51p; 83 - 70 over 4 L is 52p). A penny does not matter on one routing and does over
        a quarter of them, and the total IS the point of the table. `premium_pence` stays
        `None` when either price is unknown -- never zero for unknown (invariant 8).
        """
        ...

    def emergency_log(self, *, since: datetime | None = None) -> list[object]:
        """Routings, newest first. `TescoRouting` rows; the report, not a note."""
        ...

    def emergency_summary(
        self, *, since: datetime | None = None
    ) -> tuple[int, int, int, dict[str, tuple[int, int]]]:
        """`(routings, priced_routings, total_premium_pence, name -> (routings, premium))`.

        Declared because the MORNING DIGEST reads it, not only the CLI report: spec 4.4's
        most valuable figure is the accumulated premium, and until now it lived where
        nobody looks at 07:00.

        `priced_routings` is reported alongside the count on purpose. A premium summed over
        9 of 14 routings understates the argument the table exists to make, and the only
        way to see that is to be told (invariant 8).
        """
        ...


@runtime_checkable
class ChannelRepository(Protocol):
    """Deliveroo / Just Eat metrics. Spec 4.6."""

    def upsert_day(self, rows: Iterable[object]) -> tuple[int, int]:
        """Idempotent on (channel, metric_date). Returns (inserted, updated)."""
        ...

    def upsert_item_day(self, rows: Iterable[object]) -> tuple[int, int]: ...

    def range(self, *, since: date, until: date) -> list[object]: ...

    def latest_metric_date(self, channel: object) -> date | None: ...


@runtime_checkable
class AgentLogRepository(Protocol):
    """Spec 9: every agent action logged with inputs, output and the tool called."""

    def log(
        self,
        *,
        run_id: str,
        tool_name: str,
        inputs: dict[str, object],
        outcome: object,
        output: str | None = None,
        purpose: str | None = None,
        refusal_reason: str | None = None,
        proposal_ref: str | None = None,
        model: str | None = None,
    ) -> int: ...

    def for_run(self, run_id: str) -> list[object]: ...

    def refusals(self, *, since: datetime) -> list[object]:
        """Actions the whitelist blocked. The interesting rows."""
        ...
