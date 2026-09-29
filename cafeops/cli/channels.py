"""`cafeops channels ...` -- import a portal export, and read the numbers back."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from cafeops.cli._common import console
from cafeops.db.base import session_scope
from cafeops.db.repositories.channel import SqlChannelRepository
from cafeops.domain.types import ChannelSourceKind, SalesChannelName
from cafeops.integrations.channels import (
    SCHEMAS,
    BrowserAgentChannelSource,
    CsvChannelSource,
    build_source,
    csv_dir,
    format_pct,
    format_x,
)
from cafeops.integrations.channels.analytics import channel_performance, rank_well_convert_badly
from cafeops.integrations.channels.base import (
    ChannelSource,
    ChannelSourceUnavailable,
    UnmappableReportError,
)
from cafeops.jobs.channel_sync import (
    DEFAULT_WINDOW_DAYS,
    ChannelSyncReport,
    sync_channel,
)

app = typer.Typer(
    no_args_is_help=True,
    help="Deliveroo / Just Eat: import portal exports and read ROAS, contribution and "
    "the ranks-well-converts-badly report (spec 4.6).",
)


def _channel(raw: str) -> SalesChannelName:
    try:
        return SalesChannelName(raw.strip().upper().replace("-", "_").replace(" ", "_"))
    except ValueError as exc:
        raise typer.BadParameter(
            f"{raw!r}: expected one of {', '.join(c.value for c in SalesChannelName)}"
        ) from exc


def _window(since: str | None, until: str | None, days: int) -> tuple[date, date]:
    end = date.fromisoformat(until) if until else date.today()  # noqa: DTZ011 -- a window
    start = date.fromisoformat(since) if since else end - timedelta(days=days - 1)
    if start > end:
        raise typer.BadParameter(f"--since {start} is after --until {end}")
    return start, end


def _money(pence: int | None) -> str:
    return "-" if pence is None else f"{pence / 100:,.2f}"


@app.command(name="import")
def import_cmd(
    channel: Annotated[str, typer.Argument(help="deliveroo | just_eat")],
    file: Annotated[
        Path | None,
        typer.Option("--file", "-f", help="One export. Repeatable. Omit to scan the CSV dir."),
    ] = None,
    since: Annotated[str | None, typer.Option("--since", help="YYYY-MM-DD.")] = None,
    until: Annotated[str | None, typer.Option("--until", help="YYYY-MM-DD.")] = None,
    days: Annotated[int, typer.Option("--days", help="Window length when --since is omitted.")] = (
        DEFAULT_WINDOW_DAYS
    ),
    source: Annotated[
        str | None,
        typer.Option("--source", help="CSV_UPLOAD | BROWSER_AGENT | MANUAL. Default: config."),
    ] = None,
    commit: Annotated[
        bool, typer.Option("--commit/--dry-run", help="Write, or parse and report only.")
    ] = False,
) -> None:
    """Import one channel's export. Dry run by default."""
    name = _channel(channel)
    start, end = _window(since, until, days)

    kind = None
    if source is not None:
        try:
            kind = ChannelSourceKind(source.strip().upper())
        except ValueError as exc:
            raise typer.BadParameter(f"{source!r} is not a ChannelSourceKind") from exc

    primary: ChannelSource
    fallback: ChannelSource | None
    if file is not None:
        primary, fallback = CsvChannelSource(files=[file], platform=name), None
    else:
        primary, fallback = build_source(kind)

    console.print(f"[bold]source[/bold] {primary.describe()}")
    if fallback is not None:
        console.print(f"[dim]fallback if that breaks: {fallback.describe()}[/dim]")

    try:
        with session_scope() as session:
            report = sync_channel(
                session,
                channel=name,
                since=start,
                until=end,
                primary=primary,
                fallback=fallback,
            )
            if not commit:
                session.rollback()
            _render_sync(report, committed=commit)
    except UnmappableReportError as exc:
        console.print("[red]REFUSED -- this file was not mapped, and nothing was guessed.[/red]")
        console.print(exc.report())
        raise typer.Exit(code=2) from None
    except ChannelSourceUnavailable as exc:
        console.print(f"[yellow]source unavailable and no fallback configured:[/yellow] {exc}")
        raise typer.Exit(code=3) from None


def _render_sync(report: ChannelSyncReport, *, committed: bool) -> None:
    console.print(f"[bold]{report.summary()}[/bold]")
    for ref in report.source_refs:
        console.print(f"  file: {ref}")
    for warning in report.warnings:
        console.print(f"  [yellow]note[/yellow] {warning}")
    for change in report.provenance_changes:
        console.print(f"  [magenta]provenance changed[/magenta] {change}")
    for item in report.unresolved:
        console.print(f"  [red]unresolved[/red] {item}")
    if report.unresolved:
        console.print(
            "  [dim]Unresolved names are skipped, never guessed at: a wrongly matched item "
            "attributes views and orders to the wrong product permanently.[/dim]"
        )
    console.print(
        "[green]committed[/green]"
        if committed
        else "[yellow]DRY RUN: rolled back. Re-run with --commit to write.[/yellow]"
    )


