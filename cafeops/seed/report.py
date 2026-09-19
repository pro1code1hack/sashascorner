"""Rendering the legacy import report. Spec 6 pass 3's output shape."""

from __future__ import annotations

from rich.console import Console

from cafeops.seed.legacy import LegacyImportReport
from cafeops.seed.patterns import TemplateProposal


def render(report: LegacyImportReport, console: Console, *, max_patterns: int = 25) -> None:
    mode = "DRY RUN -- nothing written" if report.dry_run else "committed"
    console.print(f"[bold]Legacy import[/bold] ({mode})")
    console.print(f"  {report.summary()}\n")

    patterns = report.patterns
    console.print(f"[bold]{len(patterns)} proposed template(s)[/bold] -- a human confirms each")
    for proposal in patterns[:max_patterns]:
        _render_proposal(proposal, console)
    if len(patterns) > max_patterns:
        console.print(f"  [dim]... {len(patterns) - max_patterns} more not shown[/dim]")

    singles = report.singletons
    console.print(
        f"\n[bold]{len(singles)} one-off item group(s)[/bold] "
        f"covering {report.manual_items} menu item rows -> manual_recipe"
    )
    console.print(
        "  [dim]Expected: cakes, bottled drinks, paninis, meal deals. "
        "Spec 6 anticipates roughly 60 of the 175 base items.[/dim]"
    )
    for proposal in singles[:8]:
        console.print(f"    {proposal.base_item_names[0]}  [{', '.join(proposal.sizes)}]")
    if len(singles) > 8:
        console.print(f"    [dim]... {len(singles) - 8} more[/dim]")

    if report.data_quality:
        console.print(f"\n[bold yellow]Data quality ({len(report.data_quality)})[/bold yellow]")
        for line in report.data_quality[:20]:
            console.print(f"  - {line}")
        if len(report.data_quality) > 20:
            console.print(f"  [dim]... {len(report.data_quality) - 20} more[/dim]")

    if report.warnings:
        console.print(f"\n[bold yellow]Warnings ({len(report.warnings)})[/bold yellow]")
        for line in report.warnings:
            console.print(f"  - {line}")

    console.print(
        f"\n[dim]{report.estimated_cost_count} ingredient price(s) are ESTIMATE, not invoice. "
        "They stay flagged through every rollup and are excluded from margin "
        "aggregates (invariant 6).[/dim]"
    )


def _render_proposal(p: TemplateProposal, console: Console) -> None:
    axis_note = ""
    if p.axes:
        biggest = max(p.axes, key=lambda a: a.option_count)
        axis_note = f" across {biggest.option_count} {biggest.name.lower()}s"
    console.print(
        f"\n  [bold]Proposed template: {p.name}[/bold]\n"
        f"    matches {p.menu_item_count} menu items{axis_note}, "
        f"sizes {'/'.join(p.sizes)}"
    )

    fixed = [c for c in p.components if not c.is_axis_filled]
    if fixed:
        names = ", ".join(f"{c.ingredient_name} ({c.role.value})" for c in fixed[:7])
        more = f" +{len(fixed) - 7} more" if len(fixed) > 7 else ""
        console.print(f"    common: {names}{more}")

    for axis in p.axes:
        console.print(
            f"    varying: {axis.name.lower()} ({axis.role.value}) -- {axis.option_count} distinct"
        )

    if p.conflicts:
        console.print(f"    [yellow]conflicts: {len(p.conflicts)} -> review[/yellow]")
        for conflict in p.conflicts[:3]:
            console.print(f"      [yellow]{conflict.describe()}[/yellow]")
        if len(p.conflicts) > 3:
            console.print(f"      [dim]... {len(p.conflicts) - 3} more[/dim]")
