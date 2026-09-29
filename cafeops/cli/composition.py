"""Composition: proposals, templates, recipe edits, cost rollup, margins, availability,
prep times and ingredient prices (Agent B)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated

import typer
from rich.table import Table

from cafeops.cli._common import console, money, parse_as_of, pence, unit_of_work
from cafeops.config import settings
from cafeops.db.base import session_scope

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cafeops.domain.composition import LabourImpact
    from cafeops.domain.labour import PrepTime
    from cafeops.domain.types import ImpactPreview, IngredientSnapshot
    from cafeops.jobs.cost_rollup import RollupReport


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}%"


def _render_preview(preview: ImpactPreview, header: str) -> None:
    """Spec 5.5's impact preview, in the shape the brief asks for."""
    console.print(f"\n[bold]{header}[/bold]")
    console.print(f"  Affects {preview.affected_item_count} menu item(s)")
    console.print(f"  Cost per item        {money(preview.cost_delta_pence_per_item, signed=True)}")
    console.print(
        f"  COGS, last 30 days   {money(preview.monthly_cogs_delta_pence, signed=True, dp=2)}"
    )
    worst = preview.worst_margin_after
    if worst is None:
        console.print("  Lowest margin after  [yellow]not computable (no priced item)[/yellow]")
    else:
        size = f" {worst.size_code.value}" if worst.size_code else ""
        console.print(
            f"  Lowest margin after  {worst.name}{size}, "
            f"{_pct(worst.margin_pct(worst.cost_before_pence))} -> "
            f"{_pct(worst.margin_pct(worst.cost_after_pence))}"
        )
    for warning in preview.warnings:
        console.print(f"  [yellow]warning[/yellow] {warning}")


def _prep(prep: PrepTime | None) -> str:
    """Compact prep time. A trailing `*` marks an ESTIMATE; the footnote says so."""
    if prep is None or prep.seconds is None:
        return "[yellow]-[/yellow]"
    return f"{prep.seconds}s{'*' if prep.is_estimate else ''}"


def _range(
    value: Decimal | None, spread: tuple[Decimal, Decimal] | None, *, signed: bool = False
) -> str:
    """One figure when the affected items agree, else the range they span.

    Never an average: a mean across items that disagree describes no menu item, and
    the label beside it says "per item".
    """
    if value is not None:
        return money(value, signed=signed)
    if spread is not None:
        low, high = spread
        return f"{money(low, signed=signed)} to {money(high, signed=signed)} [dim](varies)[/dim]"
    return "[yellow]unknown[/yellow]"


def _render_labour(labour: LabourImpact | None) -> None:
    """Spec 5.6's half of the preview: what the edit does to labour and to throughput.

    Labour cost is printed even though a quantity edit never moves it, because "did
    that change my labour cost?" is the first thing a reader wonders and an absent line
    does not answer it.
    """
    if labour is None:
        return
    console.print("\n  [bold]Labour and throughput[/bold] (spec 5.6)")
    per_item = _range(labour.labour_cost_pence_per_item, labour.labour_cost_pence_range)
    true_delta = _range(
        labour.true_margin_delta_pence_per_item,
        labour.true_margin_delta_pence_range,
        signed=True,
    )
    console.print(
        f"  Labour per item      {per_item}"
        "  [dim](unchanged by a recipe edit -- prep time did not move)[/dim]"
    )
    console.print(f"  True margin delta    {true_delta}")
    worst_true = labour.worst_true_margin_after
    if worst_true is not None:
        console.print(
            f"  Lowest TRUE margin   {worst_true.label}, "
            f"{_pct(worst_true.before.true_margin_pct)} -> {_pct(worst_true.after.true_margin_pct)}"
        )
    worst_min = labour.worst_margin_per_minute_after
    if worst_min is not None:
        console.print(
            f"  Worst margin/minute  {worst_min.label}, "
            f"{pence(worst_min.before.margin_per_minute_pence)} -> "
            f"{pence(worst_min.after.margin_per_minute_pence)}"
        )
    if labour.rank_moves:
        console.print("  Margin/minute order MOVED among the affected items:")
        for label, was, now in labour.rank_moves[:6]:
            console.print(f"    {label}: #{was} -> #{now}")
    elif labour.ranking_after is not None and labour.ranking_after.ranked:
        console.print("  [dim]margin/minute order unchanged among the affected items[/dim]")
    for warning in labour.warnings:
        console.print(f"  [yellow]warning[/yellow] {warning}")