@app.command()
def report(
    since: Annotated[str | None, typer.Option("--since", help="YYYY-MM-DD.")] = None,
    until: Annotated[str | None, typer.Option("--until", help="YYYY-MM-DD.")] = None,
    days: Annotated[int, typer.Option("--days")] = DEFAULT_WINDOW_DAYS,
    min_views: Annotated[
        int, typer.Option("--min-views", help="Views in the window before an item is judged.")
    ] = 100,
) -> None:
    """ROAS, contribution after commission AND ad spend, and ranks-well-converts-badly."""
    start, end = _window(since, until, days)
    with session_scope() as session:
        repo = SqlChannelRepository(session)
        channels = repo.channels_present(since=start, until=end)
        if not channels:
            console.print(
                f"[yellow]no channel metrics between {start} and {end}. "
                "Run `cafeops channels import deliveroo --commit` first.[/yellow]"
            )
            return

        table = Table(
            title=f"Channel performance  {start} .. {end}  (spec 4.6)",
            title_style="bold",
            width=118,
        )
        table.add_column("Channel", no_wrap=True)
        table.add_column("Days", justify="right")
        table.add_column("Gross", justify="right")
        table.add_column("Commission", justify="right")
        table.add_column("Ad spend", justify="right")
        table.add_column("Contribution", justify="right")
        table.add_column("ROAS", justify="right")
        table.add_column("Conv.", justify="right")

        caveats: list[tuple[str, tuple[str, ...]]] = []
        for channel in channels:
            days_rows = repo.day_figures(since=start, until=end, channel=channel)
            perf = channel_performance(days_rows, channel=channel, since=start, until=end)
            table.add_row(
                channel.value,
                str(perf.days),
                _money(perf.gross_pence),
                _money(perf.commission_pence),
                _money(perf.ad_spend_pence),
                (
                    _money(perf.net_pence)
                    if perf.net_pence is not None
                    else "[yellow]unknown[/yellow]"
                ),
                format_x(perf.roas_bp),
                format_pct(perf.conversion_bp),
            )
            caveats.append((channel.value, perf.caveats()))
        console.print(table)
        console.print(
            "[dim]Contribution is gross less commission less ad spend, summed over the days "
            "that reported all three. A day missing one is dropped whole rather than "
            "part-subtracted -- same rule as ChannelMetric.net_pence and invariant 8. "
            "ROAS is attributed revenue / ad spend.[/dim]"
        )
        for name, notes in caveats:
            for note in notes:
                console.print(f"  [yellow]{name}[/yellow] {note}")

        for channel in channels:
            items = repo.item_figures(since=start, until=end, channel=channel)
            if not items:
                continue
            finding = rank_well_convert_badly(items, channel=channel, min_views=min_views)
            console.print(
                f"\n[bold]{channel.value}: ranks well, converts badly[/bold]  "
                f"({finding.considered} item(s) judged, benchmark "
                f"{format_pct(finding.benchmark_bp)})"
            )
            if not finding.gaps:
                console.print("  nothing flagged -- placement and conversion agree.")
            for gap in finding.gaps:
                console.print(f"  [red]#{gap.best_rank}[/red] {gap.sentence()}")
            for note in finding.caveats():
                console.print(f"  [dim]{note}[/dim]")
        console.print(
            "\n[dim]An item the platform already puts in front of people, which people then "
            "do not order, is a photo or description problem -- not a product problem. It is "
            "the cheapest thing on this screen to fix.[/dim]"
        )


@app.command()
def schemas() -> None:
    """What column headers each platform export must have. The mapping, printed."""
    for schema in SCHEMAS:
        console.print(f"\n[bold]{schema.label}[/bold]  ({schema.platform.value}, {schema.kind})")
        table = Table(show_header=True, width=110)
        table.add_column("field", no_wrap=True)
        table.add_column("accepted headers (normalised)")
        table.add_column("req", justify="center")
        for column in schema.columns:
            table.add_row(
                column.field,
                ", ".join(column.aliases),
                "[red]yes[/red]" if column.required else "",
            )
        console.print(table)
        if schema.size_style == "in_name":
            console.print("  [dim]size arrives folded into the item name: 'Foo (M)'[/dim]")
    console.print(
        "\n[dim]Headers are normalised (lowercased, non-alphanumerics to underscores) and "
        "matched EXACTLY against these lists. No fuzzy matching: a file that does not map "
        "is refused, because a parser that picks the nearest column will eventually read "
        "ad spend into commission and nothing downstream could tell.[/dim]"
    )
    console.print(f"[dim]CSV directory in use: {csv_dir()}[/dim]")


@app.command(name="browser-plan")
def browser_plan(
    channel: Annotated[str, typer.Argument(help="deliveroo | just_eat")],
    since: Annotated[str | None, typer.Option("--since")] = None,
    until: Annotated[str | None, typer.Option("--until")] = None,
    days: Annotated[int, typer.Option("--days")] = DEFAULT_WINDOW_DAYS,
) -> None:
    """Print the read-only instruction script the browser agent would follow.

    Contacts nobody. This is the inert description, the same pattern
    `suppliers.base.PreparedOrder` uses for orders.
    """
    name = _channel(channel)
    start, end = _window(since, until, days)
    source = BrowserAgentChannelSource()
    console.print(source.plan_for(channel=name, since=start, until=end).script())
    console.print(
        "\n[dim]Download-only: the agent takes the portal's own CSV export and that file "
        "goes through the same parser and the same refusals as a hand export. It does not "
        "read numbers off the screen, because a screen-scraped figure cannot be "
        "re-checked against anything.[/dim]"
    )
