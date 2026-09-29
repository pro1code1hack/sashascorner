"""Editing a recipe quantity, with the consequences shown first. Spec 5.5.

Two entry points and one rule between them:

- `preview_component_qty_change` writes nothing and answers "what would this do".
- `apply_component_qty_change` does it, in ONE transaction, effective from now.

**There is no retroactive mode, and adding one would be a bug, not a feature.**
Effective dating is what keeps history honest (invariant 3): March's consumption was
computed from March's recipe, and the drift metric -- the number the whole product's
trustworthiness rests on -- is only meaningful while that stays true. An edit
therefore CLOSES the live component row and OPENS a new one from today.
`CompositionRepository.close_and_open_component` does that, and refuses to touch a
row that is already closed.

The impact preview is deliberately computed BEFORE the write, from the same
quantities the write will use, so what the reviewer approved is what lands.

v2 extends the preview with **labour** (spec 5.6). A recipe change moves two things
the old preview could not see: TRUE margin, which is net of labour, and
margin-per-minute, which can reorder the menu. Labour cost itself does not move -- a
recipe edit changes what is in the cup, not how long it takes -- and reporting that it
did not move is worth as much as reporting that it did, because it is the number a
reviewer would otherwise assume changed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from cafeops.config import settings
from cafeops.db.repositories.composition import SqlCompositionRepository
from cafeops.db.repositories.menu_cost import SqlMenuCostRepository
from cafeops.domain.composition import (
    ItemImpact,
    LabourImpact,
    preview_labour_impact,
    resolve_recipe,
)
from cafeops.domain.labour import UNTIMED
from cafeops.domain.types import (
    ImpactPreview,
    MenuItemSpec,
    SizeCode,
    SubstitutionError,
)
from cafeops.jobs.cost_rollup import (
    RollupReport,
    configured_rate_pence,
    rollup_for_template,
    snapshots_at,
)

__all__ = [
    "EditResult",
    "RetroactiveEditError",
    "apply_component_qty_change",
    "preview_component_qty_change",
    "preview_component_qty_change_with_labour",
    "qty_map_with",
    "require_not_retroactive",
]

#: Spec 5.5: the COGS projection uses the last 30 days of real sales volume.
COGS_WINDOW_DAYS = 30


class RetroactiveEditError(ValueError):
    """An edit was asked to take effect before today.

    Invariant 3: history is never rewritten. Backdating a component would silently
    change what past sales are deemed to have consumed, which corrupts drift and
    every cost already reported. If a past recipe was recorded wrongly, the honest
    repair is an explicit correction with its own audit trail -- not an edit that
    quietly rewrites what happened.
    """


@dataclass(frozen=True, slots=True)
class EditResult:
    component_id: int
    new_component_id: int
    template_id: int
    effective_from: datetime
    qty_by_size_before: dict[str, str]
    qty_by_size_after: dict[str, str]
    preview: ImpactPreview
    rollup: RollupReport
    #: The same preview with spec 5.6's labour consequences. `labour.preview is preview`.
    labour: LabourImpact | None = None

    def summary(self) -> str:
        return (
            f"component {self.component_id} -> {self.new_component_id}, effective "
            f"{self.effective_from.isoformat()}; {self.preview.affected_item_count} "
            f"item(s) affected; {self.rollup.summary()}"
        )


def qty_map_with(current: dict[str, str], size_code: SizeCode, qty: Decimal) -> dict[str, str]:
    """The component's quantity map with one size changed.

    Strings, not floats: `qty_by_size` is JSON and JSON has only doubles. A recipe
    editor that turns 0.1 + 0.2 into 0.30000000000000004 is not acceptable.
    """
    out = dict(current)
    out[size_code.value] = format(qty, "f")
    return out


def preview_component_qty_change_with_labour(
    session: Session,
    component_id: int,
    *,
    qty_by_size: dict[str, str],
    at: datetime | None = None,
    window_days: int = COGS_WINDOW_DAYS,
    loaded_hourly_rate_pence: int | None = None,
) -> LabourImpact:
    """What changing this component's quantities would do, cost AND labour. Writes nothing.

    The richer of the two preview entry points, and the one `apply_...` uses. There is
    one code path: the cost-only `preview_component_qty_change` returns
    `.preview` from this, so a reviewer cannot be shown one set of affected items and
    have a different set committed.
    """
    at = at or datetime.now(UTC)
    rate = (
        loaded_hourly_rate_pence
        if loaded_hourly_rate_pence is not None
        else configured_rate_pence()
    )
    candidates, warnings = _candidates(
        session,
        component_id,
        qty_by_size=qty_by_size,
        at=at,
        window_days=window_days,
        loaded_hourly_rate_pence=rate,
    )
    return preview_labour_impact(candidates, window_days=window_days, extra_warnings=warnings)


def preview_component_qty_change(
    session: Session,
    component_id: int,
    *,
    qty_by_size: dict[str, str],
    at: datetime | None = None,
    window_days: int = COGS_WINDOW_DAYS,
) -> ImpactPreview:
    """The spec 5.5 preview alone, for callers that do not want the labour half."""
    return preview_component_qty_change_with_labour(
        session,
        component_id,
        qty_by_size=qty_by_size,
        at=at,
        window_days=window_days,
    ).preview


def apply_component_qty_change(
    session: Session,
    component_id: int,
    *,
    qty_by_size: dict[str, str],
    actor: str,
    effective_from: datetime | None = None,
    window_days: int = COGS_WINDOW_DAYS,
) -> EditResult:
    """Apply the edit from today, then recost everything it touched. Flushes; the
    caller commits, so all of it is one transaction.

    The sequence matters:

    1. Preview at `effective_from`, so the recorded consequences are the ones that
       are about to become true.
    2. Close the old component row and open the new one.
    3. Recost every menu item on the template, into `menu_item_cost`.

    All three land together or not at all. A committed edit with a stale cost cache
    would have the margin screen quietly disagreeing with the recipe.
    """
    effective_from = require_not_retroactive(effective_from or datetime.now(UTC))
    composition = SqlCompositionRepository(session)

    component = composition.live_component(component_id)
    if component is None:
        raise LookupError(f"template_component {component_id} not found")
    before_map = dict(component.qty_by_size)
    after_map = _validated(qty_by_size)

    labour = preview_component_qty_change_with_labour(
        session,
        component_id,
        qty_by_size=after_map,
        at=effective_from,
        window_days=window_days,
    )
    preview = labour.preview
    new_id = composition.close_and_open_component(
        component_id, qty_by_size=after_map, effective_from=effective_from
    )
    session.flush()
    rollup = rollup_for_template(
        session,
        component.template_id,
        at=effective_from,
        trigger=f"composition edit by {actor}",
    )
    session.flush()

    return EditResult(
        component_id=component_id,
        new_component_id=new_id,
        template_id=component.template_id,
        effective_from=effective_from,
        qty_by_size_before=before_map,
        qty_by_size_after=after_map,
        preview=preview,
        rollup=rollup,
        labour=labour,
    )


# --------------------------------------------------------------------------


def require_not_retroactive(effective_from: datetime) -> datetime:
    """Refuse anything before the start of today, local time.

    "Apply from today" is the only mode (invariant 3). The boundary is the local
    trading day rather than UTC midnight, because a café's day is the unit a human
    means by "today".
    """
    tz = settings.tz
    start_of_today = datetime.combine(datetime.now(tz).date(), time.min, tzinfo=tz)
    if effective_from < start_of_today:
        raise RetroactiveEditError(
            f"effective_from {effective_from.isoformat()} is before the start of today "
            f"({start_of_today.isoformat()}). Recipe edits apply from today only -- "
            "history is never rewritten (invariant 3)."
        )
    return effective_from


def _validated(qty_by_size: dict[str, str]) -> dict[str, str]:
    """Quantities must be parseable, non-negative Decimals held as strings."""
    out: dict[str, str] = {}
    for raw_size, raw_qty in qty_by_size.items():
        size = raw_size.strip().upper()
        try:
            SizeCode(size)
        except ValueError as exc:
            raise ValueError(f"{raw_size!r} is not a size code (S, M, XL, ONE)") from exc
        if isinstance(raw_qty, float):  # pragma: no cover -- defensive, invariant 8
            raise TypeError(f"quantity for size {size} is a float; use a string or Decimal")
        try:
            qty = Decimal(str(raw_qty))
        except InvalidOperation as exc:
            raise ValueError(f"quantity {raw_qty!r} for size {size} is not a number") from exc
        if qty < 0:
            raise ValueError(f"quantity {qty} for size {size} is negative")
        out[size] = format(qty, "f")
    return out


def _candidates(
    session: Session,
    component_id: int,
    *,
    qty_by_size: dict[str, str],
    at: datetime,
    window_days: int,
    loaded_hourly_rate_pence: int | None = None,
) -> tuple[list[ItemImpact], list[str]]:
    """Resolve every item on the template twice: as it is, and as it would be.

    The "after" side is built by substituting the component's quantity into the spec
    the repository produced -- not by writing the row and reading it back. A preview
    that mutates the database to find out what it would do is not a preview.
    """
    composition = SqlCompositionRepository(session)
    costs = SqlMenuCostRepository(session)

    component = composition.live_component(component_id)
    if component is None:
        raise LookupError(f"template_component {component_id} not found")

    item_ids = composition.menu_item_ids_for_component(component_id)
    warnings: list[str] = []
    if not item_ids:
        warnings.append(
            f"no menu item resolves through template {component.template_id}; this edit "
            "changes nothing that is sold"
        )
        return ([], warnings)

    specs = composition.item_specs(item_ids, at)
    snapshots = snapshots_at(session, at)

    until = at.astimezone(settings.tz).date()
    since = until - timedelta(days=window_days - 1)
    volumes = costs.units_sold_bulk(item_ids, since=since, until=until)
    prep_times = composition.prep_times(item_ids)
    # Seasons only ever add a warning to a resolution; see `domain/composition.py`.
    option_seasons = composition.option_seasons_for_items(item_ids)
    item_seasons = composition.item_seasons(item_ids)

    candidates: list[ItemImpact] = []
    for item_id in item_ids:
        spec = specs.get(item_id)
        if spec is None:
            continue
        try:
            before = resolve_recipe(
                spec,
                (),
                at,
                ingredients=snapshots,
                option_seasons=option_seasons.get(item_id),
                item_season=item_seasons.get(item_id),
            )
            after = resolve_recipe(
                _with_component_qty(spec, component_id, qty_by_size),
                (),
                at,
                ingredients=snapshots,
                option_seasons=option_seasons.get(item_id),
                item_season=item_seasons.get(item_id),
            )
        except SubstitutionError as exc:  # pragma: no cover -- no modifiers are applied
            warnings.append(f"{spec.name}: {exc}")
            continue
        candidates.append(
            ItemImpact(
                menu_item_id=item_id,
                name=spec.name,
                size_code=spec.size_code,
                price_pence=spec.price_pence,
                before=before,
                after=after,
                units_sold=volumes.get(item_id, Decimal("0")),
                prep=prep_times.get(item_id, UNTIMED),
                loaded_hourly_rate_pence=loaded_hourly_rate_pence,
                template_id=spec.template_id,
            )
        )

    unwarned = _unresolved(specs, item_ids)
    if unwarned:
        warnings.append(
            f"{len(unwarned)} menu item(s) on this template could not be resolved and are "
            "not counted in the figures below"
        )
    return (candidates, warnings)


def _with_component_qty(
    spec: MenuItemSpec, component_id: int, qty_by_size: dict[str, str]
) -> MenuItemSpec:
    """The same item spec with one component's quantity replaced at this item's size."""
    key = spec.size_code.value if spec.size_code else SizeCode.ONE.value
    raw = qty_by_size.get(key)
    qty = None if raw is None else Decimal(str(raw))
    components = tuple(
        replace(component, qty=qty) if component.component_id == component_id else component
        for component in spec.components
    )
    return replace(spec, components=components)


def _unresolved(specs: dict[int, MenuItemSpec], item_ids: list[int]) -> list[int]:
    return [item_id for item_id in item_ids if item_id not in specs]
