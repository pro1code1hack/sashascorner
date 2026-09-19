"""Import the legacy finance workbook. Spec 6.

`sashas_corner_finance__LEGACY_.xlsx` is the only existing source of costs and
recipes. Import it once, then it is dead.

Three passes:

1. Ingredients  -> ingredient + ingredient_price, with `source` from the
   "Supplier / notes" column. ESTIMATE stays flagged forever (invariant 6) --
   it is why the legacy 46% COGS figure is untrustworthy.
2. Flat recipes -> legacy_staged_recipe. Straight port, no interpretation.
3. Pattern detection -> PROPOSALS only. A human confirms before composition is
   written.

Row positions are located by scanning for header labels rather than hardcoded.
The workbook is a living document and rows shift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.db.models import (
    Ingredient,
    IngredientPrice,
    LegacyStagedRecipe,
    ManualRecipeLine,
    MenuItem,
    PriceSource,
    SizeCode,
    Tier,
    Unit,
)
from cafeops.domain.units import UnknownUnitError, convert, parse_unit
from cafeops.seed.patterns import StagedLine, TemplateProposal, propose_templates
from cafeops.seed.roles import infer_role, is_standalone
from cafeops.seed.shelf_life import default_for

# --------------------------------------------------------------------------
# Decisions that are not in the workbook
# --------------------------------------------------------------------------

#: Agreed tier A roster (12 items). Oat milk is on the roster but HELD AT B until
#: a real Lightspeed payload proves modifiers arrive on sale lines -- see
#: OAT_MILK_HELD below. Spec 4.5: tier A membership is earned, not assigned.
TIER_A_NAMES: frozenset[str] = frozenset(
    {
        "Napkin",
        "Whole milk",
        "Coffee beans (house blend)",
        "12oz paper cup",
        "12oz cup lid",
        "16oz paper cup",
        "16oz cup lid",
        "8oz paper cup",
        "8oz cup lid",
        "Chocolate powder",
        "Matcha powder",
    }
)

#: Tier A candidate whose consumption is only visible through POS modifiers.
#: Starts at B. Promote once Agent A confirms modifiers arrive on sale lines.
OAT_MILK_HELD: str = "Oat milk (barista)"

TIER_B_CATEGORIES: frozenset[str] = frozenset(
    {"Syrup", "Dairy", "Dairy alt", "Coffee", "Tea", "Chocolate", "Specialty", "Packaging"}
)

#: Initial waste factors. Spec 5.1: tuned from observed drift, not guessed once.
#: These are deliberate starting guesses the drift report will correct.
WASTE_FACTORS: dict[str, Decimal] = {
    "Whole milk": Decimal("0.10"),
    "Semi-skimmed milk": Decimal("0.10"),
    "Oat milk (barista)": Decimal("0.10"),
    "Almond milk (barista)": Decimal("0.10"),
    "Soy milk (barista)": Decimal("0.10"),
    "Coconut milk (barista)": Decimal("0.10"),
    "Coffee beans (house blend)": Decimal("0.05"),
    "Green matcha powder": Decimal("0.03"),
    "Chocolate powder": Decimal("0.03"),
}
#: Packaging and sundries: breakage, misprints, the odd double-napkin.
PACKAGING_WASTE = Decimal("0.01")

#: Data-quality dirt the spec names explicitly. Surfaced, never silently fixed.
KNOWN_DIRT_FRAGMENTS: tuple[tuple[str, str], ...] = (
    ("'card'", "Not a menu item -- a gift card sold at face value. Zero cost is correct."),
    ("Syrup Gift Set", "Retail item with no recipe; zero cost needs verifying."),
    ("Strawberry bliss", "Probable typo -- verify the intended item name."),
    ("VERIFY", "Composition flagged unclear in the workbook."),
)


def _price_source(note: str | None) -> PriceSource:
    """Map the "Supplier / notes" prose onto a source.

    Defaults to ESTIMATE, not INVOICE. Guessing upward would launder a guess into
    an invoice and invariant 6 exists precisely to stop that.
    """
    text = (note or "").lower()
    if "real invoice" in text or "invoice" in text:
        return PriceSource.INVOICE
    return PriceSource.ESTIMATE


@dataclass
class LegacyImportReport:
    ingredients: int = 0
    prices: int = 0
    staged_lines: int = 0
    manual_recipe_lines: int = 0
    menu_items: int = 0
    manual_items: int = 0
    proposals: list[TemplateProposal] = field(default_factory=list)
    tier_counts: dict[str, int] = field(default_factory=dict)
    estimated_cost_count: int = 0
    perishables: int = 0
    unknown_shelf_life: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data_quality: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def patterns(self) -> list[TemplateProposal]:
        return [p for p in self.proposals if not p.is_singleton]

    @property
    def singletons(self) -> list[TemplateProposal]:
        return [p for p in self.proposals if p.is_singleton]

    def summary(self) -> str:
        tiers = ", ".join(f"{k}={v}" for k, v in sorted(self.tier_counts.items()))
        return (
            f"{self.ingredients} ingredients ({tiers}), {self.prices} price rows "
            f"({self.estimated_cost_count} ESTIMATE), {self.staged_lines} staged recipe "
            f"lines -> {self.manual_recipe_lines} manual recipe lines, "
            f"{self.menu_items} menu items, {self.perishables} perishables, "
            f"{len(self.patterns)} patterns proposed, "
            f"{len(self.singletons)} one-offs"
        )


def _pence(value: Any) -> int:
    if value is None or value == "":
        return 0
    return int((Decimal(str(value)) * 100).quantize(Decimal("1")))


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _find_header_row(ws: Any, first_label: str, *, start: int = 1) -> int:
    for row_idx in range(start, ws.max_row + 1):
        if str(ws.cell(row=row_idx, column=2).value or "").strip() == first_label:
            return row_idx
    raise LookupError(f"header {first_label!r} not found in sheet {ws.title!r}")


def _dirt_for(name: str, note: str | None = None) -> str | None:
    haystack = f"{name} {note or ''}"
    for fragment, explanation in KNOWN_DIRT_FRAGMENTS:
        if fragment.lower() in haystack.lower():
            return explanation
    return None


def _size_code(raw: str | None) -> SizeCode | None:
    if raw is None:
        return None
    key = str(raw).strip().upper()
    if key in ("ONE", "ONE SIZE", ""):
        return SizeCode.ONE
    try:
        return SizeCode(key)
    except ValueError:
        return None


# ==========================================================================
# Entry point
# ==========================================================================


#: Legacy prices and recipes are effective from the café's opening, NOT from import
#: time. Spec §1: "Opened November 2025". Dating them at import instead makes every
#: cost lookup before today return None, which silently breaks historical costing,
#: back-dated P&L, and any rollup at a past date. The workbook describes what was
#: true for the whole trading history we have, so that is the date it gets.
LEGACY_EFFECTIVE_FROM: datetime = datetime(2025, 11, 1, tzinfo=UTC)


def import_legacy(
    session: Session,
    path: Path,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
    effective_from: datetime | None = None,
) -> LegacyImportReport:
    """Run all three passes. With dry_run=True nothing is committed by the caller."""
    report = LegacyImportReport(dry_run=dry_run)
    imported_at = now or datetime.now(UTC)
    effective_from = effective_from or LEGACY_EFFECTIVE_FROM
    if effective_from > imported_at:
        raise ValueError(
            f"effective_from {effective_from.isoformat()} is in the future relative to "
            f"{imported_at.isoformat()}"
        )
    wb = load_workbook(path, data_only=True, read_only=False)

    ingredients = _pass1_ingredients(session, wb, report, effective_from)
    session.flush()
    staged = _pass2_stage_recipes(session, wb, ingredients, report, effective_from)
    session.flush()
    report.proposals = propose_templates(staged)
    _count_item_kinds(report)
    return report


# --------------------------------------------------------------------------
# Pass 1
# --------------------------------------------------------------------------


def _pass1_ingredients(
    session: Session, wb: Any, report: LegacyImportReport, effective_from: datetime
) -> dict[str, Ingredient]:
    ws = wb["Ingredients"]
    header = _find_header_row(ws, "Ingredient")
    out: dict[str, Ingredient] = {}

    for row in ws.iter_rows(min_row=header + 1, values_only=True):
        raw_name = row[1] if len(row) > 1 else None
        if raw_name is None or not str(raw_name).strip():
            continue
        name = str(raw_name).strip()
        category = str(row[2]).strip() if len(row) > 2 and row[2] else None
        pack_size = _dec(row[3] if len(row) > 3 else None)
        pack_unit_raw = row[4] if len(row) > 4 else None
        pack_cost = row[5] if len(row) > 5 else None
        unit_raw = row[7] if len(row) > 7 else None
        note = str(row[8]).strip() if len(row) > 8 and row[8] else None

        # Stocking unit: prefer the dedicated Unit column, fall back to the pack
        # unit. Unlike the earlier workbook this one's Unit column is clean, but
        # the fallback costs nothing and a missing unit must not skip a row
        # silently.
        stocking_unit: Unit | None = None
        for candidate in (unit_raw, pack_unit_raw):
            try:
                stocking_unit, _unit_mult = parse_unit(candidate)
                break
            except UnknownUnitError:
                continue
        if stocking_unit is None:
            report.warnings.append(
                f"{name!r}: no usable unit (Unit={unit_raw!r}, Pack unit={pack_unit_raw!r}) "
                "-- SKIPPED"
            )
            continue

        role = infer_role(name, category)
        tier = _tier_for(name, category)
        ingredient = session.scalar(select(Ingredient).where(Ingredient.name == name))
        if ingredient is None:
            ingredient = Ingredient(name=name)
            session.add(ingredient)
            report.ingredients += 1
        ingredient.unit = stocking_unit
        ingredient.category = category
        ingredient.tier = tier
        ingredient.tracking_enabled = tier in (Tier.A, Tier.B)
        ingredient.waste_factor = _waste_for(name, role)
        ingredient.source_note = note

        # --- shelf life (spec 4.1) -----------------------------------------
        # Not in the workbook. Seeded from industry defaults and flagged ESTIMATE so
        # it stays visible until a human confirms it, exactly like prices. Without
        # these, spec 5.4's cap is inert and the system will happily order 9 days of
        # milk onto a 7-day life.
        shelf, known = default_for(name, category)
        ingredient.storage = shelf.storage
        ingredient.shelf_life_days = shelf.shelf_life_days
        ingredient.open_life_days = shelf.open_life_days
        ingredient.transit_buffer_days = shelf.transit_buffer_days
        ingredient.shelf_life_source = PriceSource.ESTIMATE
        if not known:
            report.unknown_shelf_life.append(name)
        elif shelf.shelf_life_days is not None:
            report.perishables += 1
        out[name] = ingredient
        report.tier_counts[tier.value] = report.tier_counts.get(tier.value, 0) + 1

        dirt = _dirt_for(name, note)
        if dirt:
            report.data_quality.append(f"ingredient {name!r}: {dirt}")

        # --- price ---------------------------------------------------------
        if pack_size is None or pack_size <= 0:
            report.warnings.append(f"{name!r}: no pack size -- no price row, cost stays UNKNOWN")
            continue
        try:
            pack_unit, pack_mult = parse_unit(pack_unit_raw or unit_raw)
        except UnknownUnitError:
            report.warnings.append(
                f"{name!r}: pack unit {pack_unit_raw!r} unrecognised -- no price row"
            )
            continue

        pack_qty_native = pack_size * pack_mult
        try:
            pack_qty = convert(pack_qty_native, pack_unit, stocking_unit)
        except Exception as exc:  # incompatible dimensions
            report.warnings.append(f"{name!r}: {exc} -- no price row")
            continue

        cost_pence = _pence(pack_cost)
        source = _price_source(note)
        if source is PriceSource.ESTIMATE:
            report.estimated_cost_count += 1

        session.flush()
        existing = session.scalar(
            select(IngredientPrice).where(
                IngredientPrice.ingredient_id == ingredient.id,
                IngredientPrice.effective_to.is_(None),
            )
        )
        if existing is None:
            price = IngredientPrice(
                ingredient_id=ingredient.id,
                pack_size=pack_qty,
                pack_unit=stocking_unit,
                pack_cost_pence=cost_pence,
                cost_per_unit_pence=(Decimal(cost_pence) / pack_qty if pack_qty else Decimal("0")),
                effective_from=effective_from,
                source=source,
                note=note,
            )
            session.add(price)
            report.prices += 1
        else:
            price = existing
            price.pack_size = pack_qty
            price.pack_unit = stocking_unit
            price.pack_cost_pence = cost_pence
            price.cost_per_unit_pence = Decimal(cost_pence) / pack_qty if pack_qty else Decimal("0")
            price.source = source

        # Refresh the denormalised cache, carrying the source with it so
        # invariant 6 survives into aggregates built on this column.
        ingredient.current_cost_pence_per_unit = price.cost_per_unit_pence
        ingredient.current_cost_source = source

    if report.unknown_shelf_life:
        report.data_quality.append(
            f"{len(report.unknown_shelf_life)} ingredient(s) have no shelf-life default, "
            f"so their orders are not capped by spoilage: "
            f"{sorted(report.unknown_shelf_life)[:8]}"
        )
    report.warnings.append(
        f"All shelf lives are ESTIMATE defaults, not measurements ({report.perishables} "
        "perishables). They cap order size (invariant 4), so a wrong one either wastes "
        "stock or causes a stockout. Confirm the perishables before trusting an order."
    )

    absent = sorted(TIER_A_NAMES - set(out))
    if absent:
        report.warnings.append(
            f"{len(absent)} tier A name(s) not found in the workbook, so they are not "
            f"tracked: {absent}. Fix TIER_A_NAMES or the sheet."
        )
    if OAT_MILK_HELD in out:
        report.warnings.append(
            f"{OAT_MILK_HELD!r} is a tier A candidate held at tier B until a real "
            "Lightspeed payload proves modifiers arrive on sale lines. Until then its "
            "consumption is not calculated."
        )
    return out


def _tier_for(name: str, category: str | None) -> Tier:
    if name == OAT_MILK_HELD:
        return Tier.B  # tier A candidate, held pending modifier evidence
    if name in TIER_A_NAMES:
        return Tier.A
    if is_standalone(category):
        return Tier.C
    if (category or "").strip() in TIER_B_CATEGORIES:
        return Tier.B
    return Tier.C


def _waste_for(name: str, role: Any) -> Decimal:
    from cafeops.domain.types import ComponentRole

    if name in WASTE_FACTORS:
        return WASTE_FACTORS[name]
    if role in (ComponentRole.PACKAGING, ComponentRole.SUNDRY):
        return PACKAGING_WASTE
    return Decimal("0")


# --------------------------------------------------------------------------
# Pass 2
# --------------------------------------------------------------------------


def _pass2_stage_recipes(
    session: Session,
    wb: Any,
    ingredients: dict[str, Ingredient],
    report: LegacyImportReport,
    effective_from: datetime,
) -> list[StagedLine]:
    """Port the flat recipes verbatim into staging. No interpretation."""
    ws = wb["Recipes"]
    summary_header = _find_header_row(ws, "Recipe #")
    detail_header = _find_header_row(ws, "Recipe #", start=summary_header + 1)

    # recipe # -> (item name, size, category, sell price)
    meta: dict[int, tuple[str, str | None, str | None, int]] = {}
    seen_identity: dict[tuple[str, str | None], int] = {}
    for row_idx in range(summary_header + 1, detail_header):
        raw_no = ws.cell(row=row_idx, column=2).value
        raw_name = ws.cell(row=row_idx, column=3).value
        if raw_no is None or raw_name is None:
            continue
        try:
            recipe_no = int(float(raw_no))
        except (TypeError, ValueError):
            continue
        name = str(raw_name).strip()
        raw_size = ws.cell(row=row_idx, column=4).value
        size = str(raw_size).strip() if raw_size else None
        raw_cat = ws.cell(row=row_idx, column=5).value
        category = str(raw_cat).strip() if raw_cat else None
        price = _pence(ws.cell(row=row_idx, column=6).value)

        identity = (name, size)
        first = seen_identity.get(identity)
        if first is not None:
            # Two recipe numbers for one item x size: the workbook holds a
            # redundant copy. Keep the first; merging them would roughly double
            # the drink's ingredients.
            report.data_quality.append(
                f"{name!r} [{size or '-'}] has duplicate recipes (#{first} and #{recipe_no}); "
                f"using #{first}, ignoring #{recipe_no}"
            )
            continue
        seen_identity[identity] = recipe_no
        meta[recipe_no] = (name, size, category, price)

    # --- menu items ------------------------------------------------------
    for name, size in seen_identity:
        size_code = _size_code(size)
        existing = session.scalar(
            select(MenuItem).where(MenuItem.name == name, MenuItem.size_code == size_code)
        )
        if existing is not None:
            continue
        recipe_no = seen_identity[(name, size)]
        _n, _s, category, price = meta[recipe_no]
        dirt = _dirt_for(name)
        session.add(
            MenuItem(
                name=name,
                size_code=size_code,
                category=category,
                price_pence=price,
                active=True,
                # Everything starts manual. Template assignment happens only
                # after a human confirms a proposal (spec 6 pass 3).
                manual_recipe=True,
                data_quality_flag=dirt,
            )
        )
        report.menu_items += 1
        if dirt:
            report.data_quality.append(f"menu item {name!r}: {dirt}")

    # --- staged lines ----------------------------------------------------
    staged: list[StagedLine] = []
    unknown_recipe_nos: set[int] = set()
    missing_ingredients: set[str] = set()
    unresolved_roles: set[str] = set()

    for row in ws.iter_rows(min_row=detail_header + 1, values_only=True):
        raw_no = row[1] if len(row) > 1 else None
        raw_ing = row[2] if len(row) > 2 else None
        qty = _dec(row[3] if len(row) > 3 else None)
        raw_unit = row[4] if len(row) > 4 else None
        if raw_no is None or raw_ing is None or qty is None:
            continue
        try:
            recipe_no = int(float(raw_no))
        except (TypeError, ValueError):
            continue
        if recipe_no not in meta:
            unknown_recipe_nos.add(recipe_no)
            continue

        item_name, size, category, price = meta[recipe_no]
        ing_name = str(raw_ing).strip()
        ingredient = ingredients.get(ing_name)
        if ingredient is None:
            missing_ingredients.add(ing_name)

        role = infer_role(ing_name, ingredient.category if ingredient else None)
        if role is None:
            unresolved_roles.add(ing_name)

        # Store the quantity in the ingredient's own stocking unit so nothing
        # downstream has to re-parse a workbook unit string.
        qty_native = qty
        flag: str | None = None
        if ingredient is not None:
            try:
                src_unit, mult = parse_unit(raw_unit)
                qty_native = convert(qty * mult, src_unit, ingredient.unit)
            except UnknownUnitError:
                flag = f"unrecognised recipe unit {raw_unit!r}"
            except Exception as exc:
                flag = str(exc)
        else:
            flag = "ingredient not in the Ingredients sheet"

        session.add(
            LegacyStagedRecipe(
                recipe_no=recipe_no,
                item_name=item_name,
                size_code=size,
                category=category,
                sell_price_pence=price,
                ingredient_name=ing_name,
                ingredient_id=ingredient.id if ingredient is not None else None,
                qty=qty_native,
                raw_unit=str(raw_unit) if raw_unit else None,
                role=role,
                notes=None,
                data_quality_flag=flag,
            )
        )
        report.staged_lines += 1
        staged.append(
            StagedLine(
                recipe_no=recipe_no,
                item_name=item_name,
                size_code=size,
                category=category,
                sell_price_pence=price,
                ingredient_name=ing_name,
                qty=qty_native,
                role=role,
            )
        )

    # --- manual recipe lines ---------------------------------------------
    # Every imported item starts manual (spec §6: template assignment waits for a
    # human to confirm a proposal). A manual item resolves through
    # manual_recipe_line, so without these rows all 278 of them cost nothing and
    # deplete nothing -- they would look configured and behave as if empty.
    # Materialising a proposal later closes these lines and re-points the item.
    report.manual_recipe_lines = _write_manual_recipe_lines(session, staged, report, effective_from)

    if missing_ingredients:
        report.warnings.append(
            f"{len(missing_ingredients)} recipe ingredient name(s) absent from the "
            f"Ingredients sheet: {sorted(missing_ingredients)[:8]}"
        )
    if unknown_recipe_nos:
        report.warnings.append(
            f"{len(unknown_recipe_nos)} recipe number(s) have detail but no usable summary "
            f"row, so their lines were not staged: {sorted(unknown_recipe_nos)[:8]}"
        )
    if unresolved_roles:
        report.data_quality.append(
            f"{len(unresolved_roles)} ingredient(s) could not be assigned a component role, "
            f"so any template using them needs review: {sorted(unresolved_roles)[:8]}"
        )
    return staged


def _write_manual_recipe_lines(
    session: Session,
    staged: list[StagedLine],
    report: LegacyImportReport,
    effective_from: datetime,
) -> int:
    """Turn staged legacy lines into effective-dated manual_recipe_line rows.

    Quantities in `staged` are already normalised to each ingredient's stocking
    unit by pass 2, so nothing is re-parsed here.

    Duplicate (item, ingredient) pairs within one recipe are SUMMED rather than
    dropped: two "milk" rows in one drink are additive. Cross-recipe duplicates
    cannot reach here -- pass 2 already ignores redundant recipe numbers.
    """
    # (item name, size) -> ingredient id -> summed qty
    wanted: dict[tuple[str, str | None], dict[int, Decimal]] = {}
    unresolved = 0
    for line in staged:
        ingredient = session.scalar(
            select(Ingredient).where(Ingredient.name == line.ingredient_name)
        )
        if ingredient is None:
            unresolved += 1
            continue
        key = (line.item_name, line.size_code)
        bucket = wanted.setdefault(key, {})
        bucket[ingredient.id] = bucket.get(ingredient.id, Decimal("0")) + line.qty

    written = 0
    for (name, size), by_ingredient in wanted.items():
        item = session.scalar(
            select(MenuItem).where(MenuItem.name == name, MenuItem.size_code == _size_code(size))
        )
        if item is None:
            continue
        # Do not touch an item already driven by a template: its components begin
        # at their own effective date, and adding manual lines would double-count.
        if not item.manual_recipe:
            continue
        for ingredient_id, qty in by_ingredient.items():
            if qty == 0:
                continue
            existing = session.scalar(
                select(ManualRecipeLine).where(
                    ManualRecipeLine.menu_item_id == item.id,
                    ManualRecipeLine.ingredient_id == ingredient_id,
                    ManualRecipeLine.effective_to.is_(None),
                )
            )
            if existing is not None:
                existing.qty = qty
                continue
            session.add(
                ManualRecipeLine(
                    menu_item_id=item.id,
                    ingredient_id=ingredient_id,
                    qty=qty,
                    effective_from=effective_from,
                )
            )
            written += 1

    if unresolved:
        report.warnings.append(
            f"{unresolved} staged line(s) had no matching ingredient, so they are "
            "absent from manual recipes"
        )
    return written


def _count_item_kinds(report: LegacyImportReport) -> None:
    report.manual_items = sum(p.menu_item_count for p in report.singletons)
