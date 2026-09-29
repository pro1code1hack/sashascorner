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
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import func, select
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
from cafeops.db.repositories.ingredient import SqlIngredientRepository
from cafeops.db.types import QTY_SCALE
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
    #: Forced re-import only: open rows closed and replaced from today (never edited).
    prices_superseded: int = 0
    recipe_lines_superseded: int = 0
    #: Forced re-import only: ingredients whose price or fields the workbook lost to.
    prices_kept: list[str] = field(default_factory=list)
    fields_kept: list[str] = field(default_factory=list)
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
        superseded = (
            f" (superseded from today: {self.prices_superseded} price(s), "
            f"{self.recipe_lines_superseded} recipe line(s))"
            if self.prices_superseded or self.recipe_lines_superseded
            else ""
        )
        return (
            f"{self.ingredients} ingredients ({tiers}), {self.prices} price rows "
            f"({self.estimated_cost_count} ESTIMATE), {self.staged_lines} staged recipe "
            f"lines -> {self.manual_recipe_lines} manual recipe lines, "
            f"{self.menu_items} menu items, {self.perishables} perishables, "
            f"{len(self.patterns)} patterns proposed, "
            f"{len(self.singletons)} one-offs{superseded}"
        )


def _pence(value: Any, where: str) -> int | None:
    """A £ cell to integer pence, exactly; None when the cell is blank.

    The same rule as `seed/finance_import._pence`: a missing amount is None, never 0
    (invariant 8 -- "costs nothing" and "we do not know" are different facts), and a
    third decimal place is refused rather than rounded, because rounding a price
    silently is a guess the workbook did not make.
    """
    if value is None or value == "":
        return None
    if isinstance(value, str) and value.startswith("="):
        raise ValueError(f"{where}: formula without a cached value ({value})")
    try:
        d = Decimal(str(value).replace("£", "").replace(",", "").strip())
    except InvalidOperation as exc:
        raise ValueError(f"{where}: {value!r} is not money") from exc
    pence = d * 100
    if pence != pence.to_integral_value():
        raise ValueError(f"{where}: {value!r} has more than two decimal places")
    return int(pence)


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
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


class LegacyImportRefused(RuntimeError):
    """The database already holds ingredients and the caller did not pass `force`."""


def import_legacy(
    session: Session,
    path: Path,
    *,
    dry_run: bool = False,
    force: bool = False,
    now: datetime | None = None,
    effective_from: datetime | None = None,
) -> LegacyImportReport:
    """Run all three passes. With dry_run=True nothing is committed by the caller.

    The workbook is imported once. On a database that already holds ingredients a
    write is refused unless `force=True`, because by then the rows carry things the
    workbook does not know: drift-tuned waste factors, tiers changed with a history
    row, confirmed shelf lives, invoice prices. Even forced, an existing row is only
    filled where blank (see `_merge_existing`), and a changed price or recipe
    quantity closes the open row and opens a new one from `now` -- history is never
    rewritten (invariant 3). A dry run is never refused: it writes nothing, and on a
    live database it previews what `force` would do.
    """
    report = LegacyImportReport(dry_run=dry_run)
    imported_at = now or datetime.now(UTC)
    effective_from = effective_from or LEGACY_EFFECTIVE_FROM
    if effective_from > imported_at:
        raise ValueError(
            f"effective_from {effective_from.isoformat()} is in the future relative to "
            f"{imported_at.isoformat()}"
        )
    existing = session.scalar(select(func.count(Ingredient.id))) or 0
    if existing:
        if not dry_run and not force:
            raise LegacyImportRefused(
                f"this database already holds {existing} ingredient(s). Re-importing the "
                "legacy workbook would overwrite what was learned since; pass "
                "--force-legacy to fill blanks only and supersede changed prices and "
                "recipe quantities from today."
            )
        report.warnings.append(
            f"The database already holds {existing} ingredient(s): existing rows are only "
            "filled where blank; a changed price or recipe quantity is superseded from "
            "today, never rewritten"
            + ("" if force or not dry_run else " (a --commit needs --force-legacy)")
            + "."
        )
    wb = load_workbook(path, data_only=True, read_only=False)

    ingredients = _pass1_ingredients(session, wb, report, effective_from, imported_at)
    session.flush()
    staged = _pass2_stage_recipes(session, wb, ingredients, report, effective_from, imported_at)
    session.flush()
    report.proposals = propose_templates(staged)
    _count_item_kinds(report)
    return report


# --------------------------------------------------------------------------
# Pass 1
# --------------------------------------------------------------------------


