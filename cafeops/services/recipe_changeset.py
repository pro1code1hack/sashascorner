"""A recipe edit of any kind, previewed first and applied from today. Recipes spec A2.

`edit_composition.py` handles one kind of edit (a per-size quantity of one component).
The Recipes screen edits everything a recipe has -- quantities, ingredients, roles,
the swappable flag, components added and removed, prep time, base price, flavours --
and a person edits several of those before pressing *Apply from today*. Applying them
one at a time would give N rollups and a half-applied recipe if the fifth were
refused, so a changeset is ONE transaction: all operations or none.

How it stays honest:

- **Preview writes nothing.** The live template is loaded into a pure `TemplateDraft`,
  the operations produce a second draft (`domain.composition.apply_template_ops`), and
  both sides are resolved by the same resolver from specs built the same way. No row
  is written and read back to find out what an edit would do.
- **Apply walks the same two drafts.** What was previewed is what lands: the writes
  below are a diff of `outcome.before` against `outcome.after`, and nothing else.
- **Effective-dated, never in place** (invariant 3). A changed component or flavour is
  closed at `at` and a successor opened; a removed one is closed with no successor. A
  sell price goes through `menu_item_price` (spec C-3).
- **Stale drafts are refused.** The screen sends back the `version` it was built from;
  if anything changed since (another edit, a price change, a flavour toggled), the
  apply is refused with 409 rather than applied on top of a picture nobody saw.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.models import (
    DrinkTemplate,
    Ingredient,
    MenuItem,
    Modifier,
    RecipeChange,
    Season,
    SizeProfile,
    TemplateComponent,
    VariantAxis,
    VariantOption,
)
from cafeops.db.repositories.composition import SqlCompositionRepository
from cafeops.db.repositories.menu_cost import SqlMenuCostRepository
from cafeops.domain.composition import (
    SIZE_ORDER,
    ChangeImpact,
    ChangesetContext,
    ChangesetOutcome,
    DraftAxis,
    DraftComponent,
    DraftItem,
    DraftOption,
    ItemChange,
    TemplateDraft,
    TemplateOp,
    apply_template_ops,
    item_prep,
    item_spec_from_draft,
    qty_text,
    resolve_recipe,
    summarise_changes,
)
from cafeops.domain.enums import ModifierAction
from cafeops.domain.labour import UNTIMED
from cafeops.domain.types import ComponentRole, SizeCode
from cafeops.jobs.cost_rollup import configured_rate_pence, rollup_for_template, snapshots_at
from cafeops.services.actor import require_actor
from cafeops.services.edit_composition import RetroactiveEditError, require_not_retroactive
from cafeops.services.menu_catalog import set_menu_price

__all__ = [
    "ChangesetApplied",
    "ChangesetPreview",
    "ChangesetRefusedError",
    "StaleVersionError",
    "apply_changeset",
    "apply_from",
    "change_summary",
    "changeset_context",
    "load_template_draft",
    "preview_changeset",
    "template_version",
]

WINDOW_DAYS = 30


class ChangesetRefusedError(ValueError):
    """At least one operation was refused, so none are applied (all or nothing)."""


class StaleVersionError(ValueError):
    """The recipe changed after the screen loaded it. 409: reload, then edit."""


def apply_from(day: date | None) -> datetime:
    """When an apply takes effect. "Apply from today" is the only mode (invariant 3).

    None or today -> now. A past day -> `RetroactiveEditError` (409). A future day is
    refused too: a queued edit would sit invisibly under every screen until it landed.
    Now, not the start of today, so two edits on one day chain instead of colliding
    (`effective_to > effective_from` on every dated table).
    """
    now = datetime.now(UTC)
    if day is None:
        return now
    today = now.astimezone(settings.tz).date()
    if day < today:
        require_not_retroactive(datetime.combine(day, time.min, tzinfo=settings.tz))
        raise RetroactiveEditError(f"{day.isoformat()} is before today")  # pragma: no cover
    if day > today:
        raise ValueError(
            f"edits apply from today; {day.isoformat()} is in the future and queued edits "
            "are not supported"
        )
    return now


# --------------------------------------------------------------------------
# Loading the live template as a draft
# --------------------------------------------------------------------------


def _parse_qty_map(raw: dict[str, str] | None, where: str) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for key, value in (raw or {}).items():
        if value is None:
            continue
        try:
            out[str(key).upper()] = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"{where}: stored quantity {value!r} is not a number") from exc
    return out


def load_template_draft(session: Session, template_id: int, at: datetime) -> TemplateDraft:
    """The recipe as it stands at `at`, as a pure draft."""
    template = session.get(DrinkTemplate, template_id)
    if template is None:
        raise LookupError(f"recipe {template_id} not found")

    sizes = tuple(
        session.scalars(
            select(SizeProfile.code)
            .where(SizeProfile.template_id == template_id)
            .order_by(SizeProfile.sort_order)
        )
    )
    axes = tuple(
        DraftAxis(axis_id=a.id, name=a.name, role=a.role)
        for a in session.scalars(
            select(VariantAxis)
            .where(VariantAxis.template_id == template_id)
            .order_by(VariantAxis.sort_order, VariantAxis.id)
        )
    )
    components = tuple(
        DraftComponent(
            key=f"c{c.component_id}",
            component_id=c.component_id,
            role=c.role,
            ingredient_id=c.ingredient_id,
            qty_by_size=_parse_qty_map(c.qty_by_size, f"component {c.component_id}"),
            is_substitutable=c.is_substitutable,
            is_required=c.is_required,
        )
        for c in SqlCompositionRepository(session).live_components(template_id, at)
    )

    axis_ids = [a.axis_id for a in axes]
    all_options = (
        list(session.scalars(select(VariantOption).where(VariantOption.axis_id.in_(axis_ids))))
        if axis_ids
        else []
    )
    live = [
        o
        for o in all_options
        if o.effective_from <= at and (o.effective_to is None or o.effective_to > at)
    ]
    live_by_lineage = {(o.axis_id, o.name): o for o in live}
    lineage_of = {o.id: (o.axis_id, o.name) for o in all_options}
    options = tuple(
        DraftOption(
            key=f"o{o.id}",
            option_id=o.id,
            axis_id=o.axis_id,
            name=o.name,
            ingredient_id=o.ingredient_id,
            qty_by_size=_parse_qty_map(o.qty_by_size, f"flavour {o.name}")
            if o.qty_by_size
            else None,
            price_delta_pence=o.price_delta_pence,
            season_id=o.season_id,
        )
        for o in sorted(live, key=lambda o: o.name.casefold())
    )

    size_rank = {s: i for i, s in enumerate(SIZE_ORDER)}
    rows = sorted(
        session.scalars(select(MenuItem).where(MenuItem.template_id == template_id)),
        key=lambda m: (m.name.casefold(), size_rank.get(m.size_code, 9) if m.size_code else 9),
    )
    items: list[DraftItem] = []
    for row in rows:
        keys: dict[int, str] = {}
        for axis_key, option_id in (row.selected_options or {}).items():
            try:
                axis_id, oid = int(axis_key), int(option_id)
            except (TypeError, ValueError):
                continue
            lineage = lineage_of.get(oid)
            current = live_by_lineage.get(lineage) if lineage is not None else None
            if current is not None:
                keys[axis_id] = f"o{current.id}"
        items.append(
            DraftItem(
                key=f"i{row.id}",
                menu_item_id=row.id,
                name=row.name,
                size_code=row.size_code,
                option_keys=keys,
                price_pence=row.price_pence,
                active=row.active,
                prep_seconds=row.prep_seconds,
                prep_is_estimate=row.prep_seconds_is_estimate,
                on_till=row.lightspeed_id is not None,
            )
        )

    return TemplateDraft(
        template_id=template.id,
        name=template.name,
        category=template.category,
        sizes=sizes,
        axes=axes,
        components=components,
        options=options,
        items=tuple(items),
        prep_seconds_by_size={
            str(k): int(v) for k, v in (template.prep_seconds_by_size or {}).items()
        },
        prep_is_estimate=template.prep_seconds_is_estimate,
    )


def template_version(draft: TemplateDraft) -> str:
    """An opaque token for the state a screen was built from. Changes on any edit."""
    payload = {
        "n": draft.name,
        "p": sorted(draft.prep_seconds_by_size.items()),
        "pe": draft.prep_is_estimate,
        "c": sorted(c.component_id or 0 for c in draft.components),
        "o": sorted(o.option_id or 0 for o in draft.options),
        "i": sorted(
            (it.menu_item_id or 0, it.price_pence, it.active, sorted(it.option_keys.items()))
            for it in draft.items
        ),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    return digest[:16]


def changeset_context(session: Session, draft: TemplateDraft) -> ChangesetContext:
    ingredients = list(
        session.execute(
            select(Ingredient.id, Ingredient.name, Ingredient.unit, Ingredient.retired_at)
        )
    )
    others = {
        name.casefold()
        for name in session.scalars(
            select(DrinkTemplate.name).where(DrinkTemplate.id != draft.template_id)
        )
    }
    taken = {
        (name.casefold(), size.value if size is not None else SizeCode.ONE.value)
        for name, size in session.execute(
            select(MenuItem.name, MenuItem.size_code).where(
                or_(MenuItem.template_id.is_(None), MenuItem.template_id != draft.template_id)
            )
        )
    }
    swaps: dict[ComponentRole, list[str]] = {}
    for name, role in session.execute(
        select(Modifier.name, Modifier.target_role).where(
            Modifier.is_active.is_(True), Modifier.action == ModifierAction.SUBSTITUTE
        )
    ):
        swaps.setdefault(role, []).append(name)
    return ChangesetContext(
        ingredient_names={i: n for i, n, _u, _r in ingredients},
        ingredient_units={i: u for i, _n, u, _r in ingredients},
        retired_ingredient_ids=frozenset(i for i, _n, _u, r in ingredients if r is not None),
        season_names=dict(session.execute(select(Season.id, Season.name)).tuples().all()),
        other_template_names=frozenset(others),
        taken_item_names=frozenset(taken),
        item_name_pattern=_item_name_pattern(draft),
        substitute_modifiers_by_role={role: tuple(sorted(n)) for role, n in swaps.items()},
    )


def _item_name_pattern(draft: TemplateDraft) -> str:
    """How this recipe names its items: "Vanilla Latte" -> "{flavour} Latte"."""
    patterns: Counter[str] = Counter()
    for item in draft.items:
        for key in item.option_keys.values():
            option = draft.option(key)
            if option is None:
                continue
            if item.name.casefold().startswith(option.name.casefold()):
                patterns["{flavour}" + item.name[len(option.name) :]] += 1
            elif item.name.casefold().endswith(option.name.casefold()):
                patterns[item.name[: -len(option.name)] + "{flavour}"] += 1
    if patterns:
        return patterns.most_common(1)[0][0]
    stem = draft.name.removeprefix("Flavoured ").strip()
    return "{flavour} " + stem


# --------------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChangesetPreview:
    template_id: int
    version: str
    at: datetime
    outcome: ChangesetOutcome
    impact: ChangeImpact
    loaded_hourly_rate_pence: int | None
    items_created: tuple[ItemChange, ...] = ()
    items_deactivated: tuple[ItemChange, ...] = ()


def _item_changes(
    session: Session,
    outcome: ChangesetOutcome,
    *,
    at: datetime,
    window_days: int,
    rate: int | None,
) -> list[ItemChange]:
    snapshots = snapshots_at(session, at)
    ids = [it.menu_item_id for it in outcome.before.items if it.menu_item_id is not None]
    until = at.astimezone(settings.tz).date()
    since = until - timedelta(days=window_days - 1)
    volumes = SqlMenuCostRepository(session).units_sold_bulk(ids, since=since, until=until)
    before = {it.key: it for it in outcome.before.items}
    out: list[ItemChange] = []
    for item in outcome.after.items:
        was = before.get(item.key)
        before_recipe = (
            resolve_recipe(item_spec_from_draft(outcome.before, was), (), at, ingredients=snapshots)
            if was is not None
            else None
        )
        after_recipe = resolve_recipe(
            item_spec_from_draft(outcome.after, item), (), at, ingredients=snapshots
        )
        out.append(
            ItemChange(
                key=item.key,
                menu_item_id=item.menu_item_id,
                name=item.name,
                size_code=item.size_code,
                before=before_recipe,
                after=after_recipe,
                price_before=was.price_pence if was is not None else None,
                price_after=item.price_pence,
                exists_before=was is not None,
                active_before=was.active if was is not None else False,
                active_after=item.active,
                prep_before=item_prep(outcome.before, was) if was is not None else UNTIMED,
                prep_after=item_prep(outcome.after, item),
                units_sold=volumes.get(item.menu_item_id, Decimal("0"))
                if item.menu_item_id is not None
                else Decimal("0"),
                loaded_hourly_rate_pence=rate,
                on_till=item.on_till,
            )
        )
    return out


def preview_changeset(
    session: Session,
    template_id: int,
    ops: Sequence[TemplateOp],
    *,
    window_days: int = WINDOW_DAYS,
    loaded_hourly_rate_pence: int | None = None,
) -> ChangesetPreview:
    """What the changeset would do. WRITES NOTHING.

    `loaded_hourly_rate_pence` is a what-if for the staff-time figures only (spec C-6):
    it is never persisted, and the apply always uses the configured rate.
    """
    at = datetime.now(UTC)
    draft = load_template_draft(session, template_id, at)
    outcome = apply_template_ops(draft, ops, changeset_context(session, draft))
    rate = (
        loaded_hourly_rate_pence
        if loaded_hourly_rate_pence is not None
        else configured_rate_pence()
    )
    changes = _item_changes(session, outcome, at=at, window_days=window_days, rate=rate)
    impact = summarise_changes(changes, window_days=window_days)
    return ChangesetPreview(
        template_id=template_id,
        version=template_version(draft),
        at=at,
        outcome=outcome,
        impact=impact,
        loaded_hourly_rate_pence=rate,
        items_created=tuple(c for c in changes if c.status == "new"),
        items_deactivated=tuple(c for c in changes if c.status == "off"),
    )


# --------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------


@dataclass
class ChangesetApplied:
    template_id: int
    effective_from: datetime
    new_version: str
    summary: str
    diff: tuple[str, ...]
    component_ids_opened: list[int] = field(default_factory=list)
    component_ids_closed: list[int] = field(default_factory=list)
    option_ids_opened: list[int] = field(default_factory=list)
    option_ids_closed: list[int] = field(default_factory=list)
    menu_items_created: list[int] = field(default_factory=list)
    menu_items_repriced: list[int] = field(default_factory=list)
    menu_items_switched: list[int] = field(default_factory=list)
    rollup_items_recosted: int = 0
    rollup_summary: str = ""
    pos_actions: tuple[str, ...] = ()


def change_summary(diff: Sequence[str]) -> str:
    """The history row's text: the first three lines, then `+N more` (design V1.8)."""
    if not diff:
        return "No change"
    head = " · ".join(diff[:3])
    return head + (f" · +{len(diff) - 3} more" if len(diff) > 3 else "")


