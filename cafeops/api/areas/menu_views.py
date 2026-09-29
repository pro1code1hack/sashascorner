"""Views for the Recipes, Menu items and Ingredients screens. Reads here; writes delegate.

Reads query the database directly and build finished response models inside the
worker thread (`runtime.in_session`), like every other view. Writes go through the
services (`recipe_changeset`, `menu_catalog`, `ingredient_catalog`,
`modifier_versions`, `media_store`, `materialise_template`), which own the
transaction; nothing here writes a row itself (CLAUDE.md 8).

Every `Cost` is built by `_cost` or `views.common`, so a missing cost is `null` and
flagged and never `0` (invariant 8). Waste factor never appears here: menu cost is
recipe quantities only (invariant 7).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from cafeops.api.areas.menu_schemas import (
    ChangeImpactOut,
    ChangeItemOut,
    ChangesetAppliedOut,
    ChangesetApplyIn,
    ChangesetIn,
    ChangesetPreviewOut,
    ChangeStatus,
    EditorAxisOut,
    EditorComponentOut,
    EditorItemOut,
    EditorOptionOut,
    HistoryEntryOut,
    IngredientAllergensIn,
    IngredientAllergensOut,
    IngredientCreateIn,
    IngredientDetailOut,
    IngredientMetaIn,
    IngredientPhotoOut,
    IngredientPriceAppliedOut,
    IngredientPriceApplyIn,
    IngredientPriceIn,
    IngredientPricePreviewIn,
    IngredientPricePreviewOut,
    IngredientPriceRowOut,
    IngredientRowOut,
    IngredientsResponse,
    IngredientSupplierOut,
    IngredientWriteOut,
    ItemSaleOut,
    ItemSalesOut,
    LineIn,
    LinesAppliedOut,
    LinesPreviewOut,
    LowestMarginOut,
    ManualLinesApplyIn,
    ManualLinesIn,
    MenuCategoryOut,
    MenuGroupIn,
    MenuGroupOut,
    MenuItemCreateIn,
    MenuItemDetailOut,
    MenuItemsResponse,
    MenuKindStr,
    MenuLineOut,
    MenuPricesApplyIn,
    MenuPricesIn,
    MenuSizeIn,
    MenuSizeOut,
    MenuWriteOut,
    OfferOut,
    OneOffOut,
    PackOut,
    PhotoOut,
    PrepIn,
    PrepOut,
    PriceHistoryOut,
    PricesAppliedOut,
    PricesPreviewOut,
    ProposalConfirmIn,
    ProposalCostOut,
    ProposalPreviewIn,
    ProposalPreviewOut,
    RecipeEditorOut,
    RecipeHistoryOut,
    RecipeRailProposal,
    RecipeRailTemplate,
    RecipesRailResponse,
    RefusalOut,
    SeasonOut,
    SizeCodeStr,
    SupplierNameOut,
    SwapAppliedOut,
    SwapApplyIn,
    SwapChangeIn,
    SwapOut,
    SwapPreviewOut,
    UsedInOut,
)
from cafeops.api.encoding import as_pence, as_qty, pct
from cafeops.api.params import HTTP_422
from cafeops.api.schemas import Cost, MaterialiseIn, MaterialiseResponse
from cafeops.api.views.common import MISSING_COST_NOTE, cost_from_cached, cost_unknown
from cafeops.api.views.proposals import materialise_proposal_view
from cafeops.config import settings
from cafeops.db.models import (
    DrinkTemplate,
    Ingredient,
    IngredientPrice,
    ManualRecipeLine,
    MediaAsset,
    MenuCategory,
    MenuItem,
    MenuItemPrice,
    Modifier,
    ModifierVersion,
    RecipeChange,
    Sale,
    Season,
    Supplier,
    SupplierProduct,
    TemplateComponent,
    VariantAxis,
    VariantOption,
)
from cafeops.db.repositories.composition import SqlCompositionRepository
from cafeops.db.repositories.menu_cost import CachedCost, SqlMenuCostRepository
from cafeops.domain import composition as dc
from cafeops.domain.composition import (
    SIZE_ORDER,
    ChangeImpact,
    ItemChange,
    base_prices,
    resolve_recipe,
)
from cafeops.domain.enums import MenuKind, Storage
from cafeops.domain.types import (
    ComponentRole,
    ModifierAction,
    PriceSource,
    SeasonSpec,
    SizeCode,
    Unit,
)
from cafeops.domain.units import IncompatibleUnitsError, convert
from cafeops.jobs.cost_rollup import configured_rate_pence, snapshots_at
from cafeops.services import ingredient_catalog as ic
from cafeops.services import menu_catalog as mc
from cafeops.services import modifier_versions as mv
from cafeops.services import recipe_changeset as rc
from cafeops.services.materialise_template import list_proposals, preview_proposal
from cafeops.services.media_store import media_url, store_image
from cafeops.services.menu_margin import menu_availability

_SIZE_RANK = {s: i for i, s in enumerate(SIZE_ORDER)}


def _size_rank(size: SizeCode | None) -> int:
    return _SIZE_RANK.get(size, 9) if size is not None else 9


def _size_str(size: SizeCode | None) -> str | None:
    return None if size is None else size.value


# ==========================================================================
# Shared builders
# ==========================================================================


def _cost(value: Decimal | None, source: PriceSource | None, *, reason: str | None = None) -> Cost:
    """A cost figure with its trust attached. Missing is null and flagged, never 0."""
    if value is None:
        return cost_unknown(reason or MISSING_COST_NOTE)
    return Cost(
        pence=as_pence(value),
        source=source.value if source is not None else None,
        is_estimate=source is PriceSource.ESTIMATE,
        is_missing=False,
        # Estimates are flagged, and included in the deltas that say so (the preview's
        # `estimated_count` and the italic figures); only an UNKNOWN cost is left out.
        excluded_from_aggregates=False,
    )


def _change_out(c: ItemChange) -> ChangeItemOut:
    before_labour = c.labour_before()
    after_labour = c.labour_after()
    return ChangeItemOut(
        key=c.key,
        menu_item_id=c.menu_item_id,
        name=c.name,
        size_code=_size_str(c.size_code),
        status=cast(ChangeStatus, c.status),
        on_till=c.on_till,
        price_before=c.price_before,
        price_after=c.price_after,
        cost_before=(
            _cost(c.cost_before, c.source_before)
            if c.exists_before
            else cost_unknown("did not exist before this change")
        ),
        cost_after=_cost(c.cost_after, c.source_after),
        cost_delta_pence=as_pence(c.cost_delta),
        margin_pct_before=pct(c.margin_before),
        margin_pct_after=pct(c.margin_after),
        labour_cost_pence=as_pence(after_labour.labour_cost_pence),
        true_margin_after_pence=as_pence(after_labour.true_margin_pence),
        margin_per_minute_before_pence=as_pence(before_labour.margin_per_minute_pence),
        margin_per_minute_after_pence=as_pence(after_labour.margin_per_minute_pence),
        prep_seconds=after_labour.prep_seconds,
        prep_is_estimate=after_labour.prep_is_estimate,
        units_sold=as_qty(c.units_sold) or "0",
    )


def _impact_out(impact: ChangeImpact) -> ChangeImpactOut:
    spread = impact.cost_delta_range
    return ChangeImpactOut(
        affected_item_count=impact.affected_count,
        items=tuple(_change_out(c) for c in impact.items),
        cost_delta_pence_per_item=as_pence(impact.cost_delta_per_item),
        cost_delta_pence_range=(
            (as_pence(spread[0]) or "0", as_pence(spread[1]) or "0") if spread else None
        ),
        monthly_cogs_delta_pence=as_pence(impact.monthly_cogs_delta),
        revenue_delta_pence=as_pence(impact.revenue_delta),
        worst_margin_after=(
            _change_out(impact.worst_margin_after) if impact.worst_margin_after else None
        ),
        untimed_count=impact.untimed_count,
        estimated_count=impact.estimated_count,
        window_days=impact.window_days,
        warnings=impact.warnings,
    )


def _qty(raw: str, what: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation as exc:  # pragma: no cover -- the schema pattern refuses it
        raise ValueError(f"{what}: {raw!r} is not a number") from exc


def _qty_map(raw: Mapping[SizeCodeStr, str] | None) -> dict[str, Decimal] | None:
    if raw is None:
        return None
    return {str(k): _qty(v, f"size {k}") for k, v in raw.items()}


def _history(rows: list[RecipeChange]) -> tuple[HistoryEntryOut, ...]:
    return tuple(
        HistoryEntryOut(
            effective_from=r.effective_from,
            actor=r.actor,
            kind=r.change_kind,
            summary=r.summary,
            lines=tuple(r.lines or ()),
        )
        for r in sorted(rows, key=lambda r: (r.effective_from, r.id), reverse=True)
    )


# ==========================================================================
# Recipes
# ==========================================================================


def recipes_rail_view(session: Session) -> RecipesRailResponse:
    templates = session.scalars(select(DrinkTemplate).order_by(DrinkTemplate.name)).all()
    out_t: list[RecipeRailTemplate] = []
    for t in templates:
        sizes = tuple(
            s.value
            for s in session.scalars(
                select(MenuItem.size_code.distinct()).where(MenuItem.template_id == t.id)
            )
            if s is not None
        )
        count = len(
            session.scalars(
                select(MenuItem.id).where(MenuItem.template_id == t.id, MenuItem.active.is_(True))
            ).all()
        )
        out_t.append(
            RecipeRailTemplate(
                template_id=t.id,
                name=t.name,
                category=t.category,
                sizes=tuple(sorted(sizes, key=lambda v: _size_rank(SizeCode(v)))),
                item_count=count,
            )
        )
    existing = {t.name for t in templates}
    patterns = [p for p in list_proposals(session) if not p.is_singleton]
    seen: dict[str, int] = defaultdict(int)
    for p in patterns:
        seen[p.name] += 1
    proposals = []
    for p in sorted(patterns, key=lambda p: (-p.menu_item_count, p.name)):
        if p.name in existing:
            continue
        blocked = None
        if p.is_hollow:
            blocked = (
                "no components and no axes: the imported rows carry no ingredient lines, so "
                "there is no recipe here to confirm"
            )
        elif p.conflicts:
            blocked = f"{len(p.conflicts)} conflict(s): the imported rows disagree about a quantity"
        proposals.append(
            RecipeRailProposal(
                proposal_id=p.proposal_id,
                name=p.name,
                category=p.category,
                menu_item_count=p.menu_item_count,
                is_hollow=p.is_hollow,
                name_is_ambiguous=seen[p.name] > 1,
                blocked_reason=blocked,
            )
        )
    one_offs: dict[str, int] = {}
    for item_id, name in session.execute(
        select(MenuItem.id, MenuItem.name)
        .where(MenuItem.manual_recipe.is_(True))
        .order_by(MenuItem.id)
    ):
        one_offs.setdefault(name, item_id)
    return RecipesRailResponse(
        templates=tuple(out_t),
        proposals=tuple(proposals),
        one_offs=tuple(
            OneOffOut(menu_item_id=i, name=n)
            for n, i in sorted(one_offs.items(), key=lambda kv: kv[0].casefold())
        ),
    )


def _swap_out(
    modifier: Modifier,
    names: dict[int, str],
    roles: dict[ComponentRole, bool] | None,
    version_from: datetime | None,
) -> SwapOut:
    applies = False
    if roles is not None and modifier.target_role in roles:
        applies = modifier.action is not ModifierAction.SUBSTITUTE or roles[modifier.target_role]
    return SwapOut(
        modifier_id=modifier.id,
        name=modifier.name,
        action=modifier.action.value,
        target_role=modifier.target_role.value,
        ingredient_id=modifier.ingredient_id,
        ingredient_name=names.get(modifier.ingredient_id) if modifier.ingredient_id else None,
        qty_delta=as_qty(modifier.qty_delta),
        qty_multiplier=as_qty(modifier.qty_multiplier),
        price_pence=modifier.price_pence,
        price_is_estimate=modifier.price_is_estimate,
        is_active=modifier.is_active,
        applies_here=applies,
        version_from=version_from,
    )


def _version_starts(session: Session) -> dict[int, datetime]:
    return dict(
        session.execute(
            select(ModifierVersion.modifier_id, ModifierVersion.effective_from).where(
                ModifierVersion.effective_to.is_(None)
            )
        )
        .tuples()
        .all()
    )


def recipe_history_view(session: Session, template_id: int) -> RecipeHistoryOut:
    if session.get(DrinkTemplate, template_id) is None:
        raise LookupError(f"recipe {template_id} not found")
    rows = list(
        session.scalars(select(RecipeChange).where(RecipeChange.template_id == template_id))
    )
    baseline = session.scalar(
        select(TemplateComponent.effective_from)
        .where(TemplateComponent.template_id == template_id)
        .order_by(TemplateComponent.effective_from)
        .limit(1)
    )
    return RecipeHistoryOut(template_id=template_id, baseline_from=baseline, entries=_history(rows))


def recipe_editor_view(session: Session, template_id: int) -> RecipeEditorOut:
    at = datetime.now(UTC)
    draft = rc.load_template_draft(session, template_id, at)
    ctx = rc.changeset_context(session, draft)
    snapshots = snapshots_at(session, at)
    names = dict(ctx.ingredient_names)
    seasons = dict(ctx.season_names)

    components: list[EditorComponentOut] = []
    for c in draft.components:
        snap = snapshots.get(c.ingredient_id) if c.ingredient_id is not None else None
        components.append(
            EditorComponentOut(
                component_id=c.component_id or 0,
                role=c.role.value,
                ingredient_id=c.ingredient_id,
                ingredient_name=names.get(c.ingredient_id) if c.ingredient_id else None,
                unit=snap.unit.value if snap is not None else None,
                qty_by_size={k: dc.qty_text(v) for k, v in sorted(c.qty_by_size.items())},
                is_substitutable=c.is_substitutable,
                is_required=c.is_required,
                unit_cost=(
                    _cost(snap.cost_per_unit_pence, snap.cost_source) if snap is not None else None
                ),
            )
        )

    axes: list[EditorAxisOut] = []
    for axis in draft.axes:
        options: list[EditorOptionOut] = []
        for o in draft.options:
            if o.axis_id != axis.axis_id or o.option_id is None:
                continue
            option_items = [it for it in draft.items if o.key in it.option_keys.values()]
            snap = snapshots.get(o.ingredient_id) if o.ingredient_id is not None else None
            options.append(
                EditorOptionOut(
                    option_id=o.option_id,
                    axis_id=o.axis_id,
                    name=o.name,
                    ingredient_id=o.ingredient_id,
                    ingredient_name=names.get(o.ingredient_id) if o.ingredient_id else None,
                    unit=snap.unit.value if snap is not None else None,
                    qty_by_size=(
                        {k: dc.qty_text(v) for k, v in sorted(o.qty_by_size.items())}
                        if o.qty_by_size
                        else None
                    ),
                    price_delta_pence=o.price_delta_pence,
                    season_id=o.season_id,
                    season_name=seasons.get(o.season_id) if o.season_id else None,
                    active=any(it.active for it in option_items),
                    missing_ingredient=o.ingredient_id is None,
                    menu_item_ids={
                        (it.size_code.value if it.size_code else "ONE"): it.menu_item_id or 0
                        for it in option_items
                    },
                )
            )
        axes.append(
            EditorAxisOut(
                axis_id=axis.axis_id, name=axis.name, role=axis.role.value, options=tuple(options)
            )
        )

    costs = SqlMenuCostRepository(session)
    ids = [it.menu_item_id for it in draft.items if it.menu_item_id is not None]
    availability = {
        a.menu_item_id: a
        for a in menu_availability(session, on=at.astimezone(settings.tz).date(), menu_item_ids=ids)
    }
    items: list[EditorItemOut] = []
    for it in draft.items:
        if it.menu_item_id is None:
            continue
        cached = costs.get_detail(it.menu_item_id)
        prep = dc.item_prep(draft, it)
        option_key = next(iter(it.option_keys.values()), None)
        option = draft.option(option_key) if option_key else None
        answer = availability.get(it.menu_item_id)
        items.append(
            EditorItemOut(
                menu_item_id=it.menu_item_id,
                name=it.name,
                size_code=_size_str(it.size_code),
                option_id=option.option_id if option is not None else None,
                price_pence=it.price_pence,
                active=it.active,
                on_till=it.on_till,
                cost=(
                    cost_from_cached(cached)
                    if cached is not None
                    else cost_unknown("no cached cost: the recipe does not resolve for this item")
                ),
                prep_seconds=prep.seconds,
                prep_is_estimate=prep.is_estimate,
                availability=answer.availability.value if answer is not None else "UNKNOWN",
            )
        )

    roles: dict[ComponentRole, bool] = {}
    for c in draft.components:
        roles[c.role] = roles.get(c.role, False) or c.is_substitutable
    for a in draft.axes:
        roles.setdefault(a.role, False)
    starts = _version_starts(session)
    swaps = tuple(
        _swap_out(m, names, roles, starts.get(m.id))
        for m in session.scalars(select(Modifier).order_by(Modifier.name))
    )

    base = base_prices(draft)
    warnings: list[str] = []
    disagree = tuple(size for size, (_v, d) in base.items() if d)
    if disagree:
        warnings.append(
            f"The items of this recipe do not share one base price at {', '.join(disagree)}: "
            "the most common one is shown. Setting a base price makes them agree."
        )
    return RecipeEditorOut(
        template_id=draft.template_id,
        name=draft.name,
        category=draft.category,
        version=rc.template_version(draft),
        as_of=at,
        sizes=tuple(s.value for s in draft.sizes),
        prep_seconds_by_size=dict(draft.prep_seconds_by_size),
        prep_is_estimate=draft.prep_is_estimate,
        base_price_by_size={k: v for k, (v, _d) in base.items()},
        base_price_disagrees=disagree,
        loaded_hourly_rate_pence=configured_rate_pence(),
        components=tuple(components),
        axes=tuple(axes),
        items=tuple(items),
        swaps=swaps,
        history=recipe_history_view(session, template_id),
        item_name_pattern=ctx.item_name_pattern,
        warnings=tuple(warnings),
    )


def _ops(body: ChangesetIn) -> list[dc.TemplateOp]:
    out: list[dc.TemplateOp] = []
    for op in body.ops:
        given = op.model_fields_set
        if op.op == "component.qty":
            out.append(dc.OpComponentQty(op.component_id, _qty_map(op.qty_by_size) or {}))
        elif op.op == "component.set":
            out.append(
                dc.OpComponentSet(
                    component_id=op.component_id,
                    role=ComponentRole(op.role) if op.role else None,
                    set_ingredient="ingredient_id" in given,
                    ingredient_id=op.ingredient_id,
                    is_substitutable=op.is_substitutable,
                    is_required=op.is_required,
                )
            )
        elif op.op == "component.add":
            out.append(
                dc.OpComponentAdd(
                    role=ComponentRole(op.role),
                    ingredient_id=op.ingredient_id,
                    qty_by_size=_qty_map(op.qty_by_size) or {},
                    is_substitutable=op.is_substitutable,
                    is_required=op.is_required,
                )
            )
        elif op.op == "component.remove":
            out.append(dc.OpComponentRemove(op.component_id))
        elif op.op == "prep.set":
            out.append(
                dc.OpPrepSet(
                    {str(k): v for k, v in op.prep_seconds_by_size.items()}, op.is_estimate
                )
            )
        elif op.op == "price.base":
            out.append(dc.OpBasePrice({str(k): v for k, v in op.base_price_pence_by_size.items()}))
        elif op.op == "option.set":
            out.append(
                dc.OpOptionSet(
                    option_id=op.option_id,
                    name=op.name,
                    set_ingredient="ingredient_id" in given,
                    ingredient_id=op.ingredient_id,
                    set_qty="qty_by_size" in given,
                    qty_by_size=_qty_map(op.qty_by_size),
                    price_delta_pence=op.price_delta_pence,
                    set_season="season_id" in given,
                    season_id=op.season_id,
                )
            )
        elif op.op == "option.add":
            out.append(
                dc.OpOptionAdd(
                    axis_id=op.axis_id,
                    name=op.name,
                    ingredient_id=op.ingredient_id,
                    qty_by_size=_qty_map(op.qty_by_size),
                    price_delta_pence=op.price_delta_pence,
                    season_id=op.season_id,
                )
            )
        elif op.op == "option.active":
            out.append(dc.OpOptionActive(op.option_id, op.active))
        elif op.op == "option.remove":
            out.append(dc.OpOptionRemove(op.option_id))
        elif op.op == "template.rename":
            out.append(dc.OpTemplateRename(op.name))
    return out


def _stale(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def changeset_preview_view(
    session: Session, template_id: int, body: ChangesetIn
) -> ChangesetPreviewOut:
    preview = rc.preview_changeset(
        session,
        template_id,
        _ops(body),
        window_days=body.window_days,
        loaded_hourly_rate_pence=body.loaded_hourly_rate_pence,
    )
    if preview.version != body.base_version:
        raise _stale(
            rc.StaleVersionError(
                "this recipe changed after the page was loaded, so a preview of your edit "
                "against it would describe a recipe you have not seen. Reload it and make "
                "the edit again."
            )
        )
    out = preview.outcome
    return ChangesetPreviewOut(
        template_id=template_id,
        base_version=preview.version,
        at=preview.at,
        diff=out.diff,
        refusals=tuple(RefusalOut(op_index=r.op_index, message=r.message) for r in out.refusals),
        warnings=out.warnings,
        pos_actions=out.pos_actions,
        impact=_impact_out(preview.impact),
        items_created=tuple(_change_out(c) for c in preview.items_created),
        items_deactivated=tuple(_change_out(c) for c in preview.items_deactivated),
        loaded_hourly_rate_pence=preview.loaded_hourly_rate_pence,
    )


def changeset_apply_view(
    session: Session, template_id: int, body: ChangesetApplyIn
) -> ChangesetAppliedOut:
    at = rc.apply_from(body.apply_from)
    try:
        applied = rc.apply_changeset(
            session,
            template_id,
            _ops(body),
            base_version=body.base_version,
            actor=body.actor,
            effective_from=at,
        )
    except rc.StaleVersionError as exc:
        raise _stale(exc) from None
    return ChangesetAppliedOut(
        template_id=applied.template_id,
        effective_from=applied.effective_from,
        new_version=applied.new_version,
        summary=applied.summary,
        diff=applied.diff,
        component_ids_opened=tuple(applied.component_ids_opened),
        component_ids_closed=tuple(applied.component_ids_closed),
        option_ids_opened=tuple(applied.option_ids_opened),
        option_ids_closed=tuple(applied.option_ids_closed),
        menu_items_created=tuple(applied.menu_items_created),
        menu_items_repriced=tuple(applied.menu_items_repriced),
        rollup_items_recosted=applied.rollup_items_recosted,
        rollup_summary=applied.rollup_summary,
        pos_actions=applied.pos_actions,
    )


def seasons_view(session: Session) -> tuple[SeasonOut, ...]:
    today = datetime.now(settings.tz).date()
    out: list[SeasonOut] = []
    for s in session.scalars(select(Season).order_by(Season.starts_on, Season.name)):
        spec = SeasonSpec(
            season_id=s.id,
            name=s.name,
            starts_on=s.starts_on,
            ends_on=s.ends_on,
            is_recurring_annually=s.is_recurring_annually,
        )
        out.append(
            SeasonOut(
                season_id=s.id,
                name=s.name,
                starts_on=s.starts_on,
                ends_on=s.ends_on,
                is_recurring_annually=s.is_recurring_annually,
                is_open_today=spec.contains(today),
            )
        )
    return tuple(out)


# -- swaps ---------------------------------------------------------------------


def swaps_view(session: Session) -> tuple[SwapOut, ...]:
    names = dict(session.execute(select(Ingredient.id, Ingredient.name)).tuples().all())
    starts = _version_starts(session)
    return tuple(
        _swap_out(m, names, None, starts.get(m.id))
        for m in session.scalars(select(Modifier).order_by(Modifier.name))
    )


def _swap_change(body: SwapChangeIn) -> mv.ModifierChange:
    given = body.model_fields_set
    return mv.ModifierChange(
        action=ModifierAction(body.action) if body.action else None,
        target_role=ComponentRole(body.target_role) if body.target_role else None,
        set_ingredient="ingredient_id" in given,
        ingredient_id=body.ingredient_id,
        set_qty_delta="qty_delta" in given,
        qty_delta=_qty(body.qty_delta, "quantity") if body.qty_delta is not None else None,
        set_qty_multiplier="qty_multiplier" in given,
        qty_multiplier=(
            _qty(body.qty_multiplier, "multiplier") if body.qty_multiplier is not None else None
        ),
        price_pence=body.price_pence,
        price_is_estimate=body.price_is_estimate,
        is_active=body.is_active,
    )


def swap_preview_view(session: Session, modifier_id: int, body: SwapChangeIn) -> SwapPreviewOut:
    p = mv.preview_modifier_change(
        session, modifier_id, _swap_change(body), window_days=body.window_days
    )
    return SwapPreviewOut(
        modifier_id=p.modifier_id,
        name=p.name,
        diff=p.diff,
        refusals=p.refusals,
        warnings=p.warnings,
        sales_in_window=p.sales_in_window,
        revenue_delta_pence=p.revenue_delta_pence,
        window_days=p.window_days,
        recipes_affected=p.templates_affected,
    )


def swap_apply_view(session: Session, modifier_id: int, body: SwapApplyIn) -> SwapAppliedOut:
    at = rc.apply_from(body.apply_from)
    version_id, effective_from, diff = mv.apply_modifier_change(
        session, modifier_id, _swap_change(body), actor=body.actor, effective_from=at
    )
    modifier = session.get(Modifier, modifier_id)
    if modifier is None:  # pragma: no cover
        raise LookupError(f"swap {modifier_id} not found")
    names = dict(session.execute(select(Ingredient.id, Ingredient.name)).tuples().all())
    return SwapAppliedOut(
        modifier_id=modifier_id,
        version_id=version_id,
        effective_from=effective_from,
        diff=diff,
        swap=_swap_out(modifier, names, None, effective_from),
    )


# -- proposals -----------------------------------------------------------------


def proposal_preview_view(
    _session: Session, proposal_id: str, body: ProposalPreviewIn
) -> ProposalPreviewOut:
    """Wraps `preview_proposal`, which runs in its own always-rolled-back transaction."""
    try:
        p = preview_proposal(proposal_id, allow_conflicts=body.allow_conflicts)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    report = p.report
    return ProposalPreviewOut(
        proposal_id=p.proposal_id,
        name=p.name,
        blocked_reason=p.blocked_reason,
        would_repoint=report.items_repointed if report else 0,
        would_close_manual_lines=report.manual_lines_closed if report else 0,
        components=report.components if report else 0,
        options=report.options if report else 0,
        sizes=tuple(report.sizes) if report else (),
        items_skipped_other_template=tuple(report.items_skipped_other_template) if report else (),
        items_unresolved_option=tuple(report.items_unresolved_option) if report else (),
        cost_changes=tuple(
            ProposalCostOut(
                menu_item_id=c.menu_item_id,
                name=c.name,
                size_code=_size_str(c.size_code),
                price_pence=c.price_pence,
                cost_before=_cost(
                    c.cost_before, PriceSource(c.source_before) if c.source_before else None
                ),
                cost_after=_cost(
                    c.cost_after, PriceSource(c.source_after) if c.source_after else None
                ),
                margin_pct_before=pct(ItemChange.margin_pct(c.price_pence, c.cost_before)),
                margin_pct_after=pct(ItemChange.margin_pct(c.price_pence, c.cost_after)),
            )
            for c in p.cost_changes
        ),
        warnings=tuple(report.warnings) if report else (),
        summary=report.summary() if report else None,
    )


def proposal_confirm_view(
    session: Session, proposal_id: str, body: ProposalConfirmIn
) -> MaterialiseResponse:
    """Confirm by ID ONLY (ARCHITECTURE 8Q): a name is refused here, never resolved."""
    wanted = proposal_id.strip().casefold()
    if not any(p.proposal_id == wanted for p in list_proposals(session)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"{proposal_id!r} is not a proposal id. Recipes are confirmed by id because "
                "two different detected recipes can share a name."
            ),
        )
    return materialise_proposal_view(
        session,
        key=wanted,
        body=MaterialiseIn(actor=body.actor, allow_conflicts=body.allow_conflicts),
    )


# ==========================================================================
# Menu items
# ==========================================================================


def _margin(price: int, cost: Cost) -> float | None:
    if price <= 0 or cost.pence is None:
        return None
    return pct(float((Decimal(price) - Decimal(cost.pence)) / Decimal(price) * 100))


def _groups(session: Session, rows: list[MenuItem]) -> list[MenuGroupOut]:
    costs = {c.menu_item_id: c for c in SqlMenuCostRepository(session).list_all()}
    kinds = {c.name: c.kind for c in session.scalars(select(MenuCategory))}
    template_names = dict(
        session.execute(select(DrinkTemplate.id, DrinkTemplate.name)).tuples().all()
    )
    photos = {a.id: a.filename for a in session.scalars(select(MediaAsset))}
    prep = SqlCompositionRepository(session).prep_times([r.id for r in rows])
    season_names = dict(session.execute(select(Season.id, Season.name)).tuples().all())
    sold = _sold_since(session, [r.id for r in rows], days=30)
    by_name: dict[str, list[MenuItem]] = defaultdict(list)
    for row in rows:
        by_name[row.name].append(row)
    out: list[MenuGroupOut] = []
    for name, members in by_name.items():
        members.sort(key=lambda r: _size_rank(r.size_code))
        anchor = min(members, key=lambda r: r.id)
        sizes: list[MenuSizeOut] = []
        for r in members:
            cached: CachedCost | None = costs.get(r.id)
            cost = (
                cost_from_cached(cached)
                if cached is not None
                else cost_unknown("no recipe lines, so what this costs is unknown -- not zero")
            )
            p = prep.get(r.id)
            seconds = p.seconds if p is not None and p.is_known else None
            mpm: str | None = None
            if seconds and cost.pence is not None and r.price_pence > 0:
                mpm = as_pence(
                    (Decimal(r.price_pence) - Decimal(cost.pence)) / (Decimal(seconds) / 60)
                )
            sizes.append(
                MenuSizeOut(
                    menu_item_id=r.id,
                    size_code=_size_str(r.size_code),
                    price_pence=r.price_pence,
                    active=r.active,
                    on_till=r.lightspeed_id is not None,
                    cost=cost,
                    margin_pct=_margin(r.price_pence, cost),
                    labour_cost_pence=as_pence(cached.labour_cost_pence) if cached else None,
                    manual_recipe=r.manual_recipe,
                    data_quality_flag=r.data_quality_flag,
                    has_recipe=cached is not None and cached.ingredient_count > 0,
                    prep_seconds=seconds,
                    prep_is_estimate=p.is_estimate if seconds is not None and p else None,
                    prep_is_override=r.prep_seconds is not None,
                    margin_per_minute_pence=mpm,
                )
            )
        considered = [s for s in sizes if s.active] or sizes
        priced = [s for s in considered if s.price_pence > 0]
        known = [s for s in priced if s.margin_pct is not None]
        lowest = min(known, key=lambda s: s.margin_pct or 0.0) if known else None
        category = anchor.category
        kind = kinds.get(category or "", MenuKind.OTHER)
        photo_id = next((r.photo_asset_id for r in members if r.photo_asset_id), None)
        out.append(
            MenuGroupOut(
                key=str(anchor.id),
                anchor_id=anchor.id,
                name=name,
                category=category,
                kind=kind.value,
                note=next((r.note for r in members if r.note), None),
                template_id=anchor.template_id,
                template_name=template_names.get(anchor.template_id)
                if anchor.template_id
                else None,
                photo_url=media_url(photos[photo_id]) if photo_id in photos else None,
                is_active=any(r.active for r in members),
                on_till=all(r.lightspeed_id is not None for r in members),
                sizes=tuple(sizes),
                lowest_margin=LowestMarginOut(
                    pct=lowest.margin_pct if lowest else None,
                    is_estimate=any(s.cost.is_estimate for s in known),
                    is_missing=bool(priced) and not known,
                    no_price=not priced,
                ),
                season_id=next((r.season_id for r in members if r.season_id), None),
                season_name=next(
                    (season_names.get(r.season_id) for r in members if r.season_id), None
                ),
                sold_30d=as_qty(sum((sold.get(r.id, Decimal(0)) for r in members), Decimal(0)))
                or "0",
            )
        )
    return out


def _sold_since(session: Session, ids: list[int], *, days: int) -> dict[int, Decimal]:
    """menu_item_id -> net units sold in the last `days` days (voided lines excluded).

    Summed in Python, not SQL: `qty` is a scaled integer on SQLite (ARCHITECTURE 8E).
    """
    if not ids:
        return {}
    since = datetime.now(UTC) - timedelta(days=days)
    out: dict[int, Decimal] = defaultdict(Decimal)
    for item_id, qty in session.execute(
        select(Sale.menu_item_id, Sale.qty).where(
            Sale.sold_at >= since, Sale.voided.is_(False), Sale.menu_item_id.in_(ids)
        )
    ).tuples():
        out[item_id] += qty
    return out


def menu_items_view(session: Session) -> MenuItemsResponse:
    rows = list(session.scalars(select(MenuItem)))
    groups = sorted(_groups(session, rows), key=lambda g: g.name.casefold())
    registered = {c.name: c for c in session.scalars(select(MenuCategory))}
    counts: dict[str, int] = defaultdict(int)
    for g in groups:
        counts[g.category or ""] += 1
    names = set(counts) | set(registered)
    categories = tuple(
        MenuCategoryOut(
            name=name,
            kind=(registered[name].kind if name in registered else MenuKind.OTHER).value,
            count=counts.get(name, 0),
            registered=name in registered,
        )
        for name in sorted(names, key=lambda n: (n == "", n.casefold()))
    )
    return MenuItemsResponse(as_of=datetime.now(UTC), groups=tuple(groups), categories=categories)


def menu_item_detail_view(session: Session, menu_item_id: int) -> MenuItemDetailOut:
    row = session.get(MenuItem, menu_item_id)
    if row is None:
        raise LookupError(f"menu item {menu_item_id} not found")
    members = mc.group_rows(session, menu_item_id)
    group = _groups(session, members)[0]
    size = next(s for s in group.sizes if s.menu_item_id == menu_item_id)
    at = datetime.now(UTC)
    spec = SqlCompositionRepository(session).item_spec(menu_item_id, at)
    snapshots = snapshots_at(session, at)
    categories = dict(session.execute(select(Ingredient.id, Ingredient.category)).tuples().all())
    lines: list[MenuLineOut] = []
    if spec is not None:
        recipe = resolve_recipe(spec, (), at, ingredients=snapshots)
        for b in recipe.cost_breakdown:
            lines.append(
                MenuLineOut(
                    ingredient_id=b.ingredient_id,
                    ingredient_name=b.ingredient_name,
                    qty=as_qty(b.qty) or "0",
                    unit=b.unit.value if b.unit else "EACH",
                    unit_cost=_cost(b.cost_per_unit_pence, b.source, reason="no price recorded"),
                    line_cost=_cost(b.line_cost_pence, b.source, reason="no price recorded"),
                    category=categories.get(b.ingredient_id),
                )
            )
    prices = tuple(
        PriceHistoryOut(
            effective_from=p.effective_from,
            effective_to=p.effective_to,
            price_pence=p.price_pence,
            source=p.source.value,
            set_by=p.set_by,
        )
        for p in session.scalars(
            select(MenuItemPrice)
            .where(MenuItemPrice.menu_item_id == menu_item_id)
            .order_by(MenuItemPrice.effective_from.desc())
        )
    )
    history = _history(
        list(session.scalars(select(RecipeChange).where(RecipeChange.menu_item_id == menu_item_id)))
    )
    return MenuItemDetailOut(
        size=size,
        group=group,
        lines=tuple(lines),
        editable_lines=row.manual_recipe,
        estimate_names=tuple(
            sorted({line.ingredient_name for line in lines if line.unit_cost.is_estimate})
        ),
        prices=prices,
        history=history,
    )


PAYMENT_NOTE = (
    "How each sale was paid (card, cash) is not recorded per item: the till's payment "
    "reports arrive as daily totals per method, so they cannot be matched to a line."
)


def menu_item_sales_view(
    session: Session, menu_item_id: int, *, page: int, page_size: int, all_sizes: bool
) -> ItemSalesOut:
    """Read-only: every till line matched to this product (or this size), newest first.

    Totals are over every matched line, not just the page, and exclude voided lines.
    """
    rows = mc.group_rows(session, menu_item_id) if all_sizes else []
    if not all_sizes:
        row = session.get(MenuItem, menu_item_id)
        if row is None:
            raise LookupError(f"menu item {menu_item_id} not found")
        rows = [row]
    ids = [r.id for r in rows]
    size_of = {r.id: _size_str(r.size_code) for r in rows}
    units = Decimal(0)
    gross = 0
    first: datetime | None = None
    last: datetime | None = None
    by_channel: dict[str, int] = defaultdict(int)
    total = 0
    for qty, gross_pence, sold_at, channel, voided in session.execute(
        select(Sale.qty, Sale.gross_pence, Sale.sold_at, Sale.channel, Sale.voided).where(
            Sale.menu_item_id.in_(ids)
        )
    ).tuples():
        total += 1
        if voided:
            continue
        units += qty
        gross += gross_pence
        by_channel[channel.value] += 1
        first = sold_at if first is None or sold_at < first else first
        last = sold_at if last is None or sold_at > last else last
    page_rows = list(
        session.scalars(
            select(Sale)
            .where(Sale.menu_item_id.in_(ids))
            .order_by(Sale.sold_at.desc(), Sale.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    modifier_ids = {m for sale in page_rows for m in (sale.applied_modifiers or [])}
    modifier_names = (
        dict(
            session.execute(select(Modifier.id, Modifier.name).where(Modifier.id.in_(modifier_ids)))
            .tuples()
            .all()
        )
        if modifier_ids
        else {}
    )
    return ItemSalesOut(
        menu_item_ids=tuple(ids),
        total_rows=total,
        page=page,
        page_size=page_size,
        units=as_qty(units) or "0",
        gross_pence=gross,
        first_sold_at=first,
        last_sold_at=last,
        by_channel=dict(by_channel),
        payment_note=PAYMENT_NOTE,
        rows=tuple(
            ItemSaleOut(
                sale_id=sale.id,
                sold_at=sale.sold_at,
                receipt_id=sale.lightspeed_receipt_id,
                menu_item_id=sale.menu_item_id,
                size_code=size_of.get(sale.menu_item_id),
                qty=as_qty(sale.qty) or "0",
                gross_pence=sale.gross_pence,
                channel=sale.channel.value,
                voided=sale.voided,
                is_refund=sale.is_refund,
                modifier_names=tuple(
                    modifier_names.get(m, f"modifier {m}") for m in (sale.applied_modifiers or [])
                ),
            )
            for sale in page_rows
        ),
    )


def menu_prep_view(session: Session, menu_item_id: int, body: PrepIn) -> PrepOut:
    group_ids = {r.id for r in mc.group_rows(session, menu_item_id)}
    if not set(body.seconds) <= group_ids:
        raise ValueError("those sizes do not all belong to this product")
    try:
        done = mc.set_prep_seconds(
            session, dict(body.seconds), actor=body.actor, is_estimate=body.is_estimate
        )
    except ValueError as exc:
        raise HTTPException(HTTP_422, detail=str(exc)) from exc
    return PrepOut(
        menu_item_ids=tuple(done.menu_item_ids),
        summary=done.summary,
        rollup_items_recosted=done.rollup_items_recosted,
    )


def _line_ins(lines: list[LineIn]) -> list[mc.LineIn]:
    return [
        mc.LineIn(
            ingredient_id=line.ingredient_id,
            qty=_qty(line.qty, "quantity"),
            unit=Unit(line.unit) if line.unit else None,
        )
        for line in lines
    ]


def _size(code: str | None) -> SizeCode | None:
    return SizeCode(code) if code else None


def menu_create_view(session: Session, body: MenuItemCreateIn) -> MenuWriteOut:
    ids = mc.create_menu_item(
        session,
        name=body.name,
        category=body.category,
        note=body.note,
        sizes=[
            mc.SizeIn(
                size_code=_size(s.size_code),
                price_pence=s.price_pence,
                lines=tuple(_line_ins(s.lines)),
            )
            for s in body.sizes
        ],
        actor=body.actor,
    )
    return MenuWriteOut(menu_item_ids=tuple(ids), summary=f"Created {body.name.strip()}")


def menu_group_view(session: Session, menu_item_id: int, body: MenuGroupIn) -> MenuWriteOut:
    given = body.model_fields_set
    ids = mc.update_group(
        session,
        menu_item_id,
        actor=body.actor,
        name=body.name,
        category=body.category,
        set_category="category" in given,
        note=body.note,
        set_note="note" in given,
        active=body.active,
    )
    return MenuWriteOut(menu_item_ids=tuple(ids), summary="Saved")


def menu_add_size_view(session: Session, menu_item_id: int, body: MenuSizeIn) -> MenuWriteOut:
    try:
        new_id = mc.add_size(
            session,
            menu_item_id,
            size_code=_size(body.size_code),
            price_pence=body.price_pence,
            actor=body.actor,
            copy_from_menu_item_id=body.copy_from_menu_item_id,
        )
    except mc.NotManualRecipeError as exc:
        raise _refuse_templated(exc) from None
    return MenuWriteOut(menu_item_ids=(new_id,), summary="Size added")


def menu_remove_size_view(session: Session, menu_item_id: int, actor: str) -> MenuWriteOut:
    mc.remove_size(session, menu_item_id, actor=actor)
    return MenuWriteOut(menu_item_ids=(menu_item_id,), summary="Size taken off the menu")


def menu_duplicate_view(session: Session, menu_item_id: int, actor: str) -> MenuWriteOut:
    ids = mc.duplicate_item(session, menu_item_id, actor=actor)
    return MenuWriteOut(menu_item_ids=tuple(ids), summary="Duplicated")


def _refuse_templated(exc: mc.NotManualRecipeError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def lines_preview_view(session: Session, menu_item_id: int, body: ManualLinesIn) -> LinesPreviewOut:
    try:
        p = mc.preview_manual_lines(
            session,
            menu_item_id,
            _line_ins(body.lines),
            also_menu_item_ids=body.also_menu_item_ids,
            window_days=body.window_days,
        )
    except mc.NotManualRecipeError as exc:
        raise _refuse_templated(exc) from None
    return LinesPreviewOut(
        menu_item_ids=p.menu_item_ids, diff=p.diff, impact=_impact_out(p.impact), at=p.at
    )


def lines_apply_view(
    session: Session, menu_item_id: int, body: ManualLinesApplyIn
) -> LinesAppliedOut:
    at = rc.apply_from(body.apply_from)
    try:
        a = mc.apply_manual_lines(
            session,
            menu_item_id,
            _line_ins(body.lines),
            actor=body.actor,
            also_menu_item_ids=body.also_menu_item_ids,
            effective_from=at,
        )
    except mc.NotManualRecipeError as exc:
        raise _refuse_templated(exc) from None
    return LinesAppliedOut(
        effective_from=a.effective_from,
        menu_item_ids=tuple(a.menu_item_ids),
        lines_closed=a.lines_closed,
        lines_opened=a.lines_opened,
        rollup_items_recosted=a.rollup_items_recosted,
        diff=a.diff,
    )


def prices_preview_view(session: Session, body: MenuPricesIn) -> PricesPreviewOut:
    p = mc.preview_prices(
        session,
        [(x.menu_item_id, x.price_pence) for x in body.prices],
        window_days=body.window_days,
    )
    return PricesPreviewOut(
        diff=p.diff, pos_actions=p.pos_actions, impact=_impact_out(p.impact), at=p.at
    )


def prices_apply_view(session: Session, body: MenuPricesApplyIn) -> PricesAppliedOut:
    at = rc.apply_from(body.apply_from)
    a = mc.apply_prices(
        session,
        [(x.menu_item_id, x.price_pence) for x in body.prices],
        actor=body.actor,
        effective_from=at,
    )
    return PricesAppliedOut(
        effective_from=a.effective_from,
        repriced=tuple(a.repriced),
        diff=a.diff,
        pos_actions=a.pos_actions,
    )


def category_create_view(session: Session, name: str, kind: str) -> MenuCategoryOut:
    mc.create_category(session, name=name, kind=MenuKind(kind))
    return MenuCategoryOut(
        name=name.strip(), kind=cast(MenuKindStr, kind), count=0, registered=True
    )


def photo_upload_view(
    session: Session, menu_item_id: int, data: bytes, actor: str | None
) -> PhotoOut:
    mc.group_rows(session, menu_item_id)  # 404 before any file is written
    stored = store_image(session, data, uploaded_by=actor)
    ids = mc.attach_photo(session, menu_item_id, stored.asset_id)
    return PhotoOut(
        asset_id=stored.asset_id,
        photo_url=stored.url,
        width=stored.width,
        height=stored.height,
        bytes=stored.bytes,
        content_type=stored.content_type,
        menu_item_ids=tuple(ids),
    )


def photo_clear_view(session: Session, menu_item_id: int) -> PhotoOut:
    ids = mc.attach_photo(session, menu_item_id, None)
    return PhotoOut(
        asset_id=None,
        photo_url=None,
        width=None,
        height=None,
        bytes=None,
        content_type=None,
        menu_item_ids=tuple(ids),
    )


# ==========================================================================
# Ingredients
# ==========================================================================


def _usage(session: Session) -> dict[int, dict[str, UsedInOut]]:
    """ingredient_id -> product name -> how it is used, from TODAY's recipes. Bulk."""
    at = datetime.now(UTC)
    out: dict[int, dict[str, UsedInOut]] = defaultdict(dict)
    anchors: dict[str, int] = {}
    for item_id, name in session.execute(select(MenuItem.id, MenuItem.name).order_by(MenuItem.id)):
        anchors.setdefault(name, item_id)

    for ingredient_id, name in session.execute(
        select(ManualRecipeLine.ingredient_id, MenuItem.name)
        .join(MenuItem, MenuItem.id == ManualRecipeLine.menu_item_id)
        .where(
            MenuItem.manual_recipe.is_(True),
            ManualRecipeLine.effective_from <= at,
            or_(ManualRecipeLine.effective_to.is_(None), ManualRecipeLine.effective_to > at),
        )
    ):
        out[ingredient_id].setdefault(
            name,
            UsedInOut(menu_item_id=anchors[name], name=name, via="one-off", template_name=None),
        )

    template_names = dict(
        session.execute(select(DrinkTemplate.id, DrinkTemplate.name)).tuples().all()
    )
    items_by_template: dict[int, list[tuple[str, dict[str, int]]]] = defaultdict(list)
    for template_id, name, selected in session.execute(
        select(MenuItem.template_id, MenuItem.name, MenuItem.selected_options).where(
            MenuItem.template_id.is_not(None)
        )
    ):
        items_by_template[template_id].append((name, selected or {}))

    for ingredient_id, template_id in session.execute(
        select(TemplateComponent.ingredient_id, TemplateComponent.template_id).where(
            TemplateComponent.ingredient_id.is_not(None),
            TemplateComponent.effective_from <= at,
            or_(TemplateComponent.effective_to.is_(None), TemplateComponent.effective_to > at),
        )
    ):
        for name, _sel in items_by_template.get(template_id, []):
            out[ingredient_id].setdefault(
                name,
                UsedInOut(
                    menu_item_id=anchors[name],
                    name=name,
                    via="recipe",
                    template_name=template_names.get(template_id),
                ),
            )

    lineage: dict[int, tuple[int, str]] = {
        oid: (axis, name)
        for oid, axis, name in session.execute(
            select(VariantOption.id, VariantOption.axis_id, VariantOption.name)
        )
    }
    for ingredient_id, axis_id, oname, template_id in session.execute(
        select(
            VariantOption.ingredient_id,
            VariantOption.axis_id,
            VariantOption.name,
            VariantAxis.template_id,
        )
        .join(VariantAxis, VariantAxis.id == VariantOption.axis_id)
        .where(
            VariantOption.ingredient_id.is_not(None),
            VariantOption.effective_from <= at,
            or_(VariantOption.effective_to.is_(None), VariantOption.effective_to > at),
        )
    ):
        for name, selected in items_by_template.get(template_id, []):
            if any(lineage.get(int(v)) == (axis_id, oname) for v in selected.values()):
                out[ingredient_id].setdefault(
                    name,
                    UsedInOut(
                        menu_item_id=anchors[name],
                        name=name,
                        via="recipe",
                        template_name=template_names.get(template_id),
                    ),
                )
    return out


