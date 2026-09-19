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

from cafeops.domain.types import (
    ChecklistStatus,
    ConsumptionPoint,
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
    SupplierSpec,
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

    def modifiers(self, modifier_ids: Sequence[int]) -> list[ModifierSpec]: ...

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
    def get(self, menu_item_id: int) -> tuple[Decimal, PriceSource, bool] | None:
        """(cost_pence, source, has_missing_cost) from the materialised cache."""
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

    def append_movements(self, movements: Iterable[MovementSpec]) -> int: ...

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


@runtime_checkable
class ParLevelRepository(Protocol):
    def get(self, ingredient_id: int) -> ParSpec | None: ...

    def set_auto_order(
        self, ingredient_id: int, enabled: bool, *, reason: str, at: datetime
    ) -> None:
        """Only domain/tiers.py drives this. Never a manual shortcut (invariant 2)."""
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


@runtime_checkable
class ChecklistRepository(Protocol):
    def record(
        self,
        ingredient_id: int,
        status: ChecklistStatus,
        responded_at: datetime,
        responded_by: str,
    ) -> int: ...

    def latest_low(self, *, since: datetime) -> list[int]: ...