def _render_rollup(rollup: RollupReport | None) -> None:
    if rollup is None:
        return
    console.print(f"  [dim]{rollup.summary()}[/dim]")
    for warning in rollup.warnings[:5]:
        console.print(f"  [yellow]warning[/yellow] {warning}")
    if len(rollup.warnings) > 5:
        console.print(f"  [dim]... {len(rollup.warnings) - 5} more warning(s)[/dim]")


def proposals(
    limit: Annotated[int, typer.Option(help="How many proposals to print.")] = 30,
    conflicts_only: Annotated[
        bool, typer.Option("--conflicts-only", help="Only proposals a human must adjudicate.")
    ] = False,
) -> None:
    """List the template proposals waiting for confirmation (spec 6 pass 3)."""
    from cafeops.services.materialise_template import list_proposals

    with session_scope() as session:
        found = [p for p in list_proposals(session) if not p.is_singleton]

    if conflicts_only:
        found = [p for p in found if p.conflicts]
    if not found:
        console.print("[yellow]No proposals. Run `cafeops seed --demo` first.[/yellow]")
        return

    table = Table(
        title=f"{len(found)} proposed template(s) -- nothing is written until confirmed",
        title_style="bold",
    )
    table.add_column("id", no_wrap=True, style="dim")
    table.add_column("Proposal", no_wrap=True)
    table.add_column("Items", justify="right")
    table.add_column("Sizes")
    table.add_column("Fixed", justify="right")
    table.add_column("Axes")
    table.add_column("Conflicts", justify="right")
    for proposal in found[:limit]:
        axes = ", ".join(f"{a.name}x{a.option_count}" for a in proposal.axes) or "-"
        conflict_text = f"[yellow]{len(proposal.conflicts)}[/yellow]" if proposal.conflicts else "-"
        shared = sum(1 for other in found if other.name == proposal.name) > 1
        name_cell = f"{proposal.name} [yellow](name shared)[/yellow]" if shared else proposal.name
        if proposal.is_hollow:
            name_cell += " [red](no recipe)[/red]"
        table.add_row(
            proposal.proposal_id,
            name_cell,
            str(proposal.menu_item_count),
            "/".join(proposal.sizes),
            str(sum(1 for c in proposal.components if not c.is_axis_filled)),
            axes,
            conflict_text,
        )
    console.print(table)
    console.print(
        "[dim]A proposal with conflicts is REFUSED by default: the legacy rows disagree "
        "about a quantity and a human decides which is right.[/dim]"
    )
    shared_names = {p.name for p in found if sum(1 for q in found if q.name == p.name) > 1}
    if shared_names:
        console.print(
            f"[yellow]{len(shared_names)} name(s) are shared by more than one proposal[/yellow] "
            "-- detection names a group after its defining ingredient, so two different "
            "recipes can collide. Pass the id, not the name: confirming by an ambiguous "
            "name is refused rather than resolved to whichever came first."
        )
    hollow = [p for p in found if p.is_hollow]
    if hollow:
        console.print(
            f"[red]{len(hollow)} proposal(s) contain no recipe at all[/red] "
            f"({', '.join(p.name for p in hollow)}): the legacy rows carry no ingredient "
            "lines. Confirming is refused -- it would create an empty template and strip "
            "those items of the manual recipes they use today."
        )