def _pass1_ingredients(
    session: Session,
    wb: Any,
    report: LegacyImportReport,
    effective_from: datetime,
    imported_at: datetime,
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
        # Shelf life (spec 4.1) is not in the workbook. Seeded from industry defaults
        # and flagged ESTIMATE so it stays visible until a human confirms it, exactly
        # like prices. Without these, spec 5.4's cap is inert and the system will
        # happily order 9 days of milk onto a 7-day life.
        shelf, known = default_for(name, category)
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
            ingredient.storage = shelf.storage
            ingredient.shelf_life_days = shelf.shelf_life_days
            ingredient.open_life_days = shelf.open_life_days
            ingredient.transit_buffer_days = shelf.transit_buffer_days
            ingredient.shelf_life_source = PriceSource.ESTIMATE
        else:
            _merge_existing(
                ingredient,
                report,
                unit=stocking_unit,
                category=category,
                tier=tier,
                waste_factor=_waste_for(name, role),
                note=note,
                shelf_life_days=shelf.shelf_life_days,
                open_life_days=shelf.open_life_days,
            )
            tier = ingredient.tier
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

        try:
            cost_pence = _pence(pack_cost, f"{name!r} pack cost")
        except ValueError as exc:
            report.warnings.append(f"{exc} -- no price row, cost stays UNKNOWN")
            cost_pence = None
        source = _price_source(note)
        if cost_pence is None:
            # Unknown, not free (invariant 8): no price row, and a new ingredient's
            # cost cache stays None with the ESTIMATE flag every rollup expects.
            report.estimated_cost_count += 1
            if ingredient.current_cost_pence_per_unit is None:
                ingredient.current_cost_source = PriceSource.ESTIMATE
            if pack_cost is None or pack_cost == "":
                report.warnings.append(
                    f"{name!r}: no pack cost -- no price row, cost stays UNKNOWN"
                )
            continue
        if source is PriceSource.ESTIMATE:
            report.estimated_cost_count += 1

        session.flush()
        _record_price(
            session,
            ingredient,
            report,
            pack_qty=pack_qty,
            pack_unit=stocking_unit,
            cost_pence=cost_pence,
            source=source,
            note=note,
            effective_from=effective_from,
            imported_at=imported_at,
        )

    if report.prices_kept:
        report.data_quality.append(
            f"{len(report.prices_kept)} ingredient(s) kept a price that differs from the "
            "workbook, because it was recorded after it or is confirmed: "
            f"{sorted(report.prices_kept)[:8]}"
        )
    if report.fields_kept:
        report.data_quality.append(
            f"{len(report.fields_kept)} ingredient(s) kept a unit, tier or waste factor "
            f"that differs from the workbook's default: {sorted(report.fields_kept)[:5]}"
        )
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


#: A price source is stronger than ESTIMATE; the workbook never downgrades one.
_CONFIRMED_PRICE = (PriceSource.INVOICE, PriceSource.SUPPLIER_FEED)


def _merge_existing(
    ingredient: Ingredient,
    report: LegacyImportReport,
    *,
    unit: Unit,
    category: str | None,
    tier: Tier,
    waste_factor: Decimal,
    note: str | None,
    shelf_life_days: int | None,
    open_life_days: int | None,
) -> None:
    """Fill blanks on an ingredient that already exists; keep everything else.

    The rule `services/reference_seed._fill_ingredient` uses: what a person or the
    drift report set wins over the workbook. The unit is never changed (every stock
    quantity is stored in it), the tier is never changed here (a tier change writes
    an `IngredientTierChange` row through its own service), a waste factor is only
    set while it is still the column default of zero (a non-zero one was guessed at
    import or tuned from drift since), and shelf life is only filled where it is
    blank and not confirmed.
    """
    kept: list[str] = []
    if ingredient.unit is not unit:
        kept.append(f"unit {ingredient.unit.value} (workbook: {unit.value})")
    if ingredient.category is None and category is not None:
        ingredient.category = category
    if ingredient.tier is not tier:
        kept.append(f"tier {ingredient.tier.value} (workbook default: {tier.value})")
    if ingredient.waste_factor == 0 and waste_factor != 0:
        ingredient.waste_factor = waste_factor
    elif ingredient.waste_factor != waste_factor:
        kept.append(f"waste factor {ingredient.waste_factor} (workbook default: {waste_factor})")
    if ingredient.source_note is None and note is not None:
        ingredient.source_note = note
    if ingredient.shelf_life_source in (None, PriceSource.ESTIMATE):
        filled = False
        if ingredient.shelf_life_days is None and shelf_life_days is not None:
            ingredient.shelf_life_days = shelf_life_days
            filled = True
        if ingredient.open_life_days is None and open_life_days is not None:
            ingredient.open_life_days = open_life_days
            filled = True
        if filled:
            ingredient.shelf_life_source = PriceSource.ESTIMATE
    if kept:
        report.fields_kept.append(f"{ingredient.name} ({'; '.join(kept)})")


def _record_price(
    session: Session,
    ingredient: Ingredient,
    report: LegacyImportReport,
    *,
    pack_qty: Decimal,
    pack_unit: Unit,
    cost_pence: int,
    source: PriceSource,
    note: str | None,
    effective_from: datetime,
    imported_at: datetime,
) -> None:
    """Open the ingredient's first price, or supersede a changed one from today.

    The open row is never edited (invariant 3): a different price closes it at
    `imported_at` and opens a new row there, through the repository that owns that
    close-and-open (`SqlIngredientRepository.add_price`, which also refreshes the
    cost cache with its source). An unchanged price is left alone, so a forced
    re-run is a no-op for prices. Only a price this import wrote (open since the
    workbook's own date) is ever superseded -- one recorded later is newer knowledge
    -- and a confirmed price (invoice or supplier feed) never gives way to an estimate.
    """
    current = session.scalar(
        select(IngredientPrice)
        .where(
            IngredientPrice.ingredient_id == ingredient.id,
            IngredientPrice.effective_to.is_(None),
        )
        .order_by(IngredientPrice.effective_from.desc())
        .limit(1)
    )
    repo = SqlIngredientRepository(session)
    if current is None:
        try:
            repo.add_price(
                ingredient.id,
                pack_size=pack_qty,
                pack_unit=pack_unit,
                pack_cost_pence=cost_pence,
                effective_from=effective_from,
                source=source,
                note=note,
            )
        except ValueError as exc:  # pack units across dimensions from the ingredient's
            report.warnings.append(f"{ingredient.name!r}: {exc} -- no price row")
            return
        report.prices += 1
        return

    try:
        unchanged = (
            current.pack_cost_pence == cost_pence
            and current.source is source
            and convert(current.pack_size, current.pack_unit, ingredient.unit)
            == convert(pack_qty, pack_unit, ingredient.unit)
        )
    except ValueError as exc:
        report.warnings.append(f"{ingredient.name!r}: {exc} -- price kept")
        return
    if unchanged:
        return
    if current.effective_from != effective_from:
        # Recorded after the workbook's date: in the app, from an invoice, from the
        # reference seed. Newer knowledge than a dead spreadsheet; keep it.
        report.prices_kept.append(ingredient.name)
        return
    if current.source in _CONFIRMED_PRICE and source is PriceSource.ESTIMATE:
        report.prices_kept.append(ingredient.name)
        return
    repo.add_price(
        ingredient.id,
        pack_size=pack_qty,
        pack_unit=pack_unit,
        pack_cost_pence=cost_pence,
        effective_from=imported_at,
        source=source,
        note=note,
    )
    report.prices += 1
    report.prices_superseded += 1


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
    imported_at: datetime,
) -> list[StagedLine]:
    """Port the flat recipes verbatim into staging. No interpretation.

    Staging is written once. A forced re-run still builds the lines in memory (the
    proposals and manual recipes need them) but adds no second copy of the staging
    table: `services/materialise_template` rebuilds its input from that table, and a
    duplicate would double every quantity it proposes.
    """
    already_staged = bool(session.scalar(select(func.count(LegacyStagedRecipe.id))))
    if already_staged:
        report.warnings.append(
            "legacy_staged_recipe already holds the first import's lines; they were kept "
            "and not staged again."
        )
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
        try:
            sell = _pence(ws.cell(row=row_idx, column=6).value, f"recipe #{recipe_no} price")
        except ValueError as exc:
            report.data_quality.append(str(exc))
            sell = None
        if sell is None:
            # A sell price is not a cost: the column is NOT NULL and 0 is its default,
            # so the item imports at 0 -- but it is said out loud, not assumed.
            report.data_quality.append(f"{name!r} [{size or '-'}]: no usable sell price, set to 0")
        price = sell or 0

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

        if not already_staged:
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
    report.manual_recipe_lines = _write_manual_recipe_lines(
        session, staged, report, effective_from, imported_at
    )

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
    imported_at: datetime,
) -> int:
    """Turn staged legacy lines into effective-dated manual_recipe_line rows.

    An open line with the same quantity, or one opened after the workbook's date (an
    edit made since), is left alone; a line this import wrote whose quantity the
    workbook now changes is closed at `imported_at` and a new line opens there
    (invariant 3: recipe edits are effective-dated, history is never rewritten).

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
            starts = effective_from
            if existing is not None:
                # Only a line this import wrote is superseded; a later one is an edit
                # somebody made since, and the workbook does not outrank it.
                # Compared at the stored precision: 1/6 of a loaf is 0.166667 in the
                # column and 0.1666... here, and that is not a recipe change.
                same = _stored(existing.qty) == _stored(qty)
                if same or existing.effective_from != effective_from:
                    continue
                existing.effective_to = imported_at
                starts = imported_at
                report.recipe_lines_superseded += 1
            session.add(
                ManualRecipeLine(
                    menu_item_id=item.id,
                    ingredient_id=ingredient_id,
                    qty=qty,
                    effective_from=starts,
                )
            )
            written += 1

    if unresolved:
        report.warnings.append(
            f"{unresolved} staged line(s) had no matching ingredient, so they are "
            "absent from manual recipes"
        )
    return written


def _stored(qty: Decimal) -> Decimal:
    """A quantity as the `Qty` column keeps it (QTY_SCALE places, half-up)."""
    return qty.quantize(Decimal(1).scaleb(-QTY_SCALE), rounding=ROUND_HALF_UP)


def _count_item_kinds(report: LegacyImportReport) -> None:
    report.manual_items = sum(p.menu_item_count for p in report.singletons)