def _store_qty(qty_by_size: dict[str, Decimal]) -> dict[str, str]:
    return {size: qty_text(qty) for size, qty in sorted(qty_by_size.items())}


def _component_changed(a: DraftComponent, b: DraftComponent) -> bool:
    return (
        a.role is not b.role
        or a.ingredient_id != b.ingredient_id
        or dict(a.qty_by_size) != dict(b.qty_by_size)
        or a.is_substitutable != b.is_substitutable
        or a.is_required != b.is_required
    )


def _option_changed(a: DraftOption, b: DraftOption) -> bool:
    return (
        a.ingredient_id != b.ingredient_id
        or dict(a.qty_by_size or {}) != dict(b.qty_by_size or {})
        or a.price_delta_pence != b.price_delta_pence
        or a.season_id != b.season_id
    )


def apply_changeset(
    session: Session,
    template_id: int,
    ops: Sequence[TemplateOp],
    *,
    base_version: str,
    actor: str,
    effective_from: datetime | None = None,
) -> ChangesetApplied:
    """Apply the changeset from `effective_from` (now). Flushes; the caller commits."""
    at = require_not_retroactive(effective_from or datetime.now(UTC))
    actor = require_actor(actor, error=ValueError)

    draft = load_template_draft(session, template_id, at)
    version = template_version(draft)
    if version != base_version:
        raise StaleVersionError(
            "this recipe changed after the page was loaded (another edit, a price or a "
            "flavour toggled elsewhere). Nothing was applied. Reload it and make the edit "
            "again on the current recipe."
        )
    outcome = apply_template_ops(draft, ops, changeset_context(session, draft))
    if outcome.refusals:
        raise ChangesetRefusedError(
            "Nothing was applied: "
            + " ".join(f"({r.op_index + 1}) {r.message}." for r in outcome.refusals)
        )
    if not outcome.diff:
        raise ValueError("there is nothing to apply: the changes leave the recipe as it is")

    template = session.get(DrinkTemplate, template_id)
    if template is None:  # pragma: no cover -- load_template_draft checked
        raise LookupError(f"recipe {template_id} not found")
    result = ChangesetApplied(
        template_id=template_id,
        effective_from=at,
        new_version="",
        summary=change_summary(outcome.diff),
        diff=outcome.diff,
        pos_actions=outcome.pos_actions,
    )

    _write_components(session, template_id, outcome, at, result)
    option_ids = _write_options(session, outcome, at, result)
    _write_items(session, template, outcome, option_ids, at, actor, result)

    template.name = outcome.after.name
    template.prep_seconds_by_size = dict(outcome.after.prep_seconds_by_size)
    template.prep_seconds_is_estimate = outcome.after.prep_is_estimate

    session.add(
        RecipeChange(
            template_id=template_id,
            change_kind="template_changeset",
            effective_from=at,
            actor=actor,
            summary=result.summary,
            lines=list(outcome.diff),
        )
    )
    session.flush()
    rollup = rollup_for_template(
        session, template_id, at=at, trigger=f"recipe changeset by {actor}"
    )
    result.rollup_items_recosted = rollup.costed
    result.rollup_summary = rollup.summary()
    session.flush()

    result.new_version = template_version(
        load_template_draft(session, template_id, datetime.now(UTC))
    )
    return result


