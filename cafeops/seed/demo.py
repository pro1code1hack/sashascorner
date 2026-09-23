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
    OrderChannel,
    ParLevel,
    Sale,
    SaleChannel,
    Season,
    SizeCode,
    SizeProfile,
    StockBatch,
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
from cafeops.seed.suppliers import (
    ALTERNATE_SOURCES,
    CATEGORY_TO_SUPPLIER,
    DEFAULT_SUPPLIER,
    SUPPLIERS,
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

# Suppliers, category routing and alternate sources live in seed/suppliers.py.


@dataclass
class DemoReport:
    template_created: bool = False
    menu_items: int = 0
    modifiers: int = 0
    suppliers: int = 0
    supplier_products: int = 0
    alternate_sources: int = 0
    batches: int = 0
    seasons: int = 0
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
            f"suppliers: {self.suppliers}, products: {self.supplier_products} "
            f"(+{self.alternate_sources} alternate sources), par levels: {self.par_levels}",
            f"batches: {self.batches}, seasons: {self.seasons}",
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
    seed_seasons(session, report)
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
        session.scalar(select(func.count(MenuItem.id)).where(MenuItem.template_id == template.id))
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
    """Create the eight real suppliers, their products, alternates and par levels."""
    suppliers: dict[str, Supplier] = {}
    placeholder_names: list[str] = []

    for spec in SUPPLIERS:
        existing = session.scalar(select(Supplier).where(Supplier.name == spec.name))
        if existing is None:
            existing = Supplier(name=spec.name)
            session.add(existing)
            report.suppliers += 1
        existing.lead_time_days = spec.lead_time_days
        existing.delivery_weekdays = list(spec.delivery_weekdays)
        existing.min_order_pence = spec.min_order_pence
        existing.order_channel = spec.order_channel
        existing.contact = spec.contact
        existing.cutoff_time = spec.cutoff_time
        existing.delivery_fee_pence = spec.delivery_fee_pence
        existing.free_delivery_threshold_pence = spec.free_delivery_threshold_pence
        existing.terms_are_placeholders = spec.terms_are_placeholders
        existing.order_url = spec.order_url
        existing.agent_instructions = (
            "Log in with the saved account, add each SKU to the basket, then STOP at "
            "the basket. Do not complete checkout."
            if spec.order_channel is OrderChannel.PORTAL
            else None
        )
        existing.channel_config = {}
        suppliers[spec.name] = existing
        if spec.terms_are_placeholders:
            placeholder_names.append(spec.name)
    session.flush()

    report.warnings.append(
        f"{len(placeholder_names)} supplier(s) have INVENTED terms -- lead time, "
        f"delivery days, cutoff, minimum, threshold: {', '.join(placeholder_names)}. "
        "Every order built from them is only as good as those guesses."
    )
    report.warnings.append(
        "'Nataly (custom)' is unspecified (spec 15 q5): what it supplies and through "
        "what channel is unknown, so it is modelled MANUAL with a 5-day lead."
    )

    # --- primary supplier product per ingredient, from the imported price -----
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
            CATEGORY_TO_SUPPLIER.get((ingredient.category or "").strip(), DEFAULT_SUPPLIER)
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
                    moq_packs=1,
                )
            )
            report.supplier_products += 1
        # Attribute the imported price to the supplier now that we have one.
        if price.supplier_id is None:
            price.supplier_id = supplier.id
    session.flush()

    # --- alternate sources, so sourcing has a real decision to make ----------
    for ing_name, sup_name, pack_size_text, pack_pence in ALTERNATE_SOURCES:
        ingredient = _ing(session, ing_name)
        supplier = suppliers.get(sup_name)
        if ingredient is None or supplier is None:
            report.warnings.append(
                f"alternate source skipped: {ing_name!r} at {sup_name!r} not found"
            )
            continue
        sku = f"ALT-{sup_name[:3].upper()}"
        if session.scalar(
            select(SupplierProduct).where(
                SupplierProduct.supplier_id == supplier.id,
                SupplierProduct.ingredient_id == ingredient.id,
                SupplierProduct.sku == sku,
            )
        ):
            continue
        session.add(
            SupplierProduct(
                supplier_id=supplier.id,
                ingredient_id=ingredient.id,
                sku=sku,
                pack_size=Decimal(pack_size_text),
                pack_unit=ingredient.unit,
                price_pence=pack_pence,
                is_preferred=False,
                moq_packs=1,
            )
        )
        report.alternate_sources += 1
    session.flush()

    # --- par levels ----------------------------------------------------------
    for ingredient in session.scalars(
        select(Ingredient).where(Ingredient.tracking_enabled.is_(True))
    ):
        if session.scalar(select(ParLevel).where(ParLevel.ingredient_id == ingredient.id)):
            continue
        product = session.scalar(
            select(SupplierProduct)
            .where(
                SupplierProduct.ingredient_id == ingredient.id,
                SupplierProduct.is_preferred.is_(True),
            )
            .limit(1)
        )
        pack = product.pack_size if product is not None else Decimal("1")
        session.add(
            ParLevel(
                ingredient_id=ingredient.id,
                safety_days=Decimal("2") if ingredient.tier is Tier.A else Decimal("3"),
                min_qty=pack * Decimal("0.25"),
                max_qty=pack * Decimal("3"),
                auto_order_enabled=False,
                auto_order_reason="seeded; auto-order must be earned via the drift gate",
            )
        )
        report.par_levels += 1
    session.flush()


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
        qty = OPENING_COUNT_BY_UNIT[ingredient.unit]
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

