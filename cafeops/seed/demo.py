"""Demo scenario. Spec 10 phase 0 item 6 and spec 14.

Builds the Flavoured Latte template with three flavours and three sizes on top of
the real ingredients imported from the legacy workbook, then 60 days of synthetic
sales with a weekday pattern, then deliveries and periodic counts.

Deterministic: seeded RNG and no wall-clock reads inside the generator, so two runs
produce identical data and a change in forecast output is a change in the
forecaster rather than in the fixture.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import (
    ComponentRole,
    DrinkTemplate,
    Ingredient,
    IngredientPrice,
    MenuItem,
    Modifier,
    ModifierAction,
    MovementType,
    ParLevel,
    Sale,
    SaleChannel,
    SizeCode,
    SizeProfile,
    StockCount,
    StockMovement,
    Supplier,
    SupplierProduct,
    TemplateComponent,
    Tier,
    Unit,
    VariantAxis,
    VariantOption,
)

TEMPLATE_NAME = "Flavoured Latte"

#: Per-size quantities for the latte template, in each ingredient's stocking unit.
#: Strings so the exact Decimal survives the JSON round-trip.
LATTE_COMPONENTS: tuple[tuple[ComponentRole, str | None, dict[str, str], bool], ...] = (
    (
        ComponentRole.COFFEE,
        "Coffee beans (house blend)",
        {"S": "0.018", "M": "0.018", "XL": "0.027"},
        False,
    ),
    (ComponentRole.MILK, "Whole milk", {"S": "0.12", "M": "0.18", "XL": "0.25"}, True),
    # Empty ingredient: filled by the Flavour axis.
    (ComponentRole.FLAVOUR, None, {"S": "15", "M": "20", "XL": "25"}, False),
    (ComponentRole.SUNDRY, "Napkin", {"S": "1", "M": "1", "XL": "1"}, False),
)

#: Packaging is size-determined: one slot per cup size, quantity only at its size.
LATTE_PACKAGING: tuple[tuple[str, dict[str, str]], ...] = (
    ("8oz paper cup", {"S": "1"}),
    ("8oz cup lid", {"S": "1"}),
    ("12oz paper cup", {"M": "1"}),
    ("12oz cup lid", {"M": "1"}),
    ("16oz paper cup", {"XL": "1"}),
    ("16oz cup lid", {"XL": "1"}),
)

#: Three flavours, as spec 10 asks.
LATTE_FLAVOURS: tuple[tuple[str, str, int], ...] = (
    ("Vanilla", "Vanilla syrup (Monin)", 0),
    ("Caramel", "Caramel syrup (Monin)", 0),
    ("Pistachio", "Pistachio syrup (Monin)", 30),
)

SIZES: tuple[tuple[SizeCode, str, int, int], ...] = (
    (SizeCode.S, "Small", 0, 320),
    (SizeCode.M, "Medium", 1, 360),
    (SizeCode.XL, "Extra large", 2, 410),
)

#: Alt-milk modifiers. SUBSTITUTE targets the MILK role, so one modifier works
#: across every template with a milk slot.
ALT_MILKS: tuple[tuple[str, str, int], ...] = (
    ("Oat milk", "Oat milk (barista)", 40),
    ("Almond milk", "Almond milk (barista)", 40),
    ("Soy milk", "Soy milk (barista)", 40),
)

#: Relative trade by ISO weekday. A Dundee cafe: quiet Monday, busy Saturday.
DOW_WEIGHT: dict[int, float] = {1: 0.75, 2: 0.85, 3: 0.95, 4: 1.05, 5: 1.25, 6: 1.45, 7: 0.90}
BASE_RECEIPTS_PER_DAY = 32
ALT_MILK_SHARE = 0.22
OPEN_HOUR, CLOSE_HOUR = 8, 17
HOUR_WEIGHTS: dict[int, float] = {
    8: 1.6,
    9: 1.8,
    10: 1.5,
    11: 1.2,
    12: 1.3,
    13: 1.2,
    14: 0.9,
    15: 0.7,
    16: 0.5,
}

#: Injected drift for synthetic counts, chosen so spec 5.2's gate has ingredients
#: on BOTH sides of it: several under 10% (should become eligible after two clean
#: counts), one in the 10-15% tuning band, one above 15% that must be refused.
#: Agent C therefore gets a failing case for free instead of inventing one.
COUNT_DRIFT: dict[str, Decimal] = {
    "Whole milk": Decimal("0.04"),
    "Coffee beans (house blend)": Decimal("0.06"),
    "12oz paper cup": Decimal("0.02"),
    "12oz cup lid": Decimal("0.03"),
    "Napkin": Decimal("0.12"),  # tuning band
    "16oz paper cup": Decimal("0.19"),  # must be refused auto-ordering
}
DEFAULT_DRIFT = Decimal("0.05")

SUPPLIER_DEFS: tuple[dict[str, object], ...] = (
    {
        "name": "Tesco",
        "lead_time_days": 0,
        "delivery_weekdays": [],  # walk-in: any day
        "min_order_pence": 0,
        "order_channel": "MANUAL",
        "contact": "Walk-in, Dundee",
    },
    {
        "name": "CakeSmiths",
        "lead_time_days": 2,
        "delivery_weekdays": [2, 5],  # PLACEHOLDER -- confirm with supplier
        "min_order_pence": 5000,
        "order_channel": "EMAIL",
        "contact": "orders@cakesmiths.example",
    },
    {
        "name": "Cups Direct",
        "lead_time_days": 3,
        "delivery_weekdays": [1, 2, 3, 4, 5],  # PLACEHOLDER
        "min_order_pence": 3000,
        "order_channel": "BROWSER_AGENT",
        "contact": "https://www.cupsdirect.example",
    },
)
SUPPLIER_BY_CATEGORY: dict[str, str] = {
    "Packaging": "Cups Direct",
    "Sundries": "Cups Direct",
    "Cake": "CakeSmiths",
    "Cake (CakeSmiths)": "CakeSmiths",
}
DEFAULT_SUPPLIER = "Tesco"


@dataclass
class DemoReport:
    template_created: bool = False
    menu_items: int = 0
    modifiers: int = 0
    suppliers: int = 0
    supplier_products: int = 0
    par_levels: int = 0
    days: int = 0
    receipts: int = 0
    sale_lines: int = 0
    modifiers_applied: int = 0
    opening_counts: int = 0
    deliveries: int = 0
    counts: int = 0
    first_day: date | None = None
    last_day: date | None = None
    warnings: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        span = (
            f"{self.first_day.isoformat()}..{self.last_day.isoformat()}"
            if self.first_day and self.last_day
            else "-"
        )
        out = [
            f"template {TEMPLATE_NAME!r}: "
            f"{'created' if self.template_created else 'already present'}, "
            f"{self.menu_items} sellable items, {self.modifiers} modifiers",
            f"suppliers: {self.suppliers}, products: {self.supplier_products}, "
            f"par levels: {self.par_levels}",
            f"sales: {self.days} days ({span}), {self.receipts} receipts, "
            f"{self.sale_lines} lines, {self.modifiers_applied} with an alt milk",
            f"ledger: {self.opening_counts} opening counts, {self.deliveries} deliveries, "
            f"{self.counts} periodic counts",
        ]
        out.extend(f"[yellow]warning[/yellow] {w}" for w in self.warnings)
        return out


def seed_demo(session: Session, *, days: int = 60, seed: int = 20260919) -> DemoReport:
    report = DemoReport()
    _suppliers_and_pars(session, report)
    template = _build_latte_template(session, report)
    _build_modifiers(session, report)
    session.flush()
    if template is None:
        report.warnings.append("latte template not built -- no sales generated")
        return report
    _generate_sales(session, template, report, days=days, seed=seed)
    return report


# --------------------------------------------------------------------------
# Composition
# --------------------------------------------------------------------------


def _ing(session: Session, name: str) -> Ingredient | None:
    return session.scalar(select(Ingredient).where(Ingredient.name == name))


def _build_latte_template(session: Session, report: DemoReport) -> DrinkTemplate | None:
    existing = session.scalar(select(DrinkTemplate).where(DrinkTemplate.name == TEMPLATE_NAME))
    if existing is not None:
        return existing

    # Effective from well before the demo window, so every synthetic sale resolves
    # against a recipe that was already in force when it happened.
    effective_from = datetime(2025, 11, 1, tzinfo=UTC)

    template = DrinkTemplate(
        name=TEMPLATE_NAME,
        category="Latte / flavoured latte",
        description=(
            "One pattern behind every flavoured latte. Adding a flavour is one "
            "variant_option row and creates three sellable items."
        ),
    )
    session.add(template)
    session.flush()
    report.template_created = True

    for code, label, order, _price in SIZES:
        session.add(SizeProfile(template_id=template.id, code=code, label=label, sort_order=order))

    missing: list[str] = []
    for role, ingredient_name, qty_by_size, substitutable in LATTE_COMPONENTS:
        ingredient_id = None
        if ingredient_name is not None:
            ingredient = _ing(session, ingredient_name)
            if ingredient is None:
                missing.append(ingredient_name)
                continue
            ingredient_id = ingredient.id
        session.add(
            TemplateComponent(
                template_id=template.id,
                role=role,
                ingredient_id=ingredient_id,
                qty_by_size=qty_by_size,
                is_substitutable=substitutable,
                is_required=True,
                effective_from=effective_from,
            )
        )

    for ingredient_name, qty_by_size in LATTE_PACKAGING:
        ingredient = _ing(session, ingredient_name)
        if ingredient is None:
            missing.append(ingredient_name)
            continue
        session.add(
            TemplateComponent(
                template_id=template.id,
                role=ComponentRole.PACKAGING,
                ingredient_id=ingredient.id,
                qty_by_size=qty_by_size,
                is_substitutable=False,
                # Not required: an 8oz cup has no quantity at size XL, and
                # demanding one would warn on every large latte.
                is_required=False,
                effective_from=effective_from,
            )
        )

    axis = VariantAxis(
        template_id=template.id, name="Flavour", role=ComponentRole.FLAVOUR, is_required=True
    )
    session.add(axis)
    session.flush()

    options: list[VariantOption] = []
    for label, syrup_name, delta in LATTE_FLAVOURS:
        syrup = _ing(session, syrup_name)
        if syrup is None:
            missing.append(syrup_name)
            continue
        option = VariantOption(
            axis_id=axis.id,
            name=label,
            ingredient_id=syrup.id,
            qty_by_size=None,  # inherits the FLAVOUR slot's per-size quantity
            price_delta_pence=delta,
            effective_from=effective_from,
        )
        session.add(option)
        options.append(option)
    session.flush()

    if missing:
        report.warnings.append(
            f"{len(missing)} template ingredient(s) not found in the workbook, so those "
            f"slots are absent: {sorted(set(missing))}"
        )

    # --- sellable leaves: 3 flavours x 3 sizes = 9 items ------------------
    for option in options:
        for code, _label, _order, price in SIZES:
            name = f"{option.name} Latte"
            existing_item = session.scalar(
                select(MenuItem).where(MenuItem.name == name, MenuItem.size_code == code)
            )
            if existing_item is not None:
                # The legacy import already created this name; attach it to the
                # template rather than duplicating it.
                existing_item.template_id = template.id
                existing_item.selected_options = {str(axis.id): option.id}
                existing_item.manual_recipe = False
                continue
            session.add(
                MenuItem(
                    name=name,
                    size_code=code,
                    category="Latte / flavoured latte",
                    template_id=template.id,
                    selected_options={str(axis.id): option.id},
                    price_pence=price + option.price_delta_pence,
                    active=True,
                    manual_recipe=False,
                )
            )
            report.menu_items += 1
    session.flush()
    # Report the total attached, not just what this run created -- the legacy
    # import may already have created some of these names.
    report.menu_items = int(
        session.scalar(
            select(func.count(MenuItem.id)).where(MenuItem.template_id == template.id)
        )
        or 0
    )
    return template


def _build_modifiers(session: Session, report: DemoReport) -> None:
    for name, ingredient_name, price in ALT_MILKS:
        if session.scalar(select(Modifier).where(Modifier.name == name)) is not None:
            continue
        ingredient = _ing(session, ingredient_name)
        if ingredient is None:
            report.warnings.append(f"modifier {name!r}: {ingredient_name!r} not found")
            continue
        session.add(
            Modifier(
                name=name,
                action=ModifierAction.SUBSTITUTE,
                target_role=ComponentRole.MILK,
                ingredient_id=ingredient.id,
                price_pence=price,
            )
        )
        report.modifiers += 1


def _suppliers_and_pars(session: Session, report: DemoReport) -> None:
    from cafeops.db.models.enums import OrderChannel

    suppliers: dict[str, Supplier] = {}
    for spec in SUPPLIER_DEFS:
        name = str(spec["name"])
        existing = session.scalar(select(Supplier).where(Supplier.name == name))
        if existing is None:
            existing = Supplier(
                name=name,
                lead_time_days=int(spec["lead_time_days"]),  # type: ignore[call-overload]
                delivery_weekdays=spec["delivery_weekdays"],
                min_order_pence=int(spec["min_order_pence"]),  # type: ignore[call-overload]
                order_channel=OrderChannel(str(spec["order_channel"])),
                contact=str(spec["contact"]),
                order_url=(
                    str(spec["contact"]) if spec["order_channel"] == "BROWSER_AGENT" else None
                ),
                agent_instructions=(
                    "Log in with the saved account, add each SKU to the basket, then "
                    "STOP at the basket. Do not complete checkout."
                    if spec["order_channel"] == "BROWSER_AGENT"
                    else None
                ),
                channel_config={},
            )
            session.add(existing)
            report.suppliers += 1
        suppliers[name] = existing
    session.flush()
    report.warnings.append(
        "CakeSmiths and Cups Direct lead times, delivery weekdays and minimum orders "
        "are PLACEHOLDERS -- confirm before trusting any order size."
    )

    # Supplier products from the imported price rows: one buyable pack per
    # ingredient, at the supplier its category implies.
    for ingredient in session.scalars(select(Ingredient)):
        price = session.scalar(
            select(IngredientPrice)
            .where(
                IngredientPrice.ingredient_id == ingredient.id,
                IngredientPrice.effective_to.is_(None),
            )
            .limit(1)
        )
        if price is None or price.pack_size <= 0:
            continue
        supplier = suppliers[
            SUPPLIER_BY_CATEGORY.get((ingredient.category or "").strip(), DEFAULT_SUPPLIER)
        ]
        existing_product = session.scalar(
            select(SupplierProduct).where(
                SupplierProduct.supplier_id == supplier.id,
                SupplierProduct.ingredient_id == ingredient.id,
                SupplierProduct.sku == "",
            )
        )
        if existing_product is None:
            session.add(
                SupplierProduct(
                    supplier_id=supplier.id,
                    ingredient_id=ingredient.id,
                    sku="",
                    pack_size=price.pack_size,
                    pack_unit=price.pack_unit,
                    price_pence=price.pack_cost_pence,
                    is_preferred=True,
                )
            )
            report.supplier_products += 1

        if not ingredient.tracking_enabled:
            continue
        if session.scalar(select(ParLevel).where(ParLevel.ingredient_id == ingredient.id)):
            continue
        session.add(
            ParLevel(
                ingredient_id=ingredient.id,
                safety_days=Decimal("2") if ingredient.tier is Tier.A else Decimal("3"),
                min_qty=price.pack_size * Decimal("0.25"),
                max_qty=price.pack_size * Decimal("3"),
                auto_order_enabled=False,
                auto_order_reason="seeded; auto-order must be earned via the drift gate",
            )
        )
        report.par_levels += 1
    session.flush()


# --------------------------------------------------------------------------
# Sales
# --------------------------------------------------------------------------


def _generate_sales(
    session: Session,
    template: DrinkTemplate,
    report: DemoReport,
    *,
    days: int,
    seed: int,
) -> None:
    rng = random.Random(seed)
    tz = settings.tz
    end_day = datetime.now(tz).date() - timedelta(days=1)
    first_day = end_day - timedelta(days=days - 1)
    report.days, report.first_day, report.last_day = days, first_day, end_day

    items = list(
        session.scalars(
            select(MenuItem).where(MenuItem.template_id == template.id, MenuItem.active.is_(True))
        )
    )
    if not items:
        report.warnings.append("template has no sellable items -- no sales generated")
        return

    # Medium is the default pour and outsells the extremes.
    weights = [1.6 if i.size_code is SizeCode.M else 1.0 for i in items]
    modifiers = list(session.scalars(select(Modifier)))

    report.opening_counts = _opening_counts(
        session, at=datetime.combine(first_day, time(hour=7), tzinfo=tz).astimezone(UTC)
    )

    line_seq = int(session.scalar(select(func.count(Sale.id))) or 0)
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        weight = DOW_WEIGHT[day.isoweekday()]
        receipts_today = max(1, round(BASE_RECEIPTS_PER_DAY * weight * rng.uniform(0.85, 1.15)))

        for _ in range(receipts_today):
            report.receipts += 1
            receipt_id = f"DEMO-R{report.receipts:06d}"
            sold_at = _random_instant(rng, day, tz)
            for _line in range(rng.choices([1, 2, 3], weights=[0.62, 0.30, 0.08])[0]):
                item = rng.choices(items, weights=weights, k=1)[0]
                line_seq += 1
                applied: list[int] = []
                if modifiers and rng.random() < ALT_MILK_SHARE:
                    applied.append(rng.choice(modifiers).id)
                    report.modifiers_applied += 1
                session.add(
                    Sale(
                        lightspeed_receipt_id=receipt_id,
                        lightspeed_line_id=f"DEMO-L{line_seq:07d}",
                        menu_item_id=item.id,
                        qty=Decimal("1"),
                        gross_pence=item.price_pence,
                        sold_at=sold_at,
                        channel=SaleChannel.EPOS,
                        applied_modifiers=applied,
                    )
                )
                report.sale_lines += 1
        session.flush()


def _opening_counts(session: Session, *, at: datetime) -> int:
    """An opening physical count so theoretical on-hand has an anchor.

    Without it every figure is a bare movement sum -- a large negative number --
    and OnHand.has_count_basis would correctly report the whole system as
    unanchored, which teaches nothing.
    """
    written = 0
    for ingredient in session.scalars(
        select(Ingredient).where(Ingredient.tracking_enabled.is_(True))
    ):
        if session.scalar(
            select(func.count(StockCount.id)).where(StockCount.ingredient_id == ingredient.id)
        ):
            continue
        qty = Decimal("400") if ingredient.unit is Unit.EACH else Decimal("40")
        session.add(
            StockCount(
                ingredient_id=ingredient.id,
                counted_qty=qty,
                counted_at=at,
                counted_by="demo-seed",
                note="synthetic opening count",
            )
        )
        written += 1
    return written


def _random_instant(rng: random.Random, day: date, tz: object) -> datetime:
    hours = [h for h in range(OPEN_HOUR, CLOSE_HOUR) if h in HOUR_WEIGHTS]
    hour = rng.choices(hours, weights=[HOUR_WEIGHTS[h] for h in hours], k=1)[0]
    local = datetime.combine(
        day,
        time(hour=hour, minute=rng.randrange(60), second=rng.randrange(60)),
        tzinfo=tz,  # type: ignore[arg-type]
    )
    return local.astimezone(UTC)


# --------------------------------------------------------------------------
# Restocking and periodic counts -- must run AFTER expansion
# --------------------------------------------------------------------------

RESTOCK_TRIGGER_DAYS = Decimal("3")
RESTOCK_TARGET_DAYS = Decimal("8")
COUNT_EVERY_DAYS = 7


def simulate_restocking(session: Session, report: DemoReport | None = None) -> tuple[int, int]:
    """Walk the demo window writing DELIVERY movements and periodic counts.

    Reads the SALE movements, so it must run after expansion. Without it the ledger
    only ever goes down and every on-hand figure is a large negative number.

    Returns (deliveries, counts).
    """
    tz = settings.tz
    bounds = session.execute(select(func.min(Sale.sold_at), func.max(Sale.sold_at))).first()
    if bounds is None or bounds[0] is None:
        return (0, 0)
    first_day = bounds[0].astimezone(tz).date()
    last_day = bounds[1].astimezone(tz).date()
    span = (last_day - first_day).days + 1

    deliveries = counts = 0
    for ingredient in session.scalars(
        select(Ingredient).where(Ingredient.tracking_enabled.is_(True))
    ):
        pack = session.scalar(
            select(SupplierProduct.pack_size)
            .where(SupplierProduct.ingredient_id == ingredient.id)
            .limit(1)
        )
        if pack is None or pack <= 0:
            continue
        opening = session.scalar(
            select(StockCount.counted_qty)
            .where(StockCount.ingredient_id == ingredient.id)
            .order_by(StockCount.counted_at)
            .limit(1)
        )
        running = opening if opening is not None else Decimal("0")

        consumption = _daily_sale_consumption(session, ingredient.id, first_day, last_day, tz)
        total = sum(consumption.values(), Decimal("0"))
        if total <= 0:
            continue
        avg_daily = total / Decimal(span)
        drift = COUNT_DRIFT.get(ingredient.name, DEFAULT_DRIFT)

        for offset in range(span):
            day = first_day + timedelta(days=offset)
            if running < avg_daily * RESTOCK_TRIGGER_DAYS:
                packs = max(1, math.ceil((avg_daily * RESTOCK_TARGET_DAYS - running) / pack))
                delivered = Decimal(packs) * pack
                session.add(
                    StockMovement(
                        ingredient_id=ingredient.id,
                        type=MovementType.DELIVERY,
                        qty=delivered,
                        occurred_at=datetime.combine(
                            day, time(hour=7, minute=30), tzinfo=tz
                        ).astimezone(UTC),
                        ref_type="demo_restock",
                        note=f"synthetic delivery, {packs} pack(s)",
                    )
                )
                running += delivered
                deliveries += 1

            running -= consumption.get(day, Decimal("0"))

            if offset > 0 and offset % COUNT_EVERY_DAYS == 0 and ingredient.tier is Tier.A:
                counted = (running * (Decimal("1") - drift)).quantize(Decimal("0.001"))
                if counted < 0:
                    counted = Decimal("0")
                session.add(
                    StockCount(
                        ingredient_id=ingredient.id,
                        counted_qty=counted,
                        counted_at=datetime.combine(day, time(hour=21), tzinfo=tz).astimezone(UTC),
                        counted_by="demo-seed",
                        note=f"synthetic count, injected drift {drift:.0%}",
                    )
                )
                counts += 1
                running = counted  # a count is the source of truth
        session.flush()

    if report is not None:
        report.deliveries, report.counts = deliveries, counts
    return (deliveries, counts)


def _daily_sale_consumption(
    session: Session, ingredient_id: int, first_day: date, last_day: date, tz: object
) -> dict[date, Decimal]:
    start = datetime.combine(first_day, time.min, tzinfo=tz).astimezone(UTC)  # type: ignore[arg-type]
    end = datetime.combine(last_day, time.max, tzinfo=tz).astimezone(UTC)  # type: ignore[arg-type]
    rows = session.execute(
        select(StockMovement.occurred_at, StockMovement.qty).where(
            StockMovement.ingredient_id == ingredient_id,
            StockMovement.type == MovementType.SALE,
            StockMovement.occurred_at >= start,
            StockMovement.occurred_at <= end,
        )
    ).all()
    out: dict[date, Decimal] = {}
    for occurred_at, qty in rows:
        day = occurred_at.astimezone(tz).date()  # type: ignore[arg-type]
        out[day] = out.get(day, Decimal("0")) + (-qty)
    return out