def _rows(session: Session, ingredients: list[Ingredient]) -> list[IngredientRowOut]:
    ids = [i.id for i in ingredients]
    supplier_names = dict(session.execute(select(Supplier.id, Supplier.name)).tuples().all())
    packs: dict[int, IngredientPrice] = {}
    for price in session.scalars(
        select(IngredientPrice)
        .where(IngredientPrice.ingredient_id.in_(ids), IngredientPrice.effective_to.is_(None))
        .order_by(IngredientPrice.effective_from)
    ):
        packs[price.ingredient_id] = price
    links: dict[int, list[IngredientSupplierOut]] = defaultdict(list)
    for sp in session.scalars(
        select(SupplierProduct).where(
            SupplierProduct.ingredient_id.in_(ids), SupplierProduct.archived_at.is_(None)
        )
    ):
        if any(link.supplier_id == sp.supplier_id for link in links[sp.ingredient_id]):
            continue
        links[sp.ingredient_id].append(
            IngredientSupplierOut(
                supplier_id=sp.supplier_id,
                name=supplier_names.get(sp.supplier_id, f"#{sp.supplier_id}"),
                is_preferred=sp.is_preferred,
            )
        )
    usage = _usage(session)
    photo_ids = {i.photo_asset_id for i in ingredients if i.photo_asset_id is not None}
    photos = (
        {a.id: a for a in session.scalars(select(MediaAsset).where(MediaAsset.id.in_(photo_ids)))}
        if photo_ids
        else {}
    )
    out: list[IngredientRowOut] = []
    for i in ingredients:
        pack = packs.get(i.id)
        photo = photos.get(i.photo_asset_id) if i.photo_asset_id is not None else None
        out.append(
            IngredientRowOut(
                ingredient_id=i.id,
                name=i.name,
                category=i.category,
                unit=i.unit.value,
                unit_cost=_cost(
                    i.current_cost_pence_per_unit,
                    i.current_cost_source,
                    reason="no price recorded yet -- unknown, not zero",
                ),
                pack=(
                    PackOut(
                        pack_size=as_qty(pack.pack_size) or "0",
                        pack_unit=pack.pack_unit.value,
                        pack_cost_pence=pack.pack_cost_pence,
                        source=pack.source.value,
                        supplier_id=pack.supplier_id,
                        supplier_name=supplier_names.get(pack.supplier_id)
                        if pack.supplier_id
                        else None,
                        effective_from=pack.effective_from,
                        recorded_by=pack.recorded_by,
                        note=pack.note,
                    )
                    if pack is not None
                    else None
                ),
                note=i.source_note,
                suppliers=tuple(
                    sorted(links.get(i.id, []), key=lambda link: (not link.is_preferred, link.name))
                ),
                used_in_count=len(usage.get(i.id, {})),
                retired=i.retired_at is not None,
                storage=i.storage.value,
                shelf_life_days=i.shelf_life_days,
                shelf_life_source=i.shelf_life_source.value if i.shelf_life_source else None,
                open_life_days=i.open_life_days,
                transit_buffer_days=i.transit_buffer_days,
                tier=i.tier.value,
                waste_factor=as_qty(i.waste_factor) or "0",
                photo_url=media_url(photo.filename) if photo is not None else None,
                allergens=tuple(i.allergens) if i.allergens is not None else None,
                allergens_source=i.allergens_source,
                allergens_confirmed=(i.allergens_source or "").startswith(
                    ic.ALLERGENS_CHECKED_PREFIX
                ),
                photo_licence=photo.licence if photo is not None else None,
                photo_author=photo.author if photo is not None else None,
                photo_source_url=photo.source_url if photo is not None else None,
            )
        )
    return out