#: Plausible opening stock per stocking unit. A flat number is wrong here: 40 of
#: something measured in ML is 40 millilitres of syrup, which is not a bottle --
#: and an opening count below the par floor made the order builder briefly want to
#: buy one bottle of each of 23 syrups that had never sold.
OPENING_COUNT_BY_UNIT: dict[Unit, Decimal] = {
    Unit.EACH: Decimal("400"),
    Unit.L: Decimal("40"),
    Unit.KG: Decimal("5"),
    Unit.ML: Decimal("2000"),  # ~2 L of syrup
    Unit.G: Decimal("2000"),  # ~2 kg
}

RESTOCK_TRIGGER_DAYS = Decimal("3")
RESTOCK_TARGET_DAYS = Decimal("8")
COUNT_EVERY_DAYS = 7

#: A deliberate supply disruption, so spec 4.4's emergency path has a real shortfall to
#: find. Without this, the restock cadence below (trigger at 3 days' cover, top up to 8)
#: is MORE generous than any real supplier's delivery gap -- Brakes' M/W/F round is at
#: most a 2-day gap from a Monday order -- so on-hand for a moving ingredient never
#: drops far enough to run out before the next scheduled delivery, and
#: `sourcing.route_to_retail` (correctly) never has a case to route.
#:
#: This models one real event instead: Brakes' round is disrupted for a few days
#: (breakdown, missed cutoff -- does not matter which) and only manages a small rushed
#: top-up each day rather than its usual full delivery, so `Whole milk` genuinely runs
#: down under the Brakes gap by the next order run. `reduced_packs` (not zero) is
#: deliberate: a full stockout would be the same finding with a less realistic shape --
#: a real café is usually left with a thin trickle rather than nothing at all, and it
#: keeps every figure in the demo non-negative. Keyed by ingredient NAME rather than id:
#: this file runs before ids are known to any caller and the legacy import assigns them.
#:
#: TWO windows for Whole milk, not one: `emergency-report`'s whole point (spec 4.4) is
#: a PATTERN over time, not a single incident -- a report that can only ever show one
#: row is not the report the spec describes. Both land the Monday before a scheduled
#: Brakes delivery, one bad fortnight apart, so the report has a real trend to show.
RESTOCK_DISRUPTIONS: dict[str, tuple[tuple[date, date, int], ...]] = {
    # (disruption start, disruption end, packs delivered per trigger instead of the
    # full target -- a rushed top-up, not the usual restock).
    "Whole milk": (
        (date(2026, 8, 20), date(2026, 8, 23), 2),
        (date(2026, 9, 15), date(2026, 9, 20), 2),
    ),
}


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
        windows = RESTOCK_DISRUPTIONS.get(ingredient.name, ())

        for offset in range(span):
            day = first_day + timedelta(days=offset)
            disrupted_window = next((w for w in windows if w[0] <= day <= w[1]), None)
            if running < avg_daily * RESTOCK_TRIGGER_DAYS:
                if disrupted_window is not None:
                    packs = disrupted_window[2]
                    note = f"DISRUPTED delivery, {packs} pack(s) rushed (not the full top-up)"
                else:
                    packs = max(1, math.ceil((avg_daily * RESTOCK_TARGET_DAYS - running) / pack))
                    note = f"synthetic delivery, {packs} pack(s)"
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
                        note=note,
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


