"""`cafeops transactions ...` -- hand-typed sales as files, from the terminal.

DECISIONS 28. The same two services the Telegram bot and the API use
(`services/transactions_csv`), so a file the bot exported can be re-imported here and
vice versa. Dry run by default, like every other writing command in this CLI.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from cafeops.config import settings
from cafeops.db.base import session_scope
from cafeops.domain.enums import SaleChannel, SaleSource
from cafeops.services.transactions_csv import classify_csv, export_transactions, import_transactions

console = Console()

app = typer.Typer(
    no_args_is_help=True,
    help="Transactions as CSV: export what was recorded, import what was typed elsewhere.",
)


@app.command("export")
def export_cmd(
    since: Annotated[
        str | None, typer.Option("--from", help="YYYY-MM-DD. Default: 30 days.")
    ] = None,
    until: Annotated[str | None, typer.Option("--to", help="YYYY-MM-DD. Default: today.")] = None,
    channel: Annotated[
        str | None, typer.Option("--channel", help="EPOS | CASH | DELIVEROO | JUST_EAT | OTHER")
    ] = None,
    source: Annotated[
        str | None, typer.Option("--source", help="POS_API | MANUAL | CSV_UPLOAD | LOYALTY")
    ] = None,
    out: Annotated[Path | None, typer.Option("--out", help="Write here; default stdout.")] = None,
) -> None:
    """Every sale line in the window, one row each, newest first."""
    end = datetime.fromisoformat(until).date() if until else datetime.now(settings.tz).date()
    start = datetime.fromisoformat(since).date() if since else end - timedelta(days=29)
    try:
        ch = SaleChannel[channel.upper()] if channel else None
        src = SaleSource[source.upper()] if source else None
    except KeyError as exc:
        raise typer.BadParameter(f"unknown channel or source: {exc}") from exc
    with session_scope() as session:
        result = export_transactions(session, since=start, until=end, channel=ch, source=src)
    if out is None:
        typer.echo(result.text, nl=False)
    else:
        out.write_text(result.text, encoding="utf-8")
        console.print(
            f"[green]{out}[/green]: {result.receipts} receipt(s), {result.lines} line(s), "
            f"GBP {result.gross_pence / 100:,.2f}"
        )


@app.command("import")
def import_cmd(
    file: Annotated[Path, typer.Argument(help="A transactions CSV (date, item, qty, channel...).")],
    operator: Annotated[
        str, typer.Option("--by", help="Who is recording this. Written to every row.")
    ] = "cli",
    commit: Annotated[
        bool, typer.Option("--commit/--dry-run", help="Write it, or just say what it would.")
    ] = False,
) -> None:
    """Import hand-typed sales. Idempotent per file: the same file twice adds nothing."""
    text = file.read_bytes().decode("utf-8-sig", errors="replace")
    found = classify_csv(text)
    if found.kind.value != "TRANSACTIONS":
        console.print(
            f"[red]REFUSED[/red] {file.name} looks like {found.kind.value}, not a transactions "
            f"file. {found.detail or ''} For a takings export use `cafeops payments import`; "
            "for a Deliveroo / Just Eat report use `cafeops channels import`."
        )
        raise typer.Exit(code=2)
    with session_scope() as session:
        report = import_transactions(
            session, text=text, filename=file.name, recorded_by=operator, dry_run=not commit
        )
        console.print(report.summary())
        for why in report.rejected:
            console.print(f"  [yellow]rejected[/yellow] {why}")
        if report.refused:
            raise typer.Exit(code=2)
        if not commit:
            console.print("[yellow]DRY RUN: nothing written. Re-run with --commit.[/yellow]")
            return
        console.print("[green]committed[/green]")
