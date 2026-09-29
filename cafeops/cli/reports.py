"""emergency-report -- the panic-buy log (spec 4.4)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

import typer
from rich.table import Table

from cafeops.cli._common import console
from cafeops.config import settings
from cafeops.db.base import session_scope


def emergency_report(
    days: Annotated[
        int | None,
        typer.Option("--days", help="Only routings in the last N days. Default: everything."),
    ] = None,
) -> None:
    """What panic-buying at retail has cost. Spec 4.4's argument for fixing the cadence.

    Not a note and not a screen decoration: one emergency is a bad week, and a pattern is
    a broken ordering cadence. The premium is the number that turns "we keep running to
    Tesco" into a case for ordering more often, or for renegotiating a delivery day.

    Rows are written by `cafeops simulate --commit` and by the ordering job -- never by
    this command, which only reads.
    """
    from datetime import UTC, datetime
    from datetime import timedelta as td

    from cafeops.db.repositories.sourcing import SqlSourcingRepository
    from cafeops.domain.ordering import pounds

    since = datetime.now(UTC) - td(days=days) if days is not None else None
    with session_scope() as session:
        repo = SqlSourcingRepository(session)
        count, priced, premium, per_ingredient = repo.emergency_summary(since=since)
        window = f"the last {days} day(s)" if days is not None else "the whole log"
        if count == 0:
            console.print(
                f"[green]No retail routings in {window}.[/green] Either the ordering "
                "cadence is working or nothing has been logged yet -- `cafeops simulate "
                "--commit` is what writes these rows."
            )
            return
        console.rule("[bold]panic-buy report[/bold]  spec 4.4")
        console.print(
            f"{count} retail routing(s) in {window}, {priced} of them priced. "
            f"Premium paid over the scheduled suppliers: [bold]{pounds(premium)}[/bold]"
        )
        if priced < count:
            # INVARIANT 8: a total over 9 of 14 rows understates the argument it exists
            # to make, and the only way to see that is to be told.
            console.print(
                f"[yellow]{count - priced} routing(s) carry no premium because a unit "
                "price was missing, so the figure above is a FLOOR, not the total.[/yellow]"
            )

        # Frequency and cumulative cost over time -- spec 4.4's actual argument. A list
        # of routings shows THAT it happened; this shows whether it is getting worse,
        # which is what turns the report into a case for changing the cadence rather
        # than a curiosity about one bad week. Bucketed by local ISO week (Mon-start)
        # because the ordering cadence being argued about is itself weekly.
        all_rows = repo.emergency_log(since=since)  # newest first
        buckets: dict[date, list[int]] = {}
        for row in reversed(all_rows):  # oldest first, so cumulative reads left-to-right
            local_day = row.occurred_at.astimezone(settings.tz).date()
            week_of = local_day - td(days=local_day.isoweekday() - 1)
            bucket = buckets.setdefault(week_of, [0, 0])
            bucket[0] += 1
            bucket[1] += row.premium_pence or 0
        timeline = Table(title="frequency and cumulative cost over time")
        timeline.add_column("Week of")
        timeline.add_column("Routings", justify="right")
        timeline.add_column("Premium", justify="right")
        timeline.add_column("Cumulative", justify="right")
        running_total = 0
        for week_of in sorted(buckets):
            week_count, week_premium = buckets[week_of]
            running_total += week_premium
            timeline.add_row(
                str(week_of), str(week_count), pounds(week_premium), pounds(running_total)
            )
        console.print(timeline)
        if len(buckets) < 2:
            console.print(
                "[dim]Only one week logged so far -- a trend needs more than one point. "
                "Every future `cafeops simulate --commit` run adds to this table.[/dim]"
            )

        table = Table(title="by ingredient")
        table.add_column("Ingredient")
        table.add_column("Routings", justify="right")
        table.add_column("Premium", justify="right")
        for name, (routings, pence) in sorted(
            per_ingredient.items(), key=lambda kv: (-kv[1][1], kv[0])
        ):
            table.add_row(name, str(routings), pounds(pence))
        console.print(table)
        console.print("[bold]most recent[/bold]")
        for row in repo.emergency_log(since=since)[:10]:
            premium_text = (
                pounds(row.premium_pence) if row.premium_pence is not None else "premium unknown"
            )
            console.print(
                f"  {row.occurred_at.astimezone(settings.tz).date()}  "
                f"{row.ingredient.name if row.ingredient is not None else row.ingredient_id}"
                f"  {premium_text}"
            )
            console.print(f"    [dim]{row.reason}[/dim]")
        console.print(
            "\n[dim]Every row here is a delivery that came too late, not a supplier "
            "anybody chose. The fix is the ordering cadence or the delivery schedule -- "
            "six of the eight suppliers' lead times are still invented placeholders, so "
            "confirming those is where it starts.[/dim]"
        )


def register(app: typer.Typer) -> None:
    app.command(name="emergency-report")(emergency_report)