def materialise_template_cmd(
    name: Annotated[
        str,
        typer.Argument(help="Proposal id (preferred) or name, as printed by `cafeops proposals`."),
    ],
    commit: Annotated[
        bool, typer.Option("--commit/--dry-run", help="Write it, or just say what it would write.")
    ] = False,
    allow_conflicts: Annotated[
        bool,
        typer.Option(
            "--allow-conflicts",
            help="Accept the lowest quantity where the legacy rows disagree.",
        ),
    ] = False,
    actor: Annotated[str, typer.Option("--actor", help="Who confirmed this proposal.")] = "cli",
) -> None:
    """Materialise a CONFIRMED proposal into real composition rows."""
    from cafeops.services.materialise_template import (
        ProposalAlreadyMaterialised,
        ProposalHasConflicts,
        ProposalNotFound,
        find_proposal,
        materialise_proposal,
    )

    with unit_of_work(commit=commit) as session:
        if not commit:
            try:
                proposal = find_proposal(session, name)
            except ProposalNotFound as exc:
                raise typer.BadParameter(str(exc)) from exc
            console.print(f"[bold]Would materialise:[/bold] {proposal.name}")
            console.print(
                f"  {proposal.menu_item_count} menu item row(s) across "
                f"{len(proposal.base_item_names)} base item(s), sizes "
                f"{'/'.join(proposal.sizes)}"
            )
            for component in proposal.components:
                target = component.ingredient_name or "[variant axis fills this slot]"
                console.print(f"    {component.role.value:<10} {target}  {component.qty_by_size}")
            for axis in proposal.axes:
                console.print(
                    f"    axis {axis.name!r} ({axis.role.value}) -- "
                    f"{axis.option_count} option(s): {sorted(axis.options)}"
                )
            if proposal.conflicts:
                console.print(
                    f"  [yellow]{len(proposal.conflicts)} conflict(s) -- refused without "
                    "--allow-conflicts[/yellow]"
                )
                for conflict in proposal.conflicts:
                    console.print(f"    [yellow]{conflict.describe()}[/yellow]")
            console.print("\n[yellow]DRY RUN: nothing written. Re-run with --commit.[/yellow]")
            return

        try:
            report = materialise_proposal(
                session, name, actor=actor, allow_conflicts=allow_conflicts
            )
        except (ProposalNotFound, ProposalHasConflicts, ProposalAlreadyMaterialised) as exc:
            raise typer.BadParameter(str(exc)) from exc

    console.print(f"[green]materialised[/green] {report.summary()}")
    _render_rollup(report.rollup)
    for warning in report.warnings:
        console.print(f"  [yellow]warning[/yellow] {warning}")


def templates() -> None:
    """List materialised drink templates and how many items resolve through each."""
    from sqlalchemy import func, select

    from cafeops.db.models import DrinkTemplate, MenuItem, SizeProfile, VariantAxis

    with session_scope() as session:
        rows = list(session.scalars(select(DrinkTemplate).order_by(DrinkTemplate.name)))
        table = Table(title=f"{len(rows)} template(s)", title_style="bold")
        table.add_column("#", justify="right")
        table.add_column("Template", no_wrap=True)
        table.add_column("Category")
        table.add_column("Sizes")
        table.add_column("Axes")
        table.add_column("Items", justify="right")
        for template in rows:
            sizes = list(
                session.scalars(
                    select(SizeProfile.code)
                    .where(SizeProfile.template_id == template.id)
                    .order_by(SizeProfile.sort_order)
                )
            )
            axes = list(
                session.scalars(
                    select(VariantAxis.name).where(VariantAxis.template_id == template.id)
                )
            )
            items = session.scalar(
                select(func.count(MenuItem.id)).where(MenuItem.template_id == template.id)
            )
            table.add_row(
                str(template.id),
                template.name,
                template.category or "-",
                "/".join(s.value for s in sizes),
                ", ".join(axes) or "-",
                str(items or 0),
            )
    console.print(table)


def components(
    template: Annotated[str, typer.Argument(help="Template name.")],
    as_of: Annotated[str, typer.Option("--as-of", help="Which day's recipe to show.")] = "now",
) -> None:
    """Show one template's live components, with the ids `edit-recipe` takes."""
    from cafeops.db.repositories.composition import SqlCompositionRepository

    at = parse_as_of(as_of)
    with session_scope() as session:
        repo = SqlCompositionRepository(session)
        template_id = repo.template_id_by_name(template)
        if template_id is None:
            raise typer.BadParameter(f"no template named {template!r}; see `cafeops templates`")
        live = repo.live_components(template_id, at)

    local = at.astimezone(settings.tz)
    table = Table(
        title=f"{template} -- components in force at {local:%Y-%m-%d %H:%M %Z}",
        title_style="bold",
    )
    table.add_column("Component", justify="right")
    table.add_column("Role")
    table.add_column("Ingredient", no_wrap=True)
    table.add_column("Unit")
    table.add_column("Qty by size")
    table.add_column("Sub?", justify="center")
    table.add_column("Req?", justify="center")
    table.add_column("Since")
    for component in live:
        table.add_row(
            str(component.component_id),
            component.role.value,
            component.ingredient_name or "[dim]variant axis[/dim]",
            component.unit.value if component.unit else "-",
            ", ".join(f"{k}={v}" for k, v in sorted(component.qty_by_size.items())) or "-",
            "yes" if component.is_substitutable else "-",
            "yes" if component.is_required else "-",
            component.effective_from.astimezone(settings.tz).strftime("%Y-%m-%d"),
        )
    console.print(table)