def _write_components(
    session: Session,
    template_id: int,
    outcome: ChangesetOutcome,
    at: datetime,
    result: ChangesetApplied,
) -> None:
    before = {c.key: c for c in outcome.before.components}
    after = {c.key: c for c in outcome.after.components}
    for key, old in before.items():
        if old.component_id is None:  # pragma: no cover -- drafts load real rows only
            continue
        row = session.get(TemplateComponent, old.component_id)
        if row is None or row.effective_to is not None:
            raise StaleVersionError(
                f"component {old.component_id} is no longer the live row; reload the recipe"
            )
        new = after.get(key)
        if new is None:
            row.effective_to = at
            result.component_ids_closed.append(row.id)
            continue
        if not _component_changed(old, new):
            continue
        successor = TemplateComponent(
            template_id=template_id,
            role=new.role,
            ingredient_id=new.ingredient_id,
            qty_by_size=_store_qty(dict(new.qty_by_size)),
            is_substitutable=new.is_substitutable,
            is_required=new.is_required,
            effective_from=at,
            effective_to=None,
            note=row.note,
        )
        session.add(successor)
        session.flush()
        row.effective_to = at
        row.superseded_by_id = successor.id
        result.component_ids_closed.append(row.id)
        result.component_ids_opened.append(successor.id)
    for key, new in after.items():
        if key in before:
            continue
        added = TemplateComponent(
            template_id=template_id,
            role=new.role,
            ingredient_id=new.ingredient_id,
            qty_by_size=_store_qty(dict(new.qty_by_size)),
            is_substitutable=new.is_substitutable,
            is_required=new.is_required,
            effective_from=at,
            effective_to=None,
        )
        session.add(added)
        session.flush()
        result.component_ids_opened.append(added.id)