def size_par_levels(
    session: Session,
    *,
    assumed_cadence_days: int = 7,
    headroom: Decimal = Decimal("1.5"),
) -> tuple[int, int]:
    """Re-size par levels from OBSERVED consumption. Run after expansion.

    The seed originally set `max_qty = 3 * pack_size` for everything, which ignores
    throughput entirely. On this data that put 8 of 17 moving ingredients' ceilings
    BELOW a single cover window of demand -- whole milk's 10.2 L is about 1.4 days of
    trade. The forecast would ask for 4 packs, the clamp would cut it to 0, and the
    system would correctly order nothing while warning that a stockout was
    guaranteed. In other words the par ceiling, not the forecast, was sizing the
    orders, and it was sizing them to fail.

    New shape, per ingredient with measured consumption:

        cover_days = supplier lead time + reorder cadence + safety_days
        min_qty    = safety_days * avg_daily        (the reorder floor)
        max_qty    = min_qty + cover_days * avg_daily * headroom, rounded up to
                     whole packs and never below one pack

    Returns (resized, skipped_no_consumption).
    """
    tz = settings.tz
    bounds = session.execute(select(func.min(Sale.sold_at), func.max(Sale.sold_at))).first()
    if bounds is None or bounds[0] is None:
        return (0, 0)
    first_day = bounds[0].astimezone(tz).date()
    last_day = bounds[1].astimezone(tz).date()
    span = Decimal((last_day - first_day).days + 1)

    resized = skipped = 0
    for par in session.scalars(select(ParLevel)):
        ingredient = session.get(Ingredient, par.ingredient_id)
        if ingredient is None:
            continue
        consumption = _daily_sale_consumption(session, ingredient.id, first_day, last_day, tz)
        total = sum(consumption.values(), Decimal("0"))
        if total <= 0:
            # No measured movement: leave the seeded pack-multiple ceiling alone.
            # Inventing a throughput-based par for something that never sells would
            # be fabricating demand.
            skipped += 1
            continue
        avg_daily = total / span

        product = session.scalar(
            select(SupplierProduct).where(SupplierProduct.ingredient_id == ingredient.id).limit(1)
        )
        pack = product.pack_size if product is not None and product.pack_size > 0 else Decimal("1")
        lead = 0
        if product is not None:
            supplier = session.get(Supplier, product.supplier_id)
            lead = supplier.lead_time_days if supplier is not None else 0

        cover_days = Decimal(lead) + Decimal(assumed_cadence_days) + par.safety_days
        cover_demand = cover_days * avg_daily
        min_qty = (par.safety_days * avg_daily).quantize(Decimal("0.001"))
        # The ceiling has to accommodate the worst legitimate case: holding almost a
        # full cover window and then buying one whole pack. Sizing it to the cover
        # window alone blocks that purchase whenever the pack is large relative to
        # throughput -- a 500-cup pack against 21 cups/day is 24 days of stock, so a
        # 12-day ceiling can never be satisfied and every order is clamped to zero.
        # Hence `+ pack`: the ceiling stops runaway stock without forbidding the
        # smallest purchase the supplier actually sells.
        target = cover_demand * headroom + pack
        par.min_qty = min_qty
        par.max_qty = target.quantize(Decimal("0.001"))
        resized += 1

    session.flush()
    return (resized, skipped)


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


# --------------------------------------------------------------------------
# Seasons and batches
# --------------------------------------------------------------------------

#: A recurring spring season so the seasonal paths (spec 4.3) have real data: a
#: forecast that must exclude out-of-season history, and an order that must be capped
#: at remaining season days rather than the full cover window.
SEASONS: tuple[tuple[str, date, date, bool], ...] = (
    ("Spring seasonal drinks", date(2026, 3, 1), date(2026, 5, 31), True),
    ("Pumpkin season", date(2026, 9, 15), date(2026, 11, 30), True),
)

