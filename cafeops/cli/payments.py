"""`cafeops payments ...` -- import a back-office export, and read the takings back.

Dry run by default, like every other writing command here. That default is not a
formality: `bot-preview` shipped writing by default while its help said "nothing is
sent", and the cleanup took longer than the feature (ARCHITECTURE 8R).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from cafeops.cli._common import console
from cafeops.config import settings
from cafeops.db.base import session_scope
from cafeops.integrations.payments import (
    CsvPaymentSource,
    PaymentSourceUnavailable,
    payments_dir,
)
from cafeops.services.ingest_payments import ingest_payments, read_takings

app = typer.Typer(
    no_args_is_help=True,
    help="Payment reports: daily takings by method, from a back-office export.",
)

DEFAULT_WINDOW_DAYS = 30


def _money(pence: int | None, *, missing: str = "not reported") -> str:
    return f"[dim]{missing}[/dim]" if pence is None else f"GBP {pence / 100:,.2f}"


@app.command("import")
def import_cmd(
    since: Annotated[str | None, typer.Option("--from", help="YYYY-MM-DD.")] = None,
    until: Annotated[str | None, typer.Option("--to", help="YYYY-MM-DD.")] = None,
    directory: Annotated[Path | None, typer.Option("--dir", help="Where the exports are.")] = None,
    commit: Annotated[
        bool, typer.Option("--commit/--dry-run", help="Write it, or just say what it would.")
    ] = False,
) -> None:
    """Read every mappable export in the directory and record the days it covers."""
    end = date.fromisoformat(until) if until else datetime.now(settings.tz).date()
    start = date.fromisoformat(since) if since else end - timedelta(days=DEFAULT_WINDOW_DAYS - 1)
    source = CsvPaymentSource(directory)
    console.print(f"[dim]reading {source.directory}[/dim]")
    try:
        report = source.fetch(since=start, until=end)
    except PaymentSourceUnavailable as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(code=1) from None

    with session_scope() as session:
        result = ingest_payments(session, report)
        console.print(result.summary())
        for note in result.notes:
            console.print(f"  [dim]note {note}[/dim]")
        for bad in result.rejected:
            console.print(f"  [yellow]rejected[/yellow] {bad}")
        for bad in result.unmapped:
            console.print(f"  [red]REFUSED[/red] {bad}")
        if not commit:
            session.rollback()
            console.print("[yellow]DRY RUN: rolled back. Re-run with --commit.[/yellow]")
            return
        console.print("[green]committed[/green]")


@app.command("report")
def report_cmd(
    days: Annotated[int, typer.Option("--days", help="Window length.")] = DEFAULT_WINDOW_DAYS,
) -> None:
    """What the cafe took, and how much of it is actually known."""
    end = datetime.now(settings.tz).date()
    start = end - timedelta(days=days - 1)
    with session_scope() as session:
        w = read_takings(session, since=start, until=end)
        if w.days_reported == 0:
            console.print(
                f"[yellow]no payment days recorded between {start} and {end}.[/yellow]\n"
                "Export a payment report from the back office and run "
                "`cafeops payments import --commit`, or point CAFEOPS_PAYMENTS_CSV_DIR "
                f"at it. Currently reading {payments_dir()}."
            )
            return

        table = Table(title=f"takings {w.since} .. {w.until}", title_justify="left")
        table.add_column("")
        table.add_column("amount", justify="right")
        table.add_row("gross", _money(w.gross_pence, missing="nothing reported"))
        table.add_row("refunds", _money(w.refunds_pence))
        table.add_row("card fees", _money(w.fees_pence))
        table.add_row("discounts", _money(w.discounts_pence))
        table.add_row(
            "[bold]net[/bold]", f"[bold]{_money(w.net_pence, missing='unknowable')}[/bold]"
        )
        console.print(table)

        by = Table(title="by method", title_justify="left")
        by.add_column("method")
        by.add_column("gross", justify="right")
        for method, pence in sorted(w.by_method.items(), key=lambda kv: -kv[1]):
            by.add_row(method.lower(), _money(pence))
        console.print(by)

        console.print(f"[dim]{w.days_reported} day(s) reported[/dim]")
        for caveat in w.caveats:
            console.print(f"[yellow]  {caveat}[/yellow]")


@app.command("browser-plan")
def browser_plan_cmd(
    since: Annotated[str | None, typer.Option("--from", help="YYYY-MM-DD.")] = None,
    until: Annotated[str | None, typer.Option("--to", help="YYYY-MM-DD.")] = None,
) -> None:
    """Print the read-only script the browser agent would follow. Contacts nothing.

    Spec 4.6's second implementation: when no API is reachable, an agent downloads
    the export the back office already offers -- it never reads figures off the
    screen, because a scraped total cannot be re-checked against anything. The
    downloaded file goes through the same reader and the same refusals as a hand
    export, so the only difference is who fetched it.
    """
    from cafeops.integrations.payments import BrowserAgentPaymentSource

    end = date.fromisoformat(until) if until else datetime.now(settings.tz).date()
    start = date.fromisoformat(since) if since else end - timedelta(days=DEFAULT_WINDOW_DAYS - 1)
    plan = BrowserAgentPaymentSource().plan(since=start, until=end)
    console.print(plan.script())
    console.print(
        "\n[dim]read-only: navigate and download only. No login on the cafe's behalf, "
        "nothing submitted, nothing changed (invariant 10).[/dim]"
    )
    console.print(
        "[yellow]No driver is wired[/yellow], so this prints the plan and stops. "
        "Export by hand and run `cafeops payments import --commit` until one is."
    )


@app.command("schemas")
def schemas_cmd() -> None:
    """What column headers an export must have. The mapping, printed.

    So a real export can be checked against this BEFORE importing it, rather than
    the reader's refusal being the first thing that tells you. Headers are matched
    by name after normalisation -- lower-cased, punctuation to spaces -- never by
    position, so column order and capitalisation do not matter and an unexpected
    extra column is ignored rather than mis-read.
    """
    from cafeops.integrations.payments.csv_source import (
        _RAW_ALIASES,
        METHOD_WORDS,
        REQUIRED,
    )

    table = Table(title="payment export columns", title_justify="left")
    table.add_column("field")
    table.add_column("")
    table.add_column("accepted header names")
    for field, names in _RAW_ALIASES.items():
        need = "[yellow]required[/yellow]" if field in REQUIRED else "[dim]optional[/dim]"
        table.add_row(field, need, ", ".join(sorted(names)))
    console.print(table)

    by_method: dict[str, list[str]] = {}
    for word, method in METHOD_WORDS.items():
        by_method.setdefault(method.value, []).append(word.replace("_", " "))
    methods = Table(title="payment methods", title_justify="left")
    methods.add_column("recorded as")
    methods.add_column("recognised from")
    for value, words in sorted(by_method.items()):
        methods.add_row(value, ", ".join(sorted(words)))
    console.print(methods)

    console.print(
        "[dim]An unrecognised method is kept as OTHER and named in the notes -- "
        "never folded into CARD. A column that is absent stays null: a report that "
        "omits fees is not a report of zero fees, and net is withheld rather than "
        "computed from the deductions that happen to be present.[/dim]"
    )


__all__ = ["app"]