def _write_options(
    session: Session, outcome: ChangesetOutcome, at: datetime, result: ChangesetApplied
) -> dict[str, int]:
    """Write flavour changes; returns draft key -> the live option id after the apply."""
    before = {o.key: o for o in outcome.before.options}
    ids: dict[str, int] = {}
    for new in outcome.after.options:
        old = before.get(new.key)
        if old is None:
            row = VariantOption(
                axis_id=new.axis_id,
                name=new.name,
                ingredient_id=new.ingredient_id,
                qty_by_size=_store_qty(dict(new.qty_by_size)) if new.qty_by_size else None,
                price_delta_pence=new.price_delta_pence,
                season_id=new.season_id,
                effective_from=at,
            )
            session.add(row)
            session.flush()
            ids[new.key] = row.id
            result.option_ids_opened.append(row.id)
            continue
        if old.option_id is None:  # pragma: no cover
            continue
        current = session.get(VariantOption, old.option_id)
        if current is None or current.effective_to is not None:
            raise StaleVersionError(f"flavour {old.name!r} changed elsewhere; reload the recipe")
        if new.removed:
            current.effective_to = at
            ids[new.key] = current.id
            result.option_ids_closed.append(current.id)
            continue
        if new.name != old.name:
            # A rename renames the whole lineage, so a sale from before the rename still
            # finds its flavour (db/repositories/composition.py follows axis + name).
            for version in session.scalars(
                select(VariantOption).where(
                    VariantOption.axis_id == old.axis_id, VariantOption.name == old.name
                )
            ):
                version.name = new.name
            session.flush()
        if _option_changed(old, new):
            successor = VariantOption(
                axis_id=new.axis_id,
                name=new.name,
                ingredient_id=new.ingredient_id,
                qty_by_size=_store_qty(dict(new.qty_by_size)) if new.qty_by_size else None,
                price_delta_pence=new.price_delta_pence,
                season_id=new.season_id,
                effective_from=at,
            )
            session.add(successor)
            session.flush()
            current.effective_to = at
            ids[new.key] = successor.id
            result.option_ids_closed.append(current.id)
            result.option_ids_opened.append(successor.id)
        else:
            ids[new.key] = current.id
    return ids