def edit_recipe_cmd(
    component: Annotated[int, typer.Option("--component", help="Component id.")],
    size: Annotated[str, typer.Option("--size", help="Size code: S, M, XL or ONE.")],
    qty: Annotated[str, typer.Option("--qty", help="New quantity, in the ingredient's unit.")],
    commit: Annotated[
        bool, typer.Option("--commit/--preview", help="Apply the edit, or only preview it.")
    ] = False,
    actor: Annotated[str, typer.Option("--actor", help="Who is making the edit.")] = "cli",
) -> None:
    """Preview, then optionally apply, a per-size recipe quantity change.

    Applying is effective from today and never retroactive: the old component row is
    closed and a new one opened (invariant 3).
    """
    from cafeops.db.repositories.composition import SqlCompositionRepository
    from cafeops.domain.types import SizeCode
    from cafeops.services.edit_composition import (
        apply_component_qty_change,
        preview_component_qty_change_with_labour,
        qty_map_with,
    )

    try:
        size_code = SizeCode(size.strip().upper())
    except ValueError as exc:
        raise typer.BadParameter(f"{size!r}: expected S, M, XL or ONE") from exc
    try:
        new_qty = Decimal(qty)
    except Exception as exc:
        raise typer.BadParameter(f"{qty!r} is not a number") from exc

    with unit_of_work(commit=commit) as session:
        repo = SqlCompositionRepository(session)
        live = repo.live_component(component)
        if live is None:
            raise typer.BadParameter(f"no template_component {component}")
        before = live.qty_by_size.get(size_code.value, "-")
        qty_by_size = qty_map_with(live.qty_by_size, size_code, new_qty)
        unit = live.unit.value if live.unit else ""
        header = (
            f"{live.ingredient_name or live.role.value} {before} {unit} -> "
            f"{format(new_qty, 'f')} {unit} (size {size_code.value})"
        )

        if not commit:
            labour = preview_component_qty_change_with_labour(
                session, component, qty_by_size=qty_by_size
            )
            _render_preview(labour.preview, header)
            _render_labour(labour)
            console.print(
                "\n[yellow]PREVIEW: nothing written. Re-run with --commit to apply from "
                "today.[/yellow]"
            )
            return

        result = apply_component_qty_change(
            session, component, qty_by_size=qty_by_size, actor=actor
        )

    _render_preview(result.preview, header)
    _render_labour(result.labour)
    console.print(
        f"\n[green]applied[/green] component {result.component_id} closed, "
        f"{result.new_component_id} opened, effective "
        f"{result.effective_from.astimezone(settings.tz):%Y-%m-%d %H:%M %Z}"
    )
    _render_rollup(result.rollup)


def cost_rollup_cmd(
    template: Annotated[str | None, typer.Option("--template", help="Only this template.")] = None,
    ingredient: Annotated[
        str | None, typer.Option("--ingredient", help="Only what this ingredient touches.")
    ] = None,
    as_of: Annotated[
        str, typer.Option("--as-of", help="Cost the recipes as of this date (today or later).")
    ] = "now",
) -> None:
    """Refresh `menu_item_cost`. Default is every active menu item (spec 5.5)."""
    from cafeops.db.repositories.composition import SqlCompositionRepository
    from cafeops.jobs.cost_rollup import rollup_all, rollup_for_ingredient, rollup_for_template
    from cafeops.services.edit_composition import RetroactiveEditError, require_not_retroactive

    at = parse_as_of(as_of)
    try:
        # `menu_item_cost` is a CURRENT-state cache with one row per item. Rolling up
        # at a past date and storing the result would leave the margin screen showing
        # last month's costs as though they were today's. The rollup functions still
        # take `at` -- an edit rolls up at its own effective date -- but the CLI will
        # not poison the cache with a historical figure.
        require_not_retroactive(at)
    except RetroactiveEditError as exc:
        raise typer.BadParameter(
            f"{as_of!r} is in the past. menu_item_cost holds one CURRENT cost per item, "
            "so a backdated rollup would cache a figure that is no longer true. "
            f"({exc})"
        ) from exc
    with session_scope() as session:
        repo = SqlCompositionRepository(session)
        if template and ingredient:
            raise typer.BadParameter("pass --template or --ingredient, not both")
        if template:
            template_id = repo.template_id_by_name(template)
            if template_id is None:
                raise typer.BadParameter(f"no template named {template!r}")
            report = rollup_for_template(session, template_id, at=at)
        elif ingredient:
            snapshot = _ingredient_by_name(session, ingredient)
            report = rollup_for_ingredient(session, snapshot.id, at=at)
            console.print(
                f"[dim]cascade: {snapshot.name} -> {report.templates_in_scope} template(s) -> "
                f"{report.considered} menu item(s)[/dim]"
            )
        else:
            report = rollup_all(session, at=at)

    console.print(report.summary())
    for warning in report.warnings[:8]:
        console.print(f"  [yellow]warning[/yellow] {warning}")
    if len(report.warnings) > 8:
        console.print(f"  [dim]... {len(report.warnings) - 8} more warning(s)[/dim]")


