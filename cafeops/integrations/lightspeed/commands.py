"""`cafeops pos ...` -- inspect the POS edge without touching it.

Three commands, none of which can reach Lightspeed:

* `probe` runs the recorded-transport scenarios in `resilience.py` against a mock
  transport, so the client's behaviour on a bad day is something you can *see*.
* `scenarios` lists the recorded payload directories and the exact `cafeops sync`
  line that replays each. The awkward ingestion cases are data, not code, so adding
  one is a JSON file.
* `modifier-audit` answers ARCHITECTURE.md §8K.4's open question from ingested sales.

Kept out of `cli.py` for the same reason the channel commands are: the commands read
better next to the code they drive than in a 3,000-line file.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from cafeops.db.base import session_scope
from cafeops.integrations.lightspeed.modifier_probe import ModifierAudit, audit_modifiers
from cafeops.integrations.lightspeed.resilience import SCENARIOS, run_scenarios
from cafeops.integrations.lightspeed.sync import SCENARIOS_DIR

app = typer.Typer(
    no_args_is_help=True,
    help="Lightspeed edge: recorded resilience scenarios, replayable payloads, and the "
    "modifier probe (ARCHITECTURE.md 8K).",
)
console = Console(width=120)


@app.command()
def probe(
    only: Annotated[
        str | None,
        typer.Option("--only", help="Run one scenario by name. See the list in the output."),
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose/--quiet", help="Show each recorded server's script.")
    ] = False,
) -> None:
    """Replay the recorded API failures against the client. Contacts nothing."""
    names = [only] if only else None
    try:
        results = run_scenarios(names)
    except LookupError as exc:
        raise typer.BadParameter(
            f"{exc}. Known scenarios: {', '.join(s.name for s in SCENARIOS)}"
        ) from None

    console.print(
        "[bold]cafeops pos probe[/bold] -- httpx.MockTransport only. No socket is opened, "
        "and the credentials are visibly fake strings pointed at a .invalid host."
    )
    failures = 0
    for result in results:
        mark = "[green]ok[/green]" if result.passed else "[red]FAILED[/red]"
        if not result.passed:
            failures += 1
        console.print(f"\n{mark} [bold]{result.name}[/bold]")
        console.print(f"  [dim]{result.why}[/dim]")
        if verbose:
            for line in result.server_script:
                console.print(f"  [cyan]server[/cyan] {line}")
        for line in result.outcome:
            console.print(f"  {line}")
        console.print(
            f"  [dim]{result.requests} request(s), {result.token_mints} token mint(s) "
            f"-> {result.verdict}[/dim]"
        )

    console.print(
        f"\n[bold]{len(results) - failures}/{len(results)} scenario(s) behaved as intended.[/bold]"
    )
    if failures:
        console.print(
            "[red]A failing scenario means the client no longer does what the comment in "
            "client.py says it does. Read the outcome lines above before changing either.[/red]"
        )
        raise typer.Exit(code=1)
    console.print(
        "[dim]This is a harness, not a test suite (ARCHITECTURE.md 1): nothing runs it for "
        "you. Run it after touching client.py.[/dim]"
    )


def _shown(path: Path) -> str:
    """A path relative to the working directory when it is under it, else absolute."""
    cwd = Path.cwd()
    return str(path.relative_to(cwd)) if path.is_relative_to(cwd) else str(path)


@app.command()
def scenarios() -> None:
    """The recorded payload directories, and the command that replays each."""
    console.print(f"[bold]Recorded ingestion payloads[/bold]  [dim]{_shown(SCENARIOS_DIR)}[/dim]")
    if not SCENARIOS_DIR.is_dir():
        console.print("[yellow]none recorded[/yellow]")
        return
    table = Table(width=118)
    table.add_column("scenario", no_wrap=True)
    table.add_column("pages")
    table.add_column("what it records")
    for directory in sorted(p for p in SCENARIOS_DIR.iterdir() if p.is_dir()):
        pages = sorted(p.name for p in directory.glob("receipts*.json"))
        table.add_row(directory.name, str(len(pages)), _SCENARIO_NOTES.get(directory.name, ""))
    console.print(table)
    console.print(
        "\n[dim]Replay one with:[/dim]\n"
        "  cafeops sync --from 2026-09-16 --to 2026-09-18 \\\n"
        f"    --fixtures-dir {_shown(SCENARIOS_DIR)}/<scenario> \\\n"
        "    --as-of 2026-09-16T12:00:00Z --dry-run\n"
        "[dim]`--as-of` fixes the instant the window is judged against, which is the only "
        "way a recorded payload can demonstrate clock skew: 'in the future' is meaningless "
        "against a real clock that keeps moving.[/dim]"
    )


_SCENARIO_NOTES: dict[str, str] = {
    "duplicate-receipt": "one receipt delivered on two pages, identical -- collapsed and counted",
    "conflicting-receipt": "one receipt id, two different versions in one window -- both refused",
    "clock-skew": "sold_at 3m ahead (tolerated) and 4h ahead (refused) of --as-of",
    "quantity-changed": "the shipped window with one line's qty raised 1 -> 2; re-sync after "
    "`expand` must emit an ADJUSTMENT, never mutate",
    "double-count": "an oat milk arriving BOTH as a modifier and as an upcharge line",
    "loyalty-refund": "the shipped window re-read on 09-20 (--to 2026-09-20): Ben's receipt "
    "voided, Anna refunds her americano on a later receipt, a matcha, a non-member's second "
    "visit -- the loyalty auto-stamper reverses and re-awards (CAFEOPS_LOYALTY_AUTO_STAMP)",
}


@app.command(name="modifier-audit")
def modifier_audit(
    since: Annotated[str | None, typer.Option("--since", help="YYYY-MM-DD.")] = None,
    until: Annotated[str | None, typer.Option("--until", help="YYYY-MM-DD.")] = None,
    days: Annotated[int, typer.Option("--days", help="Window length when --since is omitted.")] = (
        60
    ),
) -> None:
    """Is modifier traffic reaching us? ARCHITECTURE.md 8K.4's open question.

    Reads ingested sales only. Writes nothing, and calls nobody.
    """
    end = date.fromisoformat(until) if until else date.today()  # noqa: DTZ011 -- a window
    start = date.fromisoformat(since) if since else end - timedelta(days=days - 1)
    if start > end:
        raise typer.BadParameter(f"--since {start} is after --until {end}")

    with session_scope() as session:
        audit = audit_modifiers(session, since=start, until=end)
        session.rollback()
    _render_audit(audit)


def _render_audit(audit: ModifierAudit) -> None:
    console.print(
        f"[bold]cafeops pos modifier-audit[/bold]  {audit.since} .. {audit.until}  "
        f"({audit.lines_considered} non-void, non-refund sale line(s))"
    )

    console.print("\n[bold]1. Modifier data on the ingested lines[/bold]")
    console.print(
        f"  {audit.lines_with_modifiers} line(s) carry a modifier, of which "
        f"{audit.seeded_lines_with_modifiers} look seeded -- so "
        f"{audit.real_lines_with_modifiers} from a real sync, out of {audit.real_lines} "
        "real line(s)."
    )
    if audit.real_lines_with_modifiers == 0 and audit.real_lines:
        console.print(
            "  [dim]Zero, as ARCHITECTURE.md 11 item 2 predicts for a financial-endpoint "
            "sync.[/dim]"
        )

    console.print("\n[bold]2. The upcharge proxy (8K.3)[/bold]")
    if not audit.proxy_use:
        console.print(
            "  [yellow]no delta-recipe items exist in this database at all[/yellow] -- so there "
            "is no proxy to be rung. Check the legacy import."
        )
    for use in audit.proxy_use:
        style = "green" if use.lines_rung else "yellow"
        console.print(f"  [{style}]{use.sentence()}[/{style}]")
        console.print(
            f"    [dim]catalog id: {use.lightspeed_id or 'NOT MATCHED -- run cafeops sync'}[/dim]"
        )

    console.print("\n[bold]3. Price residue: modifiers the feed did not show us[/bold]")
    if not audit.residues:
        console.print("  no line was rung above its list price.")
    for group in audit.residues:
        style = "yellow" if group.ambiguous else "cyan"
        console.print(f"  [{style}]{group.sentence()}[/{style}]")
        for example in group.examples:
            console.print(f"    [dim]{example}[/dim]")

    console.print(f"\n[bold]Verdict[/bold]\n  {audit.verdict()}")
    for caveat in audit.caveats:
        console.print(f"  [dim]caveat {caveat}[/dim]")
