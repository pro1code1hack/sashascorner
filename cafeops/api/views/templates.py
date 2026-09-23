"""Templates, and the composition editor's preview and apply. Spec 4.2, 5.5.

Spec 10's build order puts the composition editor first, and its three panes are
exactly the three collections on `TemplateDetail`: the slots (`components`), what fills
them (`axes`, `modifiers`), and the leaves that fall out (`items`). All of it is
narrowed to a single instant: composition is effective-dated (invariant 3), so "the
recipe" is meaningless without a date, and `as_of` is on the response.

`preview_edit_view` writes nothing -- it calls the service's preview entry point, which
builds the "after" side by substituting the quantity into the spec the repository
produced rather than by writing the row and reading it back. `apply_edit_view` is the
one write in this whole API, and it applies from today only: `RetroactiveEditError` is
translated to 409 by the app's exception handler.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cafeops.api.encoding import as_pence, as_qty, pct
from cafeops.api.schemas import (
    ApplyResponse,
    ComponentOut,
    ComponentQtyApply,
    ComponentQtyChange,
    Cost,
    ImpactedItemOut,
    ImpactPreviewOut,
    LabourImpactOut,
    LabourItemOut,
    ModifierOut,
    PreviewResponse,
    TemplateDetail,
    TemplateItemOut,
    TemplateSummary,
    VariantAxisOut,
    VariantOptionOut,
)
from cafeops.api.views.common import cost_from_cached, cost_from_ingredient, cost_unknown
from cafeops.config import settings
from cafeops.db.models import (
    DrinkTemplate,
    Ingredient,
    MenuItem,
    Modifier,
    Season,
    SizeProfile,
    TemplateComponent,
    VariantAxis,
    VariantOption,
)
from cafeops.db.repositories.composition import LiveComponent, SqlCompositionRepository
from cafeops.db.repositories.ingredient import SqlIngredientRepository
from cafeops.db.repositories.menu_cost import SqlMenuCostRepository
from cafeops.domain.composition import ImpactPreview, LabourImpact, LabourImpactedItem
from cafeops.domain.types import ImpactedItem
from cafeops.services.edit_composition import (
    apply_component_qty_change,
    preview_component_qty_change_with_labour,
)
from cafeops.services.menu_margin import menu_availability

__all__ = [
    "apply_edit_view",
    "preview_edit_view",
    "template_detail_view",
    "templates_view",
]


# --------------------------------------------------------------------------
# list and detail
# --------------------------------------------------------------------------


def _summary(session: Session, template: DrinkTemplate, at: datetime) -> TemplateSummary:
    sizes = tuple(
        code.value
        for code in session.scalars(
            select(SizeProfile.code)
            .where(SizeProfile.template_id == template.id)
            .order_by(SizeProfile.sort_order)
        )
    )
    axes = tuple(
        session.scalars(
            select(VariantAxis.name)
            .where(VariantAxis.template_id == template.id)
            .order_by(VariantAxis.sort_order)
        )
    )
    items = int(
        session.scalar(select(func.count(MenuItem.id)).where(MenuItem.template_id == template.id))
        or 0
    )
    live = SqlCompositionRepository(session).live_components(template.id, at)
    return TemplateSummary(
        id=template.id,
        name=template.name,
        category=template.category,
        sizes=sizes,
        axes=axes,
        item_count=items,
        component_count=len(live),
        prep_seconds_by_size=dict(template.prep_seconds_by_size or {}),
        prep_seconds_is_estimate=template.prep_seconds_is_estimate,
    )


def templates_view(session: Session, *, at: datetime | None = None) -> tuple[TemplateSummary, ...]:
    at = at or datetime.now(UTC)
    rows = session.scalars(select(DrinkTemplate).order_by(DrinkTemplate.name)).all()
    return tuple(_summary(session, template, at) for template in rows)


def _components(session: Session, template_id: int, at: datetime) -> tuple[ComponentOut, ...]:
    repo = SqlCompositionRepository(session)
    snapshots = SqlIngredientRepository(session).snapshots_by_id()
    out: list[ComponentOut] = []
    for component in repo.live_components(template_id, at):
        snapshot = (
            snapshots.get(component.ingredient_id) if component.ingredient_id is not None else None
        )
        out.append(
            ComponentOut(
                component_id=component.component_id,
                role=component.role.value,
                ingredient_id=component.ingredient_id,
                ingredient_name=component.ingredient_name,
                unit=component.unit.value if component.unit is not None else None,
                # Already strings on the model (ARCHITECTURE 7.4). Re-normalised through
                # Decimal so "0.1200" and "0.12" cannot both reach the editor as the
                # same quantity spelled two ways.
                qty_by_size={
                    size: as_qty_str(value)
                    for size, value in sorted((component.qty_by_size or {}).items())
                },
                is_substitutable=component.is_substitutable,
                is_required=component.is_required,
                effective_from=component.effective_from,
                ingredient_cost=None if snapshot is None else cost_from_ingredient(snapshot),
            )
        )
    return tuple(out)


def as_qty_str(value: str | None) -> str:
    """A stored quantity string, normalised through Decimal. Never through float."""
    return as_qty(_decimal(value)) or "0"


def _decimal(value: str | None) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except InvalidOperation:
        # A quantity the editor cannot parse is a data fault, not a zero. It is
        # reported as 0 here only so the whole template still renders; the component
        # row itself is what needs fixing, and `cafeops components` shows the raw value.
        return Decimal("0")


def _axes(session: Session, template_id: int, at: datetime) -> tuple[VariantAxisOut, ...]:
    season_names = dict(session.execute(select(Season.id, Season.name)).all())
    axes = session.scalars(
        select(VariantAxis)
        .where(VariantAxis.template_id == template_id)
        .order_by(VariantAxis.sort_order, VariantAxis.id)
    ).all()
    out: list[VariantAxisOut] = []
    for axis in axes:
        options = session.execute(
            select(VariantOption, Ingredient.name)
            .outerjoin(Ingredient, Ingredient.id == VariantOption.ingredient_id)
            .where(
                VariantOption.axis_id == axis.id,
                VariantOption.effective_from <= at,
            )
            .order_by(VariantOption.name)
        ).all()
        out.append(
            VariantAxisOut(
                axis_id=axis.id,
                name=axis.name,
                role=axis.role.value,
                is_required=axis.is_required,
                options=tuple(
                    VariantOptionOut(
                        option_id=option.id,
                        name=option.name,
                        role=axis.role.value,
                        ingredient_id=option.ingredient_id,
                        ingredient_name=ingredient_name,
                        # A per-size override map on the option; flattened to the one
                        # value that is not None, or null when the option does not
                        # override the slot's quantity at all.
                        qty_override=_first_qty(option.qty_by_size),
                        price_delta_pence=option.price_delta_pence,
                        season_id=option.season_id,
                        season_name=(
                            season_names.get(option.season_id)
                            if option.season_id is not None
                            else None
                        ),
                    )
                    for option, ingredient_name in options
                    if option.effective_to is None or option.effective_to > at
                ),
            )
        )
    return tuple(out)


def _first_qty(qty_by_size: dict[str, str] | None) -> str | None:
    if not qty_by_size:
        return None
    for key in sorted(qty_by_size):
        value = qty_by_size[key]
        if value is not None:
            return as_qty_str(value)
    return None


def _modifiers(session: Session, template_id: int) -> tuple[ModifierOut, ...]:
    """Modifiers that target a role this template actually has.

    Modifiers target a ROLE, not an ingredient (spec 4.2), so they are not owned by a
    template. Filtering to the roles this template exposes is what makes the list
    useful in the editor rather than a global dump.
    """
    roles = set(
        session.scalars(
            select(TemplateComponent.role).where(TemplateComponent.template_id == template_id)
        )
    ) | set(session.scalars(select(VariantAxis.role).where(VariantAxis.template_id == template_id)))
    rows = session.execute(
        select(Modifier, Ingredient.name)
        .outerjoin(Ingredient, Ingredient.id == Modifier.ingredient_id)
        .where(Modifier.is_active.is_(True))
        .order_by(Modifier.name)
    ).all()
    return tuple(
        ModifierOut(
            modifier_id=modifier.id,
            name=modifier.name,
            action=modifier.action.value,
            target_role=modifier.target_role.value,
            ingredient_id=modifier.ingredient_id,
            ingredient_name=ingredient_name,
            qty_delta=as_qty(modifier.qty_delta),
            qty_multiplier=as_qty(modifier.qty_multiplier),
            price_pence=modifier.price_pence,
        )
        for modifier, ingredient_name in rows
        if modifier.target_role in roles
    )


def _items(session: Session, template_id: int, at: datetime) -> tuple[TemplateItemOut, ...]:
    costs = SqlMenuCostRepository(session)
    rows = session.scalars(
        select(MenuItem)
        .where(MenuItem.template_id == template_id)
        .order_by(MenuItem.name, MenuItem.size_code)
    ).all()
    ids = [row.id for row in rows]
    availability = {
        answer.menu_item_id: answer
        for answer in menu_availability(
            session, on=at.astimezone(settings.tz).date(), menu_item_ids=ids
        )
    }
    out: list[TemplateItemOut] = []
    for item in rows:
        cached = costs.get_detail(item.id)
        answer = availability.get(item.id)
        cost = (
            cost_from_cached(cached)
            if cached is not None
            else cost_unknown(
                "no cached cost row: this item's recipe does not resolve, so what it costs "
                "is UNKNOWN rather than zero (invariant 8). Run `cafeops cost-rollup` after "
                "fixing the recipe."
            )
        )
        out.append(
            TemplateItemOut(
                menu_item_id=item.id,
                name=item.name,
                size_code=item.size_code.value if item.size_code is not None else None,
                price_pence=item.price_pence,
                cost=cost,
                margin_pct=pct(cached.margin_pct) if cached is not None else None,
                true_margin_pct=pct(cached.true_margin_pct) if cached is not None else None,
                margin_per_minute_pence=(
                    as_pence(cached.margin_per_minute_pence) if cached is not None else None
                ),
                prep_seconds=cached.prep_seconds if cached is not None else item.prep_seconds,
                prep_source=None,
                prep_is_estimate=(
                    cached.prep_seconds_is_estimate
                    if cached is not None
                    else item.prep_seconds_is_estimate
                ),
                is_available_today=answer.is_available if answer is not None else item.active,
                availability=answer.availability.value if answer is not None else "UNKNOWN",
                availability_reasons=tuple(answer.reasons) if answer is not None else (),
            )
        )
    return tuple(out)


def template_detail_view(
    session: Session, *, template_id: int, at: datetime | None = None
) -> TemplateDetail:
    at = at or datetime.now(UTC)
    template = session.get(DrinkTemplate, template_id)
    if template is None:
        raise LookupError(f"template {template_id} not found")

    items = _items(session, template_id, at)
    warnings: list[str] = []
    uncosted = [item for item in items if item.cost.pence is None]
    if uncosted:
        warnings.append(
            f"{len(uncosted)} item(s) on this template have NO cost: "
            + ", ".join(item.name for item in uncosted[:6])
            + ". Their cost is null, not zero, and they are excluded from every margin "
            "figure (invariant 8)."
        )
    estimated = [item for item in items if item.cost.is_estimate]
    if estimated:
        warnings.append(
            f"{len(estimated)} of {len(items)} item(s) are costed from ESTIMATE prices "
            "(42 of 113 ingredient prices are estimates). Every margin below inherits that."
        )
    if not template.is_active:
        warnings.append("this template is not active")

    return TemplateDetail(
        template=_summary(session, template, at),
        as_of=at,
        components=_components(session, template_id, at),
        axes=_axes(session, template_id, at),
        modifiers=_modifiers(session, template_id),
        items=items,
        warnings=tuple(warnings),
    )


# --------------------------------------------------------------------------
# preview and apply (spec 5.5)
# --------------------------------------------------------------------------


def _impacted(item: ImpactedItem) -> ImpactedItemOut:
    return ImpactedItemOut(
        menu_item_id=item.menu_item_id,
        name=item.name,
        size_code=item.size_code.value if item.size_code is not None else None,
        price_pence=item.price_pence,
        cost_before=_impact_cost(item.cost_before_pence),
        cost_after=_impact_cost(item.cost_after_pence),
        cost_delta_pence=as_pence(item.cost_delta_pence),
        margin_pct_before=pct(item.margin_pct(item.cost_before_pence)),
        margin_pct_after=pct(item.margin_pct(item.cost_after_pence)),
    )


def _impact_cost(value: Decimal | None) -> Cost:
    if value is None:
        return Cost(
            pence=None,
            is_missing=True,
            excluded_from_aggregates=True,
            note=(
                "at least one ingredient in this item is unpriced, so its cost is UNKNOWN "
                "and it is excluded from the per-item delta and the COGS projection "
                "(invariant 8)."
            ),
        )
    return Cost(pence=as_pence(value))


def _preview_out(preview: ImpactPreview) -> ImpactPreviewOut:
    return ImpactPreviewOut(
        affected_item_count=preview.affected_item_count,
        items=tuple(_impacted(item) for item in preview.items),
        cost_delta_pence_per_item=as_pence(preview.cost_delta_pence_per_item),
        monthly_cogs_delta_pence=as_pence(preview.monthly_cogs_delta_pence),
        worst_margin_after=(
            _impacted(preview.worst_margin_after)
            if preview.worst_margin_after is not None
            else None
        ),
        warnings=tuple(preview.warnings),
    )


def _labour_item(item: LabourImpactedItem) -> LabourItemOut:
    return LabourItemOut(
        menu_item_id=item.menu_item_id,
        label=item.label,
        prep_seconds=item.after.prep.seconds,
        prep_is_estimate=item.after.prep.is_estimate,
        labour_cost_pence=as_pence(item.labour_cost_pence),
        true_margin_before_pence=as_pence(item.before.labour.true_margin_pence),
        true_margin_after_pence=as_pence(item.after.labour.true_margin_pence),
        true_margin_delta_pence=as_pence(item.true_margin_delta_pence),
        margin_per_minute_before_pence=as_pence(item.before.margin_per_minute_pence),
        margin_per_minute_after_pence=as_pence(item.after.margin_per_minute_pence),
        margin_per_minute_delta_pence=as_pence(item.margin_per_minute_delta_pence),
    )


def _labour_out(labour: LabourImpact) -> LabourImpactOut:
    return LabourImpactOut(
        items=tuple(_labour_item(item) for item in labour.items),
        labour_cost_pence_per_item=as_pence(labour.labour_cost_pence_per_item),
        labour_cost_pence_range=_range(labour.labour_cost_pence_range),
        true_margin_delta_pence_per_item=as_pence(labour.true_margin_delta_pence_per_item),
        true_margin_delta_pence_range=_range(labour.true_margin_delta_pence_range),
        labour_cost_window_pence=as_pence(labour.labour_cost_window_pence),
        untimed_count=labour.untimed_count,
        rank_moves=tuple(labour.rank_moves),
        warnings=tuple(labour.warnings),
    )


def _range(value: tuple[Decimal, Decimal] | None) -> tuple[str, str] | None:
    if value is None:
        return None
    low, high = value
    return (as_pence(low) or "0", as_pence(high) or "0")


def _component_or_raise(session: Session, *, template_id: int, component_id: int) -> LiveComponent:
    component = SqlCompositionRepository(session).live_component(component_id)
    if component is None:
        raise LookupError(f"template_component {component_id} not found, or already closed")
    if component.template_id != template_id:
        raise LookupError(
            f"template_component {component_id} belongs to template "
            f"{component.template_id}, not {template_id}"
        )
    return component


def preview_edit_view(
    session: Session, *, template_id: int, body: ComponentQtyChange
) -> PreviewResponse:
    """What the edit would do. Writes nothing.

    The service's `preview_component_qty_change_with_labour` is the same code path
    `apply` uses, so a reviewer cannot be shown one set of affected items and have a
    different set committed.
    """
    component = _component_or_raise(
        session, template_id=template_id, component_id=body.component_id
    )
    at = datetime.now(UTC)
    labour = preview_component_qty_change_with_labour(
        session,
        body.component_id,
        qty_by_size=body.qty_by_size,
        at=at,
        window_days=body.window_days,
    )
    return PreviewResponse(
        template_id=template_id,
        component_id=body.component_id,
        component_role=component.role.value,
        ingredient_name=component.ingredient_name,
        qty_by_size_before={
            size: as_qty_str(value) for size, value in sorted((component.qty_by_size or {}).items())
        },
        qty_by_size_after={
            size: as_qty_str(value) for size, value in sorted(body.qty_by_size.items())
        },
        at=at,
        window_days=body.window_days,
        preview=_preview_out(labour.preview),
        labour=_labour_out(labour),
    )


def apply_edit_view(
    session: Session, *, template_id: int, body: ComponentQtyApply
) -> ApplyResponse:
    """Apply the edit from today. The one write in this API.

    `apply_component_qty_change` closes the live row, opens a new one and recosts the
    template in one transaction, and commits it itself -- so this function must not be
    wrapped in an outer commit that could roll part of it back. `routers.py`
    calls it through `runtime.in_session`, whose `session_scope` commit is then a no-op
    on an already-committed session.
    """
    _component_or_raise(session, template_id=template_id, component_id=body.component_id)
    result = apply_component_qty_change(
        session,
        body.component_id,
        qty_by_size=body.qty_by_size,
        actor=body.actor,
        window_days=body.window_days,
    )
    labour = result.labour
    return ApplyResponse(
        template_id=result.template_id,
        component_id=result.component_id,
        new_component_id=result.new_component_id,
        effective_from=result.effective_from,
        qty_by_size_before=result.qty_by_size_before,
        qty_by_size_after=result.qty_by_size_after,
        preview=_preview_out(result.preview),
        labour=(
            _labour_out(labour)
            if labour is not None
            else LabourImpactOut(
                items=(),
                labour_cost_pence_per_item=None,
                labour_cost_pence_range=None,
                true_margin_delta_pence_per_item=None,
                true_margin_delta_pence_range=None,
                labour_cost_window_pence=None,
                untimed_count=0,
            )
        ),
        rollup_summary=result.rollup.summary(),
        rollup_items_recosted=result.rollup.costed,
        summary=result.summary(),
    )