def _ingredient_by_name(session: Session, raw: str) -> IngredientSnapshot:
    """Exact name, else a unique case-insensitive substring match."""
    from cafeops.db.repositories.ingredient import SqlIngredientRepository

    repo = SqlIngredientRepository(session)
    found = repo.get_by_name(raw)
    if found is not None:
        return found
    wanted = raw.strip().casefold()
    near = [i for i in repo.list_all() if wanted in i.name.casefold()]
    if len(near) == 1:
        return near[0]
    if not near:
        raise typer.BadParameter(f"no ingredient matching {raw!r}")
    raise typer.BadParameter(
        f"{raw!r} matches {len(near)} ingredients: {[i.name for i in near][:8]}"
    )


def menu_costs_cmd(
    limit: Annotated[int, typer.Option(help="Rows to print.")] = 25,
    template: Annotated[str | None, typer.Option("--template", help="Only this template.")] = None,
    missing: Annotated[
        bool, typer.Option("--missing", help="Only items whose cost is not fully known.")
    ] = False,
) -> None:
    """The materialised cost cache: cost, source and margin per menu item."""
    from sqlalchemy import select

    from cafeops.db.models import MenuItem
    from cafeops.db.repositories.composition import SqlCompositionRepository
    from cafeops.db.repositories.menu_cost import SqlMenuCostRepository

    with session_scope() as session:
        rows = SqlMenuCostRepository(session).list_all(only_missing=missing)
        if template:
            template_id = SqlCompositionRepository(session).template_id_by_name(template)
            if template_id is None:
                raise typer.BadParameter(f"no template named {template!r}")
            keep = set(
                session.scalars(select(MenuItem.id).where(MenuItem.template_id == template_id))
            )
            rows = [r for r in rows if r.menu_item_id in keep]

    if not rows:
        console.print("[yellow]Nothing costed yet. Run `cafeops cost-rollup`.[/yellow]")
        return

    known = [r for r in rows if r.cost_pence is not None]
    table = Table(
        title=f"{len(rows)} costed menu item(s), {len(rows) - len(known)} with an UNKNOWN cost",
        title_style="bold",
    )
    table.add_column("Item", no_wrap=True)
    table.add_column("Size", justify="center")
    table.add_column("Price", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("Margin", justify="right")
    table.add_column("Source")
    table.add_column("Ing", justify="right")
    for row in rows[:limit]:
        source = row.cost_source.value if row.cost_source else "[yellow]MISSING[/yellow]"
        if row.cost_source is not None and row.cost_source.value == "ESTIMATE":
            source = "[yellow]ESTIMATE[/yellow]"
        table.add_row(
            row.name,
            row.size_code.value if row.size_code else "-",
            money(row.price_pence, dp=2),
            money(row.cost_pence),
            _pct(row.margin_pct),
            source,
            str(row.ingredient_count),
        )
    console.print(table)
    if len(rows) > limit:
        console.print(f"[dim]... {len(rows) - limit} more[/dim]")
    console.print(
        "[dim]An UNKNOWN cost is excluded from every aggregate rather than counted as "
        "zero, and an ESTIMATE stays flagged as one (invariant 6).[/dim]"
    )


def margin(
    limit: Annotated[int, typer.Option(help="Rows per ranking.")] = 15,
    template: Annotated[str | None, typer.Option("--template", help="Only this template.")] = None,
    window_days: Annotated[
        int, typer.Option("--window-days", help="Sales window behind the volume weighting.")
    ] = 30,
    rate_pence: Annotated[
        int | None,
        typer.Option("--rate-pence", help="Override the loaded hourly rate, in PENCE."),
    ] = None,
    show_excluded: Annotated[
        bool, typer.Option("--show-excluded", help="List the items that cannot be ranked.")
    ] = False,
) -> None:
    """The menu ranked by margin % AND by margin-per-minute, side by side (spec 5.6).

    Two tables, deliberately not merged into one score. Margin % is what the menu was
    priced on; margin-per-minute is what matters when there is a queue, because the
    scarce resource at 11am is the person behind the counter and not the money. The two
    orderings disagree, and the disagreement is the finding.
    """
    from cafeops.services.menu_margin import menu_margin

    with session_scope() as session:
        try:
            view = menu_margin(
                session,
                template=template,
                window_days=window_days,
                loaded_hourly_rate_pence=rate_pence,
            )
        except LookupError as exc:
            raise typer.BadParameter(str(exc)) from exc

    rate = view.loaded_hourly_rate_pence
    rate_text = (
        "[yellow]NO loaded hourly rate configured[/yellow]"
        if rate is None
        else f"loaded rate GBP {rate / 100:.2f}/hr"
    )
    console.print(
        f"[bold]{view.items_costed} costed menu item(s)[/bold] -- {rate_text}; "
        f"volume over {view.since} to {view.until}"
    )

    ranking = view.ranking
    if not ranking.ranked:
        console.print(
            "[yellow]Nothing can be ranked: ranking needs an ingredient cost AND a prep "
            "time on the cached row.[/yellow]"
        )
        for item, reason in ranking.excluded[:limit]:
            console.print(f"  {item.label}: [yellow]{reason}[/yellow]")
        if len(ranking.excluded) > limit:
            console.print(f"  [dim]... {len(ranking.excluded) - limit} more[/dim]")
        for warning in view.warnings:
            console.print(f"  [yellow]warning[/yellow] {warning}")
        return

    table = Table(
        title=(
            f"{ranking.rankable_count} rankable item(s) -- BY MARGIN % (left) vs "
            "BY MARGIN PER MINUTE (right)"
        ),
        title_style="bold",
    )
    table.add_column("#", justify="right")
    table.add_column("By margin %", no_wrap=True)
    table.add_column("Margin", justify="right")
    table.add_column("p/min", justify="right")
    table.add_column("#", justify="right")
    table.add_column("By margin per minute", no_wrap=True)
    table.add_column("p/min", justify="right")
    table.add_column("Prep", justify="right")
    table.add_column("Margin", justify="right")

    left = ranking.by_margin_pct[:limit]
    right = ranking.by_margin_per_minute[:limit]
    for index in range(max(len(left), len(right))):
        lo = left[index] if index < len(left) else None
        ro = right[index] if index < len(right) else None
        table.add_row(
            str(index + 1) if lo else "",
            lo.label if lo else "",
            _pct(lo.margin_pct) if lo else "",
            pence(lo.margin_per_minute_pence) if lo else "",
            str(index + 1) if ro else "",
            ro.label if ro else "",
            pence(ro.margin_per_minute_pence) if ro else "",
            _prep(ro.prep) if ro else "",
            _pct(ro.margin_pct) if ro else "",
        )
    console.print(table)

    if ranking.orderings_agree:
        console.print(
            "[yellow]The two orderings AGREE exactly, which on a real menu means every "
            "ranked item takes the same time to make. Check the prep times.[/yellow]"
        )
    else:
        console.print("[bold]Where the two views disagree most[/bold]")
        for entry in ranking.biggest_disagreements[:6]:
            direction = (
                "margin/minute rates it HIGHER"
                if entry.rank_delta > 0
                else "margin/minute rates it LOWER"
            )
            console.print(
                f"  {entry.item.label}: margin % #{entry.margin_rank} vs margin/minute "
                f"#{entry.margin_per_minute_rank} ({entry.disagreement} places, {direction}) "
                f"-- {_pct(entry.item.margin_pct)} at {_prep(entry.item.prep)} = "
                f"{pence(entry.item.margin_per_minute_pence)}/min"
            )

    console.print(f"\n[bold]Labour over the window[/bold]  {view.menu.summary()}")
    if view.menu.labour_share_of_revenue_pct is not None:
        console.print(
            f"  labour is {view.menu.labour_share_of_revenue_pct:.1f}% of revenue on the "
            "included items"
        )
    for rollup in view.by_template:
        console.print(f"  [dim]{rollup.summary()}[/dim]")

    if show_excluded and ranking.excluded:
        console.print(f"\n[bold]{len(ranking.excluded)} item(s) that cannot be ranked[/bold]")
        for item, reason in ranking.excluded[:limit]:
            console.print(f"  {item.label}: [yellow]{reason}[/yellow]")
        if len(ranking.excluded) > limit:
            console.print(f"  [dim]... {len(ranking.excluded) - limit} more[/dim]")
    for warning in view.warnings:
        console.print(f"  [yellow]warning[/yellow] {warning}")
    console.print(
        "[dim]* prep time is an ESTIMATE, not a measurement. Both views are true, and "
        "neither is blended into a single score -- the disagreement between them is the "
        "finding (spec 5.6).[/dim]"
    )


def availability(
    as_of: Annotated[
        str, typer.Option("--as-of", help="Which day the menu is being asked about.")
    ] = "today",
    unavailable_only: Annotated[
        bool, typer.Option("--unavailable-only", help="Only what the menu should not offer.")
    ] = True,
    limit: Annotated[int, typer.Option(help="Rows to print.")] = 30,
) -> None:
    """What the MENU should offer on a given day, and why not (spec 4.3).

    This is the half of the seasonal question that says no. `resolve_recipe` is the
    other half and it always says yes: an out-of-season item still has a recipe, still
    costs what it costs, and a past sale of one still depleted stock. Only its place on
    a given day's menu is in question.
    """
    from cafeops.services.menu_margin import menu_availability

    on = parse_as_of(as_of).astimezone(settings.tz).date()
    with session_scope() as session:
        answers = menu_availability(session, on=on, unavailable_only=unavailable_only)

    if not answers:
        console.print(f"[green]Nothing is unavailable on {on}.[/green]")
        return

    table = Table(title=f"Menu availability on {on}", title_style="bold")
    table.add_column("Item", no_wrap=True)
    table.add_column("Size", justify="center")
    table.add_column("Status")
    table.add_column("Why")
    for answer in answers[:limit]:
        status = (
            "[green]AVAILABLE[/green]"
            if answer.is_available
            else f"[yellow]{answer.availability.value}[/yellow]"
        )
        why = "; ".join(answer.reasons)
        if answer.is_available and answer.binding_season is not None:
            why = (
                f"{answer.binding_season.name}, {answer.days_remaining} day(s) left"
                if answer.days_remaining is not None
                else answer.binding_season.name
            )
        table.add_row(answer.name, answer.size_code.value if answer.size_code else "-", status, why)
    console.print(table)
    if len(answers) > limit:
        console.print(f"[dim]... {len(answers) - limit} more[/dim]")
    console.print(
        "[dim]Unavailable means the MENU does not offer it. Its recipe still resolves -- "
        "a sale that happened consumed what it consumed (invariant 3).[/dim]"
    )


def prep_times_cmd(
    apply: Annotated[
        bool, typer.Option("--apply", help="Write the estimates. Without it, nothing is written.")
    ] = False,
    limit: Annotated[int, typer.Option(help="Rows to print.")] = 25,
) -> None:
    """Prep times per menu item, and the items nobody has timed (spec 4.2, 5.6).

    Every seeded prep time is an ESTIMATE and says so. The untimed list is not a bug:
    an item with no prep time reports labour, true margin and margin-per-minute as
    UNKNOWN rather than as a flattering number, and the list is the worklist.
    """
    from sqlalchemy import select

    from cafeops.db.models import MenuItem
    from cafeops.db.repositories.composition import SqlCompositionRepository
    from cafeops.seed.prep_times import seed_prep_times

    if apply:
        with session_scope() as session:
            report = seed_prep_times(session)
        console.print(report.summary())
        for warning in report.warnings:
            console.print(f"  [yellow]warning[/yellow] {warning}")

    with session_scope() as session:
        items = list(session.scalars(select(MenuItem).order_by(MenuItem.name, MenuItem.size_code)))
        prep = SqlCompositionRepository(session).prep_times([i.id for i in items])
        rows = [
            (item.name, item.size_code.value if item.size_code else "-", prep[item.id])
            for item in items
        ]

    timed = [row for row in rows if row[2].is_known]
    untimed = [row for row in rows if not row[2].is_known]
    table = Table(
        title=(
            f"{len(timed)} of {len(rows)} menu item(s) have a prep time; "
            f"{len(untimed)} do NOT (their labour figures are UNKNOWN)"
        ),
        title_style="bold",
    )
    table.add_column("Item", no_wrap=True)
    table.add_column("Size", justify="center")
    table.add_column("Prep", justify="right")
    table.add_column("From")
    for name, size, value in timed[:limit]:
        table.add_row(
            name,
            size,
            value.describe(),
            value.source.value,
        )
    console.print(table)
    if len(timed) > limit:
        console.print(f"[dim]... {len(timed) - limit} more timed[/dim]")
    if untimed:
        console.print(f"\n[bold]{len(untimed)} item(s) with NO prep time[/bold]")
        for name, size, _value in untimed[:limit]:
            console.print(f"  {name} [{size}]")
    console.print(
        "[dim]Every value here is an ESTIMATE, not a measurement. The ~15 items that "
        "actually sell are the ones worth timing with a stopwatch.[/dim]"
    )


def set_price_cmd(
    ingredient: Annotated[str, typer.Option("--ingredient", help="Ingredient name.")],
    pack_cost: Annotated[int, typer.Option("--pack-cost", help="New pack cost, in PENCE.")],
    pack_size: Annotated[
        str | None, typer.Option("--pack-size", help="Pack size; defaults to the current one.")
    ] = None,
    source: Annotated[
        str, typer.Option("--source", help="INVOICE, ESTIMATE or SUPPLIER_FEED.")
    ] = "INVOICE",
    note: Annotated[str | None, typer.Option("--note")] = None,
    commit: Annotated[
        bool, typer.Option("--commit/--dry-run", help="Write the price and cascade the cost.")
    ] = False,
) -> None:
    """Open a new ingredient price and cascade it to every menu item cost (spec 5.5)."""
    from sqlalchemy import select

    from cafeops.db.models import IngredientPrice
    from cafeops.db.repositories.ingredient import SqlIngredientRepository
    from cafeops.domain.types import PriceSource
    from cafeops.jobs.cost_rollup import rollup_for_ingredient

    try:
        price_source = PriceSource(source.strip().upper())
    except ValueError as exc:
        raise typer.BadParameter(
            f"{source!r}: expected INVOICE, ESTIMATE or SUPPLIER_FEED"
        ) from exc

    with unit_of_work(commit=commit) as session:
        snapshot = _ingredient_by_name(session, ingredient)
        current = session.scalar(
            select(IngredientPrice)
            .where(
                IngredientPrice.ingredient_id == snapshot.id,
                IngredientPrice.effective_to.is_(None),
            )
            .order_by(IngredientPrice.effective_from.desc())
            .limit(1)
        )
        if pack_size is not None:
            size = Decimal(pack_size)
        elif current is not None:
            size = current.pack_size
        else:
            raise typer.BadParameter("--pack-size is required: this ingredient has no price yet")
        unit = current.pack_unit if current is not None else snapshot.unit

        old_per_unit = snapshot.cost_per_unit_pence
        from cafeops.domain.units import convert

        # Per ingredient unit: the pack may be in kg for an ingredient counted in g.
        new_per_unit = Decimal(pack_cost) / convert(size, unit, snapshot.unit) if size > 0 else None
        console.print(
            f"[bold]{snapshot.name}[/bold]: {money(old_per_unit, dp=4)} -> "
            f"{money(new_per_unit, dp=4)} per {unit.value} "
            f"({pack_cost}p / {format(size, 'f')} {unit.value}, {price_source.value})"
        )

        if not commit:
            console.print("[yellow]DRY RUN: nothing written. Re-run with --commit.[/yellow]")
            return

        SqlIngredientRepository(session).add_price(
            snapshot.id,
            pack_size=size,
            pack_unit=unit,
            pack_cost_pence=pack_cost,
            effective_from=datetime.now(UTC),
            source=price_source,
            note=note,
        )
        session.flush()
        report = rollup_for_ingredient(session, snapshot.id)

    console.print(f"[green]committed[/green] {report.summary()}")
    for warning in report.warnings[:5]:
        console.print(f"  [yellow]warning[/yellow] {warning}")


def register(app: typer.Typer) -> None:
    app.command()(proposals)
    app.command(name="materialise-template")(materialise_template_cmd)
    app.command()(templates)
    app.command()(components)
    app.command(name="edit-recipe")(edit_recipe_cmd)
    app.command(name="cost-rollup")(cost_rollup_cmd)
    app.command(name="menu-costs")(menu_costs_cmd)
    app.command()(margin)
    app.command()(availability)
    app.command(name="prep-times")(prep_times_cmd)
    app.command(name="set-price")(set_price_cmd)