def ingredients_view(session: Session) -> IngredientsResponse:
    ingredients = list(
        session.scalars(
            select(Ingredient).where(Ingredient.retired_at.is_(None)).order_by(Ingredient.name)
        )
    )
    rows = _rows(session, ingredients)
    counts: dict[str, int] = {}
    for i in ingredients:
        key = i.category or "Other"
        counts[key] = counts.get(key, 0) + 1
    suppliers = tuple(
        SupplierNameOut(supplier_id=s.id, name=s.name)
        for s in session.scalars(
            select(Supplier).where(Supplier.archived_at.is_(None)).order_by(Supplier.name)
        )
    )
    return IngredientsResponse(
        rows=tuple(rows),
        categories=tuple(counts.items()),
        estimated_count=sum(1 for r in rows if r.unit_cost.is_estimate),
        total=len(rows),
        suppliers=suppliers,
    )


def ingredient_detail_view(session: Session, ingredient_id: int) -> IngredientDetailOut:
    ingredient = session.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise LookupError(f"ingredient {ingredient_id} not found")
    row = _rows(session, [ingredient])[0]
    suppliers = {s.id: s for s in session.scalars(select(Supplier))}
    raw_offers: list[tuple[SupplierProduct, Decimal | None]] = []
    for sp in session.scalars(
        select(SupplierProduct).where(
            SupplierProduct.ingredient_id == ingredient_id, SupplierProduct.archived_at.is_(None)
        )
    ):
        try:
            size = convert(sp.pack_size, sp.pack_unit, ingredient.unit)
            unit_cost: Decimal | None = Decimal(sp.price_pence) / size if size > 0 else None
        except IncompatibleUnitsError:
            unit_cost = None
        raw_offers.append((sp, unit_cost))
    known = [c for _sp, c in raw_offers if c is not None]
    best = min(known) if known else None
    offers = tuple(
        OfferOut(
            supplier_product_id=sp.id,
            supplier_id=sp.supplier_id,
            supplier_name=suppliers[sp.supplier_id].name if sp.supplier_id in suppliers else "?",
            sku=sp.sku or None,
            pack_size=as_qty(sp.pack_size) or "0",
            pack_unit=sp.pack_unit.value,
            price_pence=sp.price_pence,
            unit_cost_pence=as_pence(cost),
            vs_cheapest_pct=(
                pct(float((cost - best) / best * 100))
                if cost is not None and best is not None and best > 0 and len(known) > 1
                else None
            ),
            is_cheapest=cost is not None and best is not None and len(known) > 1 and cost == best,
            is_preferred=sp.is_preferred,
            last_seen_price_at=sp.last_seen_price_at,
            terms_are_placeholders=(
                suppliers[sp.supplier_id].terms_are_placeholders
                if sp.supplier_id in suppliers
                else True
            ),
        )
        for sp, cost in sorted(raw_offers, key=lambda pair: (not pair[0].is_preferred, pair[0].id))
    )
    preferred = next((o.supplier_name for o in offers if o.is_preferred), None)
    usage = _usage(session).get(ingredient_id, {})
    history = tuple(
        IngredientPriceRowOut(
            effective_from=p.effective_from,
            effective_to=p.effective_to,
            pack_size=as_qty(p.pack_size) or "0",
            pack_unit=p.pack_unit.value,
            pack_cost_pence=p.pack_cost_pence,
            cost_per_unit_pence=as_pence(p.cost_per_unit_pence) or "0",
            source=p.source.value,
            supplier_name=suppliers[p.supplier_id].name if p.supplier_id in suppliers else None,
            recorded_by=p.recorded_by,
        )
        for p in session.scalars(
            select(IngredientPrice)
            .where(IngredientPrice.ingredient_id == ingredient_id)
            .order_by(IngredientPrice.effective_from.desc())
        )
    )
    return IngredientDetailOut(
        row=row,
        offers=offers,
        preferred_supplier=preferred,
        used_in=tuple(sorted(usage.values(), key=lambda u: u.name.casefold())),
        price_history=history,
        unit_locked_by={
            k: v for k, v in ic.ingredient_references(session, ingredient_id).items() if v
        },
        retire_blocked_by={
            k: v for k, v in ic.open_recipe_uses(session, ingredient_id).items() if v
        },
    )