def _write_items(
    session: Session,
    template: DrinkTemplate,
    outcome: ChangesetOutcome,
    option_ids: dict[str, int],
    at: datetime,
    actor: str,
    result: ChangesetApplied,
) -> None:
    before = {it.key: it for it in outcome.before.items}
    for item in outcome.after.items:
        selected = {
            str(axis_id): option_ids[key]
            for axis_id, key in sorted(item.option_keys.items())
            if key in option_ids
        }
        was = before.get(item.key)
        if item.menu_item_id is None:
            row = MenuItem(
                name=item.name,
                category=template.category,
                template_id=template.id,
                size_code=item.size_code,
                selected_options=selected,
                price_pence=item.price_pence,
                active=item.active,
                manual_recipe=False,
            )
            session.add(row)
            session.flush()
            set_menu_price(
                session, row, item.price_pence, at=at, actor=actor, note="new flavour", force=True
            )
            result.menu_items_created.append(row.id)
            continue
        existing = session.get(MenuItem, item.menu_item_id)
        if existing is None:  # pragma: no cover
            raise StaleVersionError(f"menu item {item.menu_item_id} disappeared; reload")
        if dict(existing.selected_options or {}) != selected and selected:
            existing.selected_options = selected
            result.menu_items_switched.append(existing.id)
        if was is not None and was.price_pence != item.price_pence:
            set_menu_price(
                session, existing, item.price_pence, at=at, actor=actor, note="recipe base price"
            )
            result.menu_items_repriced.append(existing.id)
        if existing.active != item.active:
            existing.active = item.active