#: Which seeded flavour belongs to which season. Pistachio is spring-only (spec 4.3).
SEASONAL_OPTIONS: dict[str, str] = {"Pistachio": "Spring seasonal drinks"}


def seed_seasons(session: Session, report: DemoReport) -> None:
    """Create seasons and attach the seasonal flavour option."""
    by_name: dict[str, Season] = {}
    for name, starts, ends, recurring in SEASONS:
        existing = session.scalar(select(Season).where(Season.name == name))
        if existing is None:
            existing = Season(
                name=name,
                starts_on=starts,
                ends_on=ends,
                is_recurring_annually=recurring,
                note="seeded demo season",
            )
            session.add(existing)
            report.seasons += 1
        by_name[name] = existing
    session.flush()

    for option_name, season_name in SEASONAL_OPTIONS.items():
        season = by_name.get(season_name)
        option = session.scalar(select(VariantOption).where(VariantOption.name == option_name))
        if season is None or option is None:
            continue
        option.season_id = season.id
    session.flush()


def seed_batches(session: Session, report: DemoReport) -> None:
    """Create stock_batch rows for existing stock. Spec 4.1, spec 16.

    Two sources, both needed for the demo to be honest:

    1. **Opening counts** -- stock that was already there. Received at the count
       instant, expiring per the ingredient's shelf life. A count is not a purchase,
       so these batches have no `po_line_id`, which is exactly why that column is
       nullable.
    2. **DELIVERY movements** -- each synthetic restock becomes a batch, so FIFO has
       several lots per ingredient with different dates to choose between.

    Runs after restocking, because it reads the delivery ledger.
    """
    shelf_by_ing: dict[int, Ingredient] = {i.id: i for i in session.scalars(select(Ingredient))}

    def expiry_for(ingredient: Ingredient, received_at: datetime) -> datetime | None:
        if ingredient.shelf_life_days is None:
            return None
        return received_at + timedelta(days=ingredient.shelf_life_days)

    # --- from opening counts -------------------------------------------------
    first_counts: dict[int, StockCount] = {}
    for count in session.scalars(select(StockCount).order_by(StockCount.counted_at)):
        first_counts.setdefault(count.ingredient_id, count)

    for ingredient_id, count in first_counts.items():
        ingredient = shelf_by_ing.get(ingredient_id)
        if ingredient is None or count.counted_qty <= 0:
            continue
        if session.scalar(
            select(func.count(StockBatch.id)).where(
                StockBatch.ingredient_id == ingredient_id,
                StockBatch.po_line_id.is_(None),
                StockBatch.note == "opening stock",
            )
        ):
            continue
        price = ingredient.current_cost_pence_per_unit or Decimal("0")
        session.add(
            StockBatch(
                ingredient_id=ingredient_id,
                qty_received=count.counted_qty,
                qty_remaining=count.counted_qty,
                received_at=count.counted_at,
                expires_at=expiry_for(ingredient, count.counted_at),
                unit_cost_pence=price,
                note="opening stock",
            )
        )
        report.batches += 1
    session.flush()

    # --- from DELIVERY movements --------------------------------------------
    deliveries = session.scalars(
        select(StockMovement)
        .where(StockMovement.type == MovementType.DELIVERY)
        .order_by(StockMovement.occurred_at)
    )
    for movement in deliveries:
        ingredient = shelf_by_ing.get(movement.ingredient_id)
        if ingredient is None or movement.qty <= 0:
            continue
        if movement.batch_id is not None:
            continue
        price = ingredient.current_cost_pence_per_unit or Decimal("0")
        batch = StockBatch(
            ingredient_id=movement.ingredient_id,
            qty_received=movement.qty,
            qty_remaining=movement.qty,
            received_at=movement.occurred_at,
            expires_at=expiry_for(ingredient, movement.occurred_at),
            unit_cost_pence=price,
            note="synthetic delivery",
        )
        session.add(batch)
        session.flush()
        # Link the ledger row to the batch it created, so provenance is walkable.
        movement.batch_id = batch.id
        report.batches += 1
    session.flush()