def _price_in(body: IngredientPriceIn) -> ic.PriceIn:
    return ic.PriceIn(
        pack_size=_qty(body.pack_size, "pack size"),
        pack_unit=Unit(body.pack_unit),
        pack_cost_pence=body.pack_cost_pence,
        source=PriceSource(body.source),
        supplier_id=body.supplier_id,
        note=body.note,
    )


def ingredient_price_preview_view(
    session: Session, ingredient_id: int, body: IngredientPricePreviewIn
) -> IngredientPricePreviewOut:
    p = ic.preview_ingredient_price(
        session, ingredient_id, _price_in(body), window_days=body.window_days
    )
    return IngredientPricePreviewOut(
        ingredient_id=ingredient_id,
        unit=p.unit.value,
        unit_cost_before=_cost(
            p.cost_per_unit_before, p.source_before, reason="no price recorded yet"
        ),
        unit_cost_after=_cost(p.cost_per_unit_after, p.source_after),
        diff=p.diff,
        impact=_impact_out(p.impact),
        at=p.at,
    )


def _in_use(exc: ic.IngredientInUseError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


def ingredient_price_apply_view(
    session: Session, ingredient_id: int, body: IngredientPriceApplyIn
) -> IngredientPriceAppliedOut:
    at = rc.apply_from(body.apply_from)
    try:
        a = ic.apply_ingredient_price(
            session, ingredient_id, _price_in(body), actor=body.actor, effective_from=at
        )
    except ic.IngredientInUseError as exc:
        raise _in_use(exc) from None
    return IngredientPriceAppliedOut(
        ingredient_id=ingredient_id,
        price_id=a.price_id,
        effective_from=a.effective_from,
        unit_cost=_cost(a.cost_per_unit_pence, a.source),
        rollup_items_recosted=a.rollup_items_recosted,
        rollup_summary=a.rollup_summary,
    )


def ingredient_create_view(session: Session, body: IngredientCreateIn) -> IngredientWriteOut:
    new_id = ic.create_ingredient(
        session,
        name=body.name,
        unit=Unit(body.unit),
        category=body.category,
        storage=Storage(body.storage),
        shelf_life_days=body.shelf_life_days,
        open_life_days=body.open_life_days,
        transit_buffer_days=body.transit_buffer_days,
        waste_factor=_qty(body.waste_factor, "waste factor"),
        note=body.note,
        price=_price_in(body.price) if body.price is not None else None,
        sku=body.sku,
        actor=body.actor,
    )
    return IngredientWriteOut(ingredient_id=new_id, summary=f"Added {body.name.strip()}")


def ingredient_meta_view(
    session: Session, ingredient_id: int, body: IngredientMetaIn
) -> IngredientWriteOut:
    given = body.model_fields_set
    try:
        ic.update_ingredient(
            session,
            ingredient_id,
            actor=body.actor,
            name=body.name,
            category=body.category,
            set_category="category" in given,
            note=body.note,
            set_note="note" in given,
            unit=Unit(body.unit) if body.unit else None,
        )
    except ic.IngredientInUseError as exc:
        raise _in_use(exc) from None
    return IngredientWriteOut(ingredient_id=ingredient_id, summary="Saved")


def ingredient_photo_upload_view(
    session: Session, ingredient_id: int, data: bytes, actor: str | None
) -> IngredientPhotoOut:
    if session.get(Ingredient, ingredient_id) is None:  # 404 before any file is written
        raise LookupError(f"ingredient {ingredient_id} not found")
    stored = store_image(session, data, uploaded_by=actor)
    ic.attach_ingredient_photo(session, ingredient_id, stored.asset_id)
    return IngredientPhotoOut(
        ingredient_id=ingredient_id,
        asset_id=stored.asset_id,
        photo_url=stored.url,
        width=stored.width,
        height=stored.height,
        bytes=stored.bytes,
        content_type=stored.content_type,
    )


def ingredient_photo_clear_view(session: Session, ingredient_id: int) -> IngredientPhotoOut:
    ic.attach_ingredient_photo(session, ingredient_id, None)
    return IngredientPhotoOut(
        ingredient_id=ingredient_id,
        asset_id=None,
        photo_url=None,
        width=None,
        height=None,
        bytes=None,
        content_type=None,
    )


def ingredient_allergens_view(
    session: Session, ingredient_id: int, body: IngredientAllergensIn
) -> IngredientAllergensOut:
    ic.set_allergens(session, ingredient_id, body.allergens, actor=body.actor)
    row = session.get(Ingredient, ingredient_id)
    assert row is not None
    return IngredientAllergensOut(
        ingredient_id=ingredient_id,
        allergens=tuple(row.allergens) if row.allergens is not None else None,
        allergens_source=row.allergens_source,
    )


def ingredient_retire_view(session: Session, ingredient_id: int, actor: str) -> IngredientWriteOut:
    try:
        ic.retire_ingredient(session, ingredient_id, actor=actor)
    except ic.IngredientInUseError as exc:
        raise _in_use(exc) from None
    return IngredientWriteOut(ingredient_id=ingredient_id, summary="Retired")
