"""Turning a CONFIRMED template proposal into real composition rows. Spec 6 pass 3.

`seed/patterns.py` proposes 27 templates from the legacy workbook and deliberately
writes **none** of them: auto-generating templates from dirty data and treating them
as truth is how you get a system confidently costing drinks wrong. Every menu item
lands `manual_recipe=True` and stays that way until a human says otherwise.

This module is that "otherwise". It takes one proposal BY NAME -- the act of naming it
is the confirmation -- and writes `drink_template`, `size_profile`,
`template_component`, `variant_axis` and `variant_option`, then re-points the
`MenuItem` rows at the new template and clears `manual_recipe`.
`seed/demo.py::_build_latte_template` is the shape it produces, by hand, for the
latte.

Four refusals, each protecting something a later screen depends on:

1. **A proposal with conflicts is refused** unless the caller explicitly accepts
   them. A conflict means the legacy rows disagree about a quantity at one size --
   two real sub-patterns lumped together. Materialising it silently would bake one
   arbitrary quantity into every drink in the group.
2. **A singleton proposal is refused.** One base item is not a pattern; it stays a
   manual recipe, which is the correct model for a cake or a bottled drink.
3. **A menu item already attached to another template is skipped, never stolen.**
   Re-pointing it would change what its past sales are deemed to have consumed --
   the template's components begin today, so history before today would resolve to
   nothing. Invariant 3 forbids that, and a warning says so per item.
4. **Effective dating starts today, never earlier.** Same reason, same invariant.

Everything happens in ONE transaction, and the cost rollup for the new template is
part of it: a template with no cached costs is a margin screen with holes in it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from cafeops.db.models import (
    ComponentRole,
    DrinkTemplate,
    Ingredient,
    LegacyStagedRecipe,
    ManualRecipeLine,
    MenuItem,
    SizeCode,
    SizeProfile,
    TemplateComponent,
    VariantAxis,
    VariantOption,
)
from cafeops.jobs.cost_rollup import RollupReport, rollup_for_template
from cafeops.seed.patterns import StagedLine, TemplateProposal, propose_templates
from cafeops.seed.roles import AXIS_CANDIDATE_ROLES, SIZE_DETERMINED_ROLES
from cafeops.services.edit_composition import require_not_retroactive

__all__ = [
    "MaterialisationReport",
    "ProposalAlreadyMaterialised",
    "ProposalHasConflicts",
    "ProposalNotFound",
    "find_proposal",
    "list_proposals",
    "materialise_proposal",
]

#: Roles a modifier may swap. MILK only, because that is the one the POS actually
#: modifies: an oat latte is a latte with an oat modifier. A SUBSTITUTE against any
#: other slot raises (spec 4.3 rule 4), which is the behaviour we want -- nobody
#: substitutes the cup.
SUBSTITUTABLE_ROLES: frozenset[ComponentRole] = frozenset({ComponentRole.MILK})

_SIZE_LABELS: dict[SizeCode, str] = {
    SizeCode.S: "Small",
    SizeCode.M: "Medium",
    SizeCode.XL: "Extra large",
    SizeCode.ONE: "One size",
}
_SIZE_SORT: dict[SizeCode, int] = {SizeCode.S: 0, SizeCode.M: 1, SizeCode.XL: 2, SizeCode.ONE: 3}


class ProposalNotFound(LookupError):
    """No proposal by that name. The message lists the closest available names."""


class AmbiguousProposal(LookupError):
    """More than one proposal has this name. They are different recipes."""


class ProposalHasConflicts(ValueError):
    """The legacy rows disagree about a quantity. A human adjudicates, not this code."""


class ProposalAlreadyMaterialised(ValueError):
    """A template with this name already exists. Edit it rather than duplicating it."""


@dataclass
class MaterialisationReport:
    proposal_name: str
    template_id: int | None = None
    sizes: tuple[str, ...] = ()
    components: int = 0
    axis_filled_slots: int = 0
    axes: int = 0
    options: int = 0
    items_repointed: int = 0
    items_unresolved_option: list[str] = field(default_factory=list)
    items_skipped_other_template: list[str] = field(default_factory=list)
    items_not_found: list[str] = field(default_factory=list)
    manual_lines_closed: int = 0
    missing_ingredients: list[str] = field(default_factory=list)
    accepted_conflicts: int = 0
    rollup: RollupReport | None = None
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"template {self.proposal_name!r} (#{self.template_id})",
            f"{len(self.sizes)} size(s) {'/'.join(self.sizes)}",
            f"{self.components} component(s) incl. {self.axis_filled_slots} variant-filled",
            f"{self.axes} axis/axes with {self.options} option(s)",
            f"{self.items_repointed} menu item(s) re-pointed",
        ]
        if self.items_skipped_other_template:
            parts.append(f"{len(self.items_skipped_other_template)} already on another template")
        if self.items_not_found:
            parts.append(f"{len(self.items_not_found)} menu item row(s) not found")
        if self.manual_lines_closed:
            parts.append(f"{self.manual_lines_closed} manual recipe line(s) closed")
        return "; ".join(parts)


# --------------------------------------------------------------------------
# Reading the proposals back out of staging
# --------------------------------------------------------------------------


def staged_lines(session: Session) -> list[StagedLine]:
    """Rebuild pattern detection's input from `legacy_staged_recipe`.

    The proposals are not persisted -- only the staged rows they were derived from
    are. Recomputing is deterministic and gives the same 27 proposals the import
    review printed, which is the point: what a human confirmed is what gets written.
    """
    rows = session.scalars(select(LegacyStagedRecipe).order_by(LegacyStagedRecipe.id))
    return [
        StagedLine(
            recipe_no=row.recipe_no,
            item_name=row.item_name,
            size_code=row.size_code,
            category=row.category,
            sell_price_pence=row.sell_price_pence,
            ingredient_name=row.ingredient_name,
            qty=row.qty,
            role=row.role,
        )
        for row in rows
    ]


def list_proposals(session: Session) -> list[TemplateProposal]:
    return propose_templates(staged_lines(session))


def find_proposal(session: Session, key: str) -> TemplateProposal:
    """Resolve a proposal by `proposal_id` or by name.

    The id is tried first and is the only unambiguous handle: detection names a
    group after its defining ingredient, so two structurally different groups can
    share a name. `Flavoured Matcha (Matcha powder)` is two proposals today, 18
    items and 5.

    A name matching more than one proposal now RAISES rather than returning the
    first. It used to silently pick one, which meant confirming a proposal could
    write the wrong recipe onto real menu items with nothing to notice it by.
    """
    proposals = list_proposals(session)
    wanted = key.strip().casefold()

    for proposal in proposals:
        if proposal.proposal_id == wanted:
            return proposal

    exact = [p for p in proposals if p.name.casefold() == wanted]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise AmbiguousProposal(
            f"{len(exact)} proposals are named {key!r} and they are different recipes "
            f"({', '.join(f'{p.proposal_id} = {p.menu_item_count} items' for p in exact)}). "
            "Confirming by name could write the wrong one, so pass the proposal id."
        )

    near = [p for p in proposals if wanted in p.name.casefold()]
    if len(near) == 1:
        return near[0]
    candidates = [p.name for p in (near or [p for p in proposals if not p.is_singleton])][:10]
    raise ProposalNotFound(f"no proposal named {key!r}. Candidates: " + "; ".join(candidates))


# --------------------------------------------------------------------------
# Materialisation
# --------------------------------------------------------------------------


def materialise_proposal(
    session: Session,
    name: str,
    *,
    actor: str,
    effective_from: datetime | None = None,
    allow_conflicts: bool = False,
) -> MaterialisationReport:
    """Write one confirmed proposal into real composition rows. One transaction."""
    effective_from = require_not_retroactive(effective_from or datetime.now(UTC))
    proposal = find_proposal(session, name)
    report = MaterialisationReport(proposal_name=proposal.name)

    if proposal.is_hollow:
        raise ProposalHasConflicts(
            f"{proposal.name!r} has no components and no axes -- there is no recipe in it "
            "to write. Confirming it would create an empty template AND take its "
            f"{proposal.menu_item_count} menu item(s) off the manual recipes they resolve "
            "through today, so they would cost and deplete nothing. The legacy rows for "
            "this group carry no ingredient lines; fix the import, not the template."
        )
    if proposal.is_singleton:
        raise ProposalHasConflicts(
            f"{proposal.name!r} covers a single base item, which is not a pattern. "
            "Leave it as a manual recipe -- that is the right model for a one-off."
        )
    if proposal.conflicts and not allow_conflicts:
        detail = "; ".join(c.describe() for c in proposal.conflicts[:5])
        raise ProposalHasConflicts(
            f"{proposal.name!r} has {len(proposal.conflicts)} unresolved conflict(s): "
            f"{detail}. The legacy rows disagree about a quantity, so a human must say "
            "which is right. To accept the lowest quantity at each size and have that "
            "choice recorded, pass allow_conflicts=True (CLI: --allow-conflicts)."
        )
    if session.scalar(select(DrinkTemplate.id).where(DrinkTemplate.name == proposal.name)):
        raise ProposalAlreadyMaterialised(
            f"a template named {proposal.name!r} already exists; edit its components "
            "instead of materialising the proposal a second time"
        )

    if proposal.conflicts:
        report.accepted_conflicts = len(proposal.conflicts)
        report.warnings.append(
            f"{len(proposal.conflicts)} conflict(s) ACCEPTED by {actor}: the lowest "
            "quantity at each size was written. The legacy rows disagreed and this "
            "choice is arbitrary -- verify these quantities against the real recipes."
        )

    try:
        _build(session, proposal, report, effective_from=effective_from, actor=actor)
        session.flush()
        report.rollup = rollup_for_template(
            session,
            _require_template_id(report),
            at=effective_from,
            trigger=f"template {proposal.name!r} materialised by {actor}",
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return report


def _require_template_id(report: MaterialisationReport) -> int:
    if report.template_id is None:  # pragma: no cover -- _build always sets it
        raise RuntimeError("template was not created")
    return report.template_id


def _build(
    session: Session,
    proposal: TemplateProposal,
    report: MaterialisationReport,
    *,
    effective_from: datetime,
    actor: str,
) -> None:
    ingredients = _ingredients_by_name(session)
    staged = [line for line in staged_lines(session) if line.item_name in proposal.base_item_names]

    template = DrinkTemplate(
        name=proposal.name,
        category=proposal.category,
        description=(
            f"Materialised from legacy pattern detection by {actor} on "
            f"{effective_from.date().isoformat()}: {proposal.menu_item_count} menu item "
            f"rows across {len(proposal.base_item_names)} base items."
        ),
        is_active=True,
    )
    session.add(template)
    session.flush()
    report.template_id = template.id

    # -- sizes ------------------------------------------------------------
    codes = sorted(
        {code for raw in proposal.sizes if (code := _size_code(raw)) is not None},
        key=lambda c: _SIZE_SORT.get(c, 9),
    )
    for order, code in enumerate(codes):
        session.add(
            SizeProfile(
                template_id=template.id,
                code=code,
                label=_SIZE_LABELS.get(code, code.value),
                sort_order=order,
            )
        )
    report.sizes = tuple(c.value for c in codes)

    # -- fixed and variant-filled slots -----------------------------------
    axis_filled_roles: set[ComponentRole] = set()
    for component in proposal.components:
        qty_by_size = _normalise_sizes(component.qty_by_size)
        if component.ingredient_name is None:
            # The slot a variant axis fills: no ingredient, only a default quantity.
            session.add(
                TemplateComponent(
                    template_id=template.id,
                    role=component.role,
                    ingredient_id=None,
                    qty_by_size=qty_by_size,
                    is_substitutable=False,
                    is_required=component.role in AXIS_CANDIDATE_ROLES,
                    effective_from=effective_from,
                    note="filled by a variant axis",
                )
            )
            report.components += 1
            report.axis_filled_slots += 1
            axis_filled_roles.add(component.role)
            continue

        ingredient = ingredients.get(component.ingredient_name)
        if ingredient is None:
            report.missing_ingredients.append(component.ingredient_name)
            continue
        session.add(
            TemplateComponent(
                template_id=template.id,
                role=component.role,
                ingredient_id=ingredient.id,
                qty_by_size=qty_by_size,
                is_substitutable=component.role in SUBSTITUTABLE_ROLES,
                # Packaging is size-determined: the 8oz cup slot legitimately has no
                # quantity at M or XL, and requiring one would warn on every large
                # drink (ARCHITECTURE 7.5).
                is_required=component.role not in SIZE_DETERMINED_ROLES,
                effective_from=effective_from,
            )
        )
        report.components += 1

    # -- axes and options -------------------------------------------------
    option_ids_by_ingredient: dict[int, dict[int, int]] = {}  # axis_id -> ing_id -> option_id
    axis_ids: list[int] = []
    per_option_qty = _option_quantities(staged)

    for order, axis_proposal in enumerate(proposal.axes):
        axis = VariantAxis(
            template_id=template.id,
            name=axis_proposal.name,
            role=axis_proposal.role,
            is_required=axis_proposal.role in AXIS_CANDIDATE_ROLES,
            sort_order=order,
        )
        session.add(axis)
        session.flush()
        axis_ids.append(axis.id)
        report.axes += 1
        option_ids_by_ingredient[axis.id] = {}

        has_slot = axis_proposal.role in axis_filled_roles
        for label, ingredient_name in sorted(axis_proposal.options.items()):
            ingredient = ingredients.get(ingredient_name)
            if ingredient is None:
                report.missing_ingredients.append(ingredient_name)
                continue
            own_qty = _normalise_sizes(
                per_option_qty.get((axis_proposal.role, ingredient_name), {})
            )
            if not own_qty and not has_slot:
                # No slot to inherit from and no quantity of its own: this option
                # would resolve to nothing. Say so rather than writing a dead row.
                report.warnings.append(
                    f"option {label!r} on axis {axis_proposal.name!r} has no quantity at any "
                    "size and no slot to inherit from; it will resolve to nothing"
                )
            option = VariantOption(
                axis_id=axis.id,
                name=label,
                ingredient_id=ingredient.id,
                # Per-option quantities come from the legacy rows for that exact
                # syrup, which is more faithful than one quantity for the axis: some
                # syrups are poured at 20ml and some at 30ml.
                qty_by_size=own_qty or None,
                price_delta_pence=0,
                effective_from=effective_from,
            )
            session.add(option)
            session.flush()
            report.options += 1
            option_ids_by_ingredient[axis.id][ingredient.id] = option.id

    if report.missing_ingredients:
        report.warnings.append(
            f"{len(set(report.missing_ingredients))} ingredient name(s) in the proposal are "
            f"not in the ingredient table, so those slots were skipped: "
            f"{sorted(set(report.missing_ingredients))[:8]}"
        )

    # -- re-point the menu items ------------------------------------------
    _repoint_items(
        session,
        proposal,
        report,
        staged=staged,
        ingredients=ingredients,
        template_id=template.id,
        axis_roles={axis_id: p.role for axis_id, p in zip(axis_ids, proposal.axes, strict=True)},
        option_ids_by_ingredient=option_ids_by_ingredient,
        effective_from=effective_from,
    )


def _repoint_items(
    session: Session,
    proposal: TemplateProposal,
    report: MaterialisationReport,
    *,
    staged: list[StagedLine],
    ingredients: dict[str, Ingredient],
    template_id: int,
    axis_roles: dict[int, ComponentRole],
    option_ids_by_ingredient: dict[int, dict[int, int]],
    effective_from: datetime,
) -> None:
    """Attach each (base item x size) row to the template and clear `manual_recipe`."""
    # base item -> role -> ingredient names it actually uses
    by_item_role: dict[str, dict[ComponentRole, set[str]]] = defaultdict(lambda: defaultdict(set))
    sizes_by_item: dict[str, set[str]] = defaultdict(set)
    for line in staged:
        sizes_by_item[line.item_name].add(_size_key(line.size_code))
        if line.role is not None:
            by_item_role[line.item_name][line.role].add(line.ingredient_name)

    for item_name in proposal.base_item_names:
        selected = _selected_options(
            item_name,
            by_item_role[item_name],
            axis_roles=axis_roles,
            ingredients=ingredients,
            option_ids_by_ingredient=option_ids_by_ingredient,
            report=report,
        )
        for size_key in sorted(sizes_by_item[item_name]):
            code = _size_code(size_key)
            item = session.scalar(
                select(MenuItem).where(MenuItem.name == item_name, MenuItem.size_code == code)
            )
            label = f"{item_name} [{size_key}]"
            if item is None:
                report.items_not_found.append(label)
                continue
            if item.template_id is not None and item.template_id != template_id:
                # Stealing it would leave its past sales resolving against components
                # that only begin today -- history rewritten by omission (invariant 3).
                report.items_skipped_other_template.append(label)
                continue

            item.template_id = template_id
            item.selected_options = {str(axis_id): oid for axis_id, oid in selected.items()}
            item.manual_recipe = False
            report.items_repointed += 1
            report.manual_lines_closed += _close_manual_lines(session, item.id, effective_from)

    if report.items_skipped_other_template:
        report.warnings.append(
            f"{len(report.items_skipped_other_template)} menu item row(s) already belong to "
            "another template and were left alone, because re-pointing them would change "
            "what their past sales are deemed to have consumed (invariant 3): "
            f"{report.items_skipped_other_template[:6]}"
        )
    if report.items_not_found:
        report.warnings.append(
            f"{len(report.items_not_found)} (item, size) row(s) in the proposal have no "
            f"menu_item row: {report.items_not_found[:6]}"
        )
    if report.items_unresolved_option:
        report.warnings.append(
            f"{len(report.items_unresolved_option)} item(s) could not be matched to a variant "
            f"option and will resolve without it: {report.items_unresolved_option[:6]}"
        )


def _selected_options(
    item_name: str,
    roles: dict[ComponentRole, set[str]],
    *,
    axis_roles: dict[int, ComponentRole],
    ingredients: dict[str, Ingredient],
    option_ids_by_ingredient: dict[int, dict[int, int]],
    report: MaterialisationReport,
) -> dict[int, int]:
    """Which option on each axis this base item uses, read from its legacy rows."""
    selected: dict[int, int] = {}
    for axis_id, role in axis_roles.items():
        names = sorted(roles.get(role, set()))
        matched: list[int] = []
        for name in names:
            ingredient = ingredients.get(name)
            if ingredient is None:
                continue
            option_id = option_ids_by_ingredient.get(axis_id, {}).get(ingredient.id)
            if option_id is not None:
                matched.append(option_id)
        if not matched:
            report.items_unresolved_option.append(f"{item_name} ({role.value})")
            continue
        if len(matched) > 1:
            report.warnings.append(
                f"{item_name!r} uses {len(matched)} different {role.value} ingredients; "
                "one axis holds one choice, so the first was selected and the rest are "
                "not modelled -- this item probably wants its own template"
            )
        selected[axis_id] = matched[0]
    return selected


def _close_manual_lines(session: Session, menu_item_id: int, effective_from: datetime) -> int:
    """Close any live manual recipe lines rather than deleting them.

    The item now resolves through a template. Its old manual lines stay on record and
    keep their effective window, so a sale dated before today still resolves the way
    it did when it happened (invariant 3).
    """
    rows = list(
        session.scalars(
            select(ManualRecipeLine).where(
                ManualRecipeLine.menu_item_id == menu_item_id,
                or_(
                    ManualRecipeLine.effective_to.is_(None),
                    ManualRecipeLine.effective_to > effective_from,
                ),
            )
        )
    )
    for row in rows:
        row.effective_to = effective_from
    return len(rows)


# --------------------------------------------------------------------------
# Quantities and sizes
# --------------------------------------------------------------------------


def _option_quantities(staged: list[StagedLine]) -> dict[tuple[ComponentRole, str], dict[str, str]]:
    """Per-(role, ingredient) quantity by size, from the legacy rows of this group.

    Where members disagree at one size the LOWEST value wins, which is the same
    tie-break `seed/patterns.py` applies to fixed components. It is arbitrary, and
    that is why a proposal with conflicts is refused by default.
    """
    seen: dict[tuple[ComponentRole, str], dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for line in staged:
        if line.role is None:
            continue
        seen[(line.role, line.ingredient_name)][_size_key(line.size_code)].add(
            format(line.qty, "f")
        )
    out: dict[tuple[ComponentRole, str], dict[str, str]] = {}
    for key, per_size in seen.items():
        out[key] = {
            size: min(values, key=lambda v: Decimal(v)) for size, values in per_size.items()
        }
    return out


def _size_key(raw: str | None) -> str:
    """Workbook size text -> a `SizeCode` VALUE.

    This normalisation is load-bearing. Pattern detection keys quantities by the raw
    workbook text, so a one-size item arrives as `"One"`, while `qty_by_size` is read
    back with `SizeCode.ONE.value` == `"ONE"`. Writing the raw text into the JSON
    would make every one-size recipe resolve to nothing at all.
    """
    code = _size_code(raw)
    return (code or SizeCode.ONE).value


def _size_code(raw: str | None) -> SizeCode | None:
    if raw is None:
        return SizeCode.ONE
    key = str(raw).strip().upper()
    if key in ("", "ONE", "ONE SIZE"):
        return SizeCode.ONE
    try:
        return SizeCode(key)
    except ValueError:
        return None


def _normalise_sizes(qty_by_size: dict[str, str]) -> dict[str, str]:
    return {_size_key(size): qty for size, qty in qty_by_size.items()}


def _ingredients_by_name(session: Session) -> dict[str, Ingredient]:
    return {row.name: row for row in session.scalars(select(Ingredient))}
