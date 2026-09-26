"""Back-office CLI. The owner never sees this -- her only interface is Telegram."""

from __future__ import annotations

import shutil
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from cafeops.config import settings
from cafeops.db.base import SessionFactory, session_scope
from cafeops.domain.types import GateAlertLevel, Tier
from cafeops.domain.units import format_qty

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Cafe Ops -- inventory and auto-ordering for Sasha's Corner.",
)
# Wide enough that ingredient names and quantities are never truncated.
console = Console(width=120)


def _parse_as_of(raw: str) -> datetime:
    """`today` | `now` | YYYY-MM-DD -> a UTC instant.

    A bare date means end-of-day local time, because "stock as of today" means
    "after today's trade", not "at midnight before it".
    """
    tz = settings.tz
    lowered = raw.strip().lower()
    if lowered == "now":
        return datetime.now(UTC)
    if lowered == "today":
        return datetime.now(tz).replace(hour=23, minute=59, second=59).astimezone(UTC)
    if lowered == "yesterday":
        local = datetime.now(tz) - timedelta(days=1)
        return local.replace(hour=23, minute=59, second=59).astimezone(UTC)
    if "t" in lowered or ":" in lowered:
        # A full instant, e.g. 2026-09-16T12:00:00Z. Only tried when the string looks
        # like one: `datetime.fromisoformat` accepts a bare date too, and silently
        # reading "today" as midnight would invert the end-of-day rule above.
        try:
            instant = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise typer.BadParameter(
                f"{raw!r}: expected 'today', 'yesterday', 'now', YYYY-MM-DD, or a full "
                "ISO-8601 instant like 2026-09-16T12:00:00Z"
            ) from exc
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=tz)
        return instant.astimezone(UTC)
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise typer.BadParameter(
            f"{raw!r}: expected 'today', 'yesterday', 'now', or YYYY-MM-DD"
        ) from exc
    return datetime.combine(parsed, time.max, tzinfo=tz).astimezone(UTC)


# --------------------------------------------------------------------------
# import-legacy
# --------------------------------------------------------------------------


@app.command(name="import-legacy")
def import_legacy_cmd(
    workbook: Annotated[
        Path | None, typer.Option("--workbook", help="Legacy finance workbook (.xlsx).")
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--commit",
            help="Print the proposed templates without writing anything.",
        ),
    ] = True,
) -> None:
    """Import the legacy workbook and propose composition templates (spec 6)."""
    from cafeops.seed.legacy import import_legacy
    from cafeops.seed.report import render

    path = _workbook_path(workbook)
    session = SessionFactory()
    try:
        report = import_legacy(session, path, dry_run=dry_run)
        render(report, console)
        if dry_run:
            # Nothing is persisted. The proposals exist to be read and argued
            # with, not to be trusted (spec 6 pass 3).
            session.rollback()
            console.print(
                "\n[yellow]DRY RUN: rolled back. Re-run with --commit to write "
                "ingredients, prices and staged recipes.[/yellow]"
            )
        else:
            session.commit()
            console.print("\n[green]committed[/green]")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# --------------------------------------------------------------------------
# seed
# --------------------------------------------------------------------------


@app.command(name="import-finance")
def import_finance_cmd(
    workbook: Annotated[
        Path | None,
        typer.Option(
            "--workbook", help="Finance workbook (.xlsx). Default: sashas_corner_finance.xlsx."
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run/--commit", help="Report what would change, or write it."),
    ] = True,
) -> None:
    """Import Daily Sales, Expenses and Director Account from the finance workbook.

    Idempotent: a second --commit changes nothing. Rows edited in the app are left alone.
    Just Eat money in the workbook's cash column becomes Just Eat takings (DECISIONS 4).
    """
    from cafeops.config import REPO_ROOT
    from cafeops.seed.finance_import import check_against_workbook, import_finance, report_lines

    path = (workbook or REPO_ROOT / "sashas_corner_finance.xlsx").expanduser()
    if not path.exists():
        raise typer.BadParameter(f"workbook not found: {path}")
    session = SessionFactory()
    try:
        report = import_finance(session, path)
        for line in report_lines(report):
            console.print(line, markup=False, highlight=False)
        for line in check_against_workbook(session, path):
            console.print(line, markup=False, highlight=False)
        if dry_run:
            session.rollback()
            console.print("\n[yellow]DRY RUN: rolled back. Re-run with --commit to write.[/yellow]")
        else:
            session.commit()
            console.print("\n[green]committed[/green]")
        if report.refused:
            raise typer.Exit(code=2)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@app.command(name="menu-import-board")
def menu_import_board_cmd(
    board: Annotated[
        Path | None,
        typer.Option(
            "--board",
            help="Menu boards file. Default: site/backend/config/menu_board.toml.",
        ),
    ] = None,
    prices: Annotated[
        str | None,
        typer.Option(
            "--prices",
            help="Owner's call on conflicting prices: 'board' (board wins) or 'keep'.",
        ),
    ] = None,
    commit: Annotated[
        bool, typer.Option("--commit/--dry-run", help="Write through the menu services.")
    ] = False,
) -> None:
    """Bring the ops menu up to the published TV menu boards (categories, products, prices).

    Dry run lists new categories, category assignments, new products, every price
    conflict (board vs current, with the current price's source), sizes ops lacks,
    and ops products not on the boards. Writes only with --commit, only through
    services.menu_catalog, and never decides a price conflict by itself.
    """
    from cafeops.config import REPO_ROOT
    from cafeops.seed.menu_board import apply, plan, report_lines

    path = (board or REPO_ROOT / "site" / "backend" / "config" / "menu_board.toml").expanduser()
    if not path.exists():
        raise typer.BadParameter(f"board file not found: {path}")
    if prices not in (None, "board", "keep"):
        raise typer.BadParameter("--prices must be 'board' or 'keep'")
    session = SessionFactory()
    try:
        if not commit:
            for line in report_lines(plan(session, path)):
                console.print(line, markup=False, highlight=False)
            console.print("\n[yellow]DRY RUN: nothing written. Re-run with --commit.[/yellow]")
            return
        try:
            done = apply(session, path, prices=prices)  # type: ignore[arg-type]
        except (RuntimeError, ValueError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc
        for line in done:
            console.print(line, markup=False, highlight=False)
        console.print(f"\n[green]done: {len(done)} changes[/green]")
    finally:
        session.close()


def _workbook_path(workbook: Path | None) -> Path:
    path = workbook or settings.finance_workbook_path
    if path is None:
        raise typer.BadParameter(
            "no workbook given; pass --workbook or set CAFEOPS_FINANCE_WORKBOOK_PATH"
        )
    path = Path(path).expanduser()
    if not path.exists():
        raise typer.BadParameter(f"workbook not found: {path}")
    return path


@app.command()
def seed(
    demo: Annotated[
        bool, typer.Option("--demo", help="Generate the latte template and 60 days of sales.")
    ] = False,
    workbook: Annotated[
        Path | None, typer.Option("--workbook", help="Legacy workbook to import first.")
    ] = None,
    days: Annotated[int, typer.Option(help="Days of synthetic sales for --demo.")] = 60,
) -> None:
    """Seed a working database. With --demo, a full scenario per spec 14."""
    from cafeops.seed.demo import seed_demo

    if workbook is not None or settings.finance_workbook_path is not None:
        from cafeops.seed.legacy import import_legacy

        path = _workbook_path(workbook)
        with session_scope() as session:
            console.print(f"[bold]Importing[/bold] {path.name}")
            report = import_legacy(session, path, dry_run=False)
            console.print(f"  {report.summary()}")
            for warning in report.warnings:
                console.print(f"  [yellow]warning[/yellow] {warning}")

    # Prep times are seeded whether or not --demo runs: without them every labour
    # figure, true margin and margin-per-minute in the system is None, and spec 5.6's
    # finding cannot be computed at all. Every value is written as an ESTIMATE.
    from cafeops.seed.prep_times import seed_prep_times

    if not demo:
        with session_scope() as session:
            console.print("[bold]Estimating[/bold] prep times")
            prep = seed_prep_times(session)
            console.print(f"  {prep.summary()}")
        console.print("[green]done[/green] (pass --demo for the full scenario)")
        return

    with session_scope() as session:
        console.print("\n[bold]Building[/bold] demo composition and sales")
        demo_report = seed_demo(session, days=days)

    from cafeops.services.expand_recipes import expand_pending

    with session_scope() as session:
        console.print("[bold]Expanding[/bold] sales into stock movements")
        # allocate_batches=False: the seed expands 60 days of sales BEFORE the
        # deliveries that supplied them exist, then builds batches by replaying the
        # finished ledger (ARCHITECTURE.md 8F.2). Allocating here would report every
        # movement as a shortfall against batches that have not been created yet.
        expansion = expand_pending(session, allocate_batches=False)
        console.print(f"  {expansion.summary()}")
        for warning in expansion.warnings[:5]:
            console.print(f"  [yellow]warning[/yellow] {warning}")

    # Restocking reads the SALE movements, so it has to follow expansion.
    from cafeops.seed.demo import simulate_restocking, size_par_levels

    with session_scope() as session:
        console.print("[bold]Simulating[/bold] deliveries and physical counts")
        deliveries, counts = simulate_restocking(session)
        demo_report.deliveries, demo_report.counts = deliveries, counts

    # Par levels must be sized from OBSERVED throughput, which only exists once
    # sales have been expanded. A pack-multiple ceiling put 8 of 17 moving
    # ingredients below one cover window of demand, so the clamp -- not the
    # forecast -- was sizing the orders.
    # Batches are built by replaying the finished ledger chronologically: a batch
    # cannot be allocated before it is received, and deliveries interleave with 60
    # days of sales. See services/rebuild_batches for why a replay rather than
    # allocating during expansion.
    from cafeops.services.rebuild_batches import rebuild_batches

    with session_scope() as session:
        console.print("[bold]Rebuilding[/bold] batches from the ledger (FIFO + expiry)")
        rebuild = rebuild_batches(session)
        console.print(f"  {rebuild.summary()}")
        demo_report.batches = rebuild.batches_created

    with session_scope() as session:
        console.print("[bold]Sizing[/bold] par levels from observed consumption")
        resized, skipped = size_par_levels(session)
        console.print(
            f"  {resized} re-sized from throughput; {skipped} left at the seeded "
            "pack multiple (no measured consumption)"
        )

    # After the template exists, so its per-size defaults can be written to it.
    with session_scope() as session:
        console.print("[bold]Estimating[/bold] prep times (all ESTIMATE, spec 5.6)")
        prep = seed_prep_times(session)
        console.print(f"  {prep.summary()}")
        for warning in prep.warnings:
            console.print(f"  [yellow]warning[/yellow] {warning}")

    # Labour lands in menu_item_cost, so the cache has to be built after prep times.
    from cafeops.jobs.cost_rollup import rollup_all

    with session_scope() as session:
        console.print("[bold]Costing[/bold] the menu (cost + labour)")
        rollup = rollup_all(session)
        console.print(f"  {rollup.summary()}")

    console.print()
    for line in demo_report.lines():
        console.print(f"  {line}")
    console.print("\n[green]done[/green]")


# --------------------------------------------------------------------------
# stock
# --------------------------------------------------------------------------


@app.command()
def stock(
    as_of: Annotated[
        str, typer.Option("--as-of", help="'today', 'yesterday', 'now', or YYYY-MM-DD.")
    ] = "today",
    tier: Annotated[str | None, typer.Option("--tier", help="Filter to a tier: A, B or C.")] = None,
    all_ingredients: Annotated[
        bool, typer.Option("--all", help="Include untracked (tier C) ingredients.")
    ] = False,
    batches: Annotated[
        bool, typer.Option("--batches/--no-batches", help="Show open batches and expiry.")
    ] = True,
) -> None:
    """Print THEORETICAL on-hand per ingredient, with open batches and expiry."""
    from cafeops.services.read_stock import read_on_hand

    at = _parse_as_of(as_of)
    tiers = None
    if tier:
        try:
            tiers = (Tier(tier.strip().upper()),)
        except ValueError as exc:
            raise typer.BadParameter(f"{tier!r}: expected A, B or C") from exc

    with session_scope() as session:
        readings = read_on_hand(session, as_of=at, tiers=tiers, include_untracked=all_ingredients)

    if not readings:
        console.print("[yellow]No tracked ingredients. Run `cafeops seed` first.[/yellow]")
        return

    local = at.astimezone(settings.tz)
    table = Table(
        title=(f"THEORETICAL on-hand as of {local:%Y-%m-%d %H:%M %Z}  (not a physical count)"),
        title_style="bold",
    )
    table.add_column("Tier", justify="center")
    table.add_column("Ingredient", no_wrap=True)
    table.add_column("Theoretical", justify="right", no_wrap=True)
    table.add_column("Last count", justify="right")
    table.add_column("Counted at")
    table.add_column("Mv", justify="right")
    table.add_column("Expires in", justify="right", no_wrap=True)
    table.add_column("Basis")

    unanchored = 0
    negative = 0
    short_dated: list[tuple[str, int]] = []
    unbatched: list[tuple[str, Decimal, object]] = []
    for reading in readings:
        on_hand = reading.on_hand
        ingredient = reading.ingredient
        qty_text = format_qty(on_hand.qty, ingredient.unit)
        if on_hand.qty < 0:
            negative += 1
            qty_text = f"[red]{qty_text}[/red]"
        if reading.is_anchored:
            basis = "counted + ledger"
            count_text = format_qty(on_hand.basis_count_qty or Decimal("0"), ingredient.unit)
            counted_at = (
                on_hand.basis_counted_at.astimezone(settings.tz).strftime("%Y-%m-%d")
                if on_hand.basis_counted_at
                else "-"
            )
        else:
            unanchored += 1
            basis = "[yellow]ledger only - NO COUNT[/yellow]"
            count_text, counted_at = "-", "-"

        # Spec 10.8: short-dated stock must be visible before it is too late to
        # sell through. Colour is reserved for a crossed threshold, not decoration.
        days_left = reading.soonest_expiry_days
        if days_left is None:
            expiry_text = "[dim]never[/dim]"
        elif days_left < 0:
            expiry_text = f"[red]{days_left}d OVERDUE[/red]"
            short_dated.append((ingredient.name, days_left))
        elif days_left <= 3:
            expiry_text = f"[red]{days_left}d[/red]"
            short_dated.append((ingredient.name, days_left))
        elif days_left <= 7:
            expiry_text = f"[yellow]{days_left}d[/yellow]"
        else:
            expiry_text = f"{days_left}d"

        gap = reading.batch_coverage_gap
        if reading.batches and abs(gap) > Decimal("0.001"):
            unbatched.append((ingredient.name, gap, ingredient.unit))

        table.add_row(
            ingredient.tier.value,
            ingredient.name,
            qty_text,
            count_text,
            counted_at,
            str(on_hand.movement_count),
            expiry_text,
            basis,
        )

    console.print(table)
    console.print(
        "[dim]Every figure above is THEORETICAL: the last physical count plus the "
        "signed ledger since. Physical counts are the source of truth.[/dim]"
    )
    if unanchored:
        console.print(
            f"[yellow]{unanchored} ingredient(s) have no physical count -- those "
            "figures are a bare movement sum and should not be trusted for ordering."
            "[/yellow]"
        )
    if negative:
        console.print(
            f"[red]{negative} ingredient(s) are negative -- the ledger has consumed "
            "more than the last count recorded. Count them.[/red]"
        )

    if short_dated:
        console.print(
            f"\n[red]{len(short_dated)} ingredient(s) have stock expiring within "
            f"3 days:[/red] "
            + ", ".join(f"{name} ({d}d)" for name, d in sorted(short_dated, key=lambda x: x[1]))
        )
    if unbatched:
        console.print(
            f"\n[yellow]{len(unbatched)} ingredient(s) hold stock no batch accounts "
            "for. FIFO and the expiry sweep can only see batched stock, so this "
            "quantity can never expire or be counted as waste:[/yellow]"
        )
        for name, gap, unit in unbatched[:6]:
            console.print(f"  {name}: {format_qty(gap, unit)} unbatched")  # type: ignore[arg-type]

    if batches:
        detail = Table(title="Open batches, soonest expiry first", title_style="bold")
        detail.add_column("Ingredient", no_wrap=True)
        detail.add_column("Batch", justify="right")
        detail.add_column("Remaining", justify="right", no_wrap=True)
        detail.add_column("Received")
        detail.add_column("Expires")
        detail.add_column("Days", justify="right")
        detail.add_column("Value", justify="right")
        shown = 0
        for reading in readings:
            for spec in reading.batches:
                if shown >= 30:
                    break
                expiry = spec.effective_expiry(reading.open_life_days)
                days = spec.days_left(at, reading.open_life_days)
                value = (
                    None
                    if spec.unit_cost_pence is None
                    else spec.qty_remaining * spec.unit_cost_pence / 100
                )
                detail.add_row(
                    reading.ingredient.name,
                    str(spec.batch_id),
                    format_qty(spec.qty_remaining, reading.ingredient.unit),
                    spec.received_at.astimezone(settings.tz).strftime("%Y-%m-%d"),
                    "-" if expiry is None else expiry.astimezone(settings.tz).strftime("%Y-%m-%d"),
                    "-" if days is None else str(days),
                    "-" if value is None else f"GBP {value:.2f}",
                )
                shown += 1
            if shown >= 30:
                break
        total_batches = sum(len(r.batches) for r in readings)
        if shown:
            console.print()
            console.print(detail)
            if total_batches > shown:
                console.print(f"[dim]... {total_batches - shown} more open batches[/dim]")
        else:
            console.print(
                "\n[yellow]No open batches. FIFO depletion and the expiry sweep have "
                "nothing to work with -- run `cafeops seed --demo` or receive a "
                "delivery.[/yellow]"
            )


# --------------------------------------------------------------------------
# expand / recalc
# --------------------------------------------------------------------------


@app.command()
def expand(
    limit: Annotated[int | None, typer.Option(help="Max sale lines to expand.")] = None,
) -> None:
    """Expand un-expanded sales into SALE stock movements."""
    from cafeops.services.expand_recipes import expand_pending

    with session_scope() as session:
        report = expand_pending(session, limit=limit)
    console.print(report.summary())


@app.command()
def ingredients(
    tier: Annotated[str | None, typer.Option("--tier", help="Filter to A, B or C.")] = None,
) -> None:
    """List ingredients with tier, unit, waste factor and supplier."""
    from sqlalchemy import select

    from cafeops.db.models import Ingredient, Supplier, SupplierProduct

    with session_scope() as session:
        stmt = select(Ingredient).order_by(Ingredient.tier, Ingredient.name)
        if tier:
            stmt = stmt.where(Ingredient.tier == Tier(tier.strip().upper()))
        rows = list(session.scalars(stmt))
        suppliers: dict[int, str] = {}
        for ingredient in rows:
            name = session.scalar(
                select(Supplier.name)
                .join(SupplierProduct, SupplierProduct.supplier_id == Supplier.id)
                .where(SupplierProduct.ingredient_id == ingredient.id)
                .limit(1)
            )
            suppliers[ingredient.id] = name or "-"

        table = Table(title=f"{len(rows)} ingredients", title_style="bold")
        table.add_column("Tier", justify="center")
        table.add_column("Ingredient")
        table.add_column("Unit")
        table.add_column("Waste", justify="right")
        table.add_column("Tracked", justify="center")
        table.add_column("Supplier")
        for ingredient in rows:
            table.add_row(
                ingredient.tier.value,
                ingredient.name,
                ingredient.unit.value,
                f"{ingredient.waste_factor:.2f}",
                "yes" if ingredient.tracking_enabled else "-",
                suppliers[ingredient.id],
            )
    console.print(table)


#: Report lines that mean "a human must look at this". Styled so they cannot be
#: skimmed past, and listed here rather than inline so adding a refusal to
#: `SyncResult.lines()` does not quietly arrive in plain text.
_SYNC_ALERT_PREFIXES = (
    "UNRESOLVED",
    "AMBIGUOUS",
    "SUBSTITUTION",
    "CONFLICTING",
    "DOUBLE-COUNT",
    "FUTURE-DATED",
    "CLOCK SKEW",
    "DUPLICATE",
    "PARTIAL",
)


@app.command()
def sync(
    from_: Annotated[
        str, typer.Option("--from", help="Start of the window, inclusive, YYYY-MM-DD.")
    ],
    to: Annotated[str, typer.Option("--to", help="End of the window, inclusive, YYYY-MM-DD.")],
    fixtures: Annotated[
        bool,
        typer.Option(
            "--fixtures/--live",
            help="Ingest recorded fixture payloads (default) instead of calling the live API.",
        ),
    ] = True,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run/--commit",
            help="Do the whole sync and report it, then roll back. Nothing is written.",
        ),
    ] = False,
    fixtures_dir: Annotated[
        Path | None,
        typer.Option(
            "--fixtures-dir",
            help="Replay a recorded payload directory instead of the shipped one. "
            "See `cafeops pos scenarios`.",
        ),
    ] = None,
    as_of: Annotated[
        str | None,
        typer.Option(
            "--as-of",
            help="The instant to judge the window against, e.g. 2026-09-16T12:00:00Z. "
            "Defaults to now. Needed to demonstrate clock skew from a recorded payload.",
        ),
    ] = None,
) -> None:
    """Ingest Lightspeed sales for a date window. Idempotent on lightspeed_line_id.

    `--dry-run` runs everything -- the catalog match, the de-duplication, the skew
    checks, the correction arithmetic -- and then throws the transaction away, so the
    first run against real credentials can be read before it reaches the ledger.
    """
    from cafeops.integrations.lightspeed.sync import sync_window

    try:
        since = date.fromisoformat(from_)
        until = date.fromisoformat(to)
    except ValueError as exc:
        raise typer.BadParameter(f"expected YYYY-MM-DD: {exc}") from exc
    if since > until:
        raise typer.BadParameter(f"--from {since} is after --to {until}")
    if fixtures_dir is not None and not fixtures:
        raise typer.BadParameter("--fixtures-dir only means anything with --fixtures")

    if not fixtures and not settings.lightspeed_configured:
        raise typer.BadParameter(
            "Lightspeed is not configured (fixtures only) -- pass --fixtures, "
            "or set CAFEOPS_LIGHTSPEED_CLIENT_ID / CAFEOPS_LIGHTSPEED_CLIENT_SECRET "
            "(and the refresh token + business id) in .env."
        )

    now = _parse_as_of(as_of) if as_of else datetime.now(UTC)
    with session_scope() as session:
        result = sync_window(
            session,
            since=since,
            until=until,
            fixtures=fixtures,
            fixtures_dir=fixtures_dir,
            now=now,
            dry_run=dry_run,
        )
        if dry_run:
            # Rolled back here rather than by never writing: the point of a dry run is
            # that the code path is the real one, including the upsert's own reads of
            # rows this transaction created.
            session.rollback()
        else:
            from cafeops.services.sync_runs import record_cli_sync

            record_cli_sync(
                session,
                fixtures=fixtures,
                since=since,
                until=until,
                started_at=now,
                receipts_seen=result.receipts_seen,
                lines_ingested=result.ingest.inserted if result.ingest else None,
                unresolved_count=len(result.ingest.unresolved_items) if result.ingest else None,
                partial_reason=result.partial_reason,
            )

    console.print(f"[bold]cafeops sync[/bold] {since}..{until}")
    for line in result.lines():
        style = "yellow" if line.strip().startswith(_SYNC_ALERT_PREFIXES) else None
        console.print(f"  {line}" if style is None else f"  [{style}]{line}[/{style}]")
    if dry_run:
        console.print(
            "[yellow]DRY RUN: rolled back, nothing written. Re-run with --commit to "
            "ingest.[/yellow]"
        )
    else:
        console.print("[green]done[/green]")


password_app = typer.Typer(help="The shared back-office password.", no_args_is_help=True)
app.add_typer(password_app, name="password")


@password_app.command(name="reset")
def password_reset_cmd(
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation.")] = False,
) -> None:
    """Break-glass: forget the password set in Settings; CAFEOPS_API_PASSWORD rules again.

    Signs out every device. For when nobody remembers the password changed in the app.
    """
    from cafeops.services.auth import reset_password

    if not yes:
        typer.confirm("Forget the stored password and sign out every device?", abort=True)
    with session_scope() as session:
        revoked = reset_password(session, actor="cli")
    source = (
        "CAFEOPS_API_PASSWORD" if settings.api_password else "nothing (set CAFEOPS_API_PASSWORD)"
    )
    console.print(
        f"[green]reset[/green]: {revoked} session(s) revoked; the password is now {source}"
    )


# --------------------------------------------------------------------------
# count / drift  (spec 5.2 -- the trust metric and the auto-order gate)
# --------------------------------------------------------------------------


def _resolve_ingredient(session, raw: str):
    """Accept an ingredient name or a numeric id. Exact names, no fuzzy matching:
    guessing which ingredient a count belongs to would corrupt the ledger silently."""
    from cafeops.db.repositories.ingredient import SqlIngredientRepository

    repo = SqlIngredientRepository(session)
    found = repo.get(int(raw)) if raw.strip().isdigit() else repo.get_by_name(raw.strip())
    if found is None:
        raise typer.BadParameter(f"no ingredient named {raw!r} (try `cafeops ingredients`)")
    return found


def _verdict_style(verdict) -> str:
    from cafeops.domain.types import DriftVerdict

    if verdict is DriftVerdict.FORCE_MANUAL:
        return "red"
    if verdict is DriftVerdict.TUNE_WASTE_FACTOR:
        return "yellow"
    return "green"


@app.command()
def count(
    ingredient: Annotated[
        str, typer.Option("--ingredient", "-i", help="Ingredient name (exact) or id.")
    ],
    qty: Annotated[str, typer.Option("--qty", help="Counted quantity, in the stocking unit.")],
    by: Annotated[str, typer.Option("--by", help="Who counted. Recorded, not optional.")] = "cli",
    at: Annotated[
        str, typer.Option("--at", help="'now', 'today', or YYYY-MM-DD. Default now.")
    ] = "now",
    note: Annotated[str | None, typer.Option("--note", help="Free text on the count.")] = None,
) -> None:
    """Record a PHYSICAL count: re-anchor on-hand, measure drift, run the gate."""
    from cafeops.services.record_count import record_count

    try:
        counted = Decimal(qty)
    except ArithmeticError as exc:
        raise typer.BadParameter(f"{qty!r} is not a decimal quantity") from exc

    with session_scope() as session:
        found = _resolve_ingredient(session, ingredient)
        outcome = record_count(
            session,
            ingredient_id=found.id,
            counted_qty=counted,
            counted_at=_parse_as_of(at),
            counted_by=by,
            note=note,
        )
        _print_outcome(outcome)


def _print_outcome(outcome) -> None:
    from cafeops.domain.units import format_qty

    ing = outcome.ingredient
    on_hand = outcome.on_hand_before
    local = outcome.counted_at.astimezone(settings.tz)

    table = Table(
        title=f"{ing.name} -- physical count {local:%Y-%m-%d %H:%M %Z}",
        title_style="bold",
        show_header=False,
    )
    table.add_column("key")
    table.add_column("value")
    table.add_row("THEORETICAL before", format_qty(on_hand.qty, ing.unit))
    table.add_row("COUNTED (source of truth)", format_qty(outcome.counted_qty, ing.unit))
    if on_hand.basis_counted_at is not None:
        table.add_row(
            "previous anchor",
            f"{format_qty(on_hand.basis_count_qty or Decimal('0'), ing.unit)}"
            f" at {on_hand.basis_counted_at.astimezone(settings.tz):%Y-%m-%d %H:%M}"
            f", {on_hand.movement_count} movements since",
        )
    if outcome.drift is not None:
        drift = outcome.drift
        style = _verdict_style(drift.verdict)
        table.add_row("drift", f"[{style}]{drift.drift_pct:+.2f}%[/{style}]")
        table.add_row("verdict", f"[{style}]{drift.verdict.value}[/{style}]")
        if drift.suggested_waste_factor is not None:
            table.add_row(
                "waste_factor",
                f"{ing.waste_factor:.3f} -> suggest {drift.suggested_waste_factor:.3f}"
                "  (proposal only; `cafeops drift --apply-waste` adopts it)",
            )
    decision = outcome.decision
    table.add_row("auto-order", f"{decision.action.value}: {decision.reason}")
    if decision.revoke_cause is not None:
        table.add_row("revoke cause", f"[yellow]{decision.revoke_cause.value}[/yellow]")
    table.add_row(
        "auto_order_enabled",
        ("[green]True[/green]" if decision.auto_order_enabled else "False")
        + (" (changed)" if decision.changed else " (unchanged)"),
    )
    table.add_row("clean streak", f"{decision.clean_streak} of {decision.required_streak}")
    console.print(table)

    for text in outcome.notes:
        console.print(f"[dim]note: {text}[/dim]")
    # Two levels, not one flag. A >15% gap is a statement about the FIGURES; a revoke in
    # the tuning band is a statement about BEHAVIOUR -- orders that were being drafted
    # automatically have just stopped. Printing the same red sentence for both would say
    # the stock is untrustworthy when the gap is still inside the working band.
    if decision.alert_level is GateAlertLevel.ALARM:
        console.print(
            f"[bold red]ALERT[/bold red] {ing.name}: theoretical stock is not "
            "trustworthy. Auto-ordering is OFF and every order for it needs a human."
        )
    elif decision.alert_level is GateAlertLevel.NOTICE:
        console.print(
            f"[bold yellow]AUTO-ORDERING REVOKED[/bold yellow] {ing.name}: drafts for it "
            "are no longer built automatically. The figures are still inside the working "
            "band -- what expired is the evidence the grant rested on, and two consecutive "
            "counts under the bar earn it back."
        )
    console.print(
        "[dim]The count re-anchors on-hand (spec 5.1). No correcting movement is "
        "written -- the ledger stays append-only and the count IS the new basis.[/dim]"
    )


@app.command()
def drift(
    tier: Annotated[str | None, typer.Option("--tier", help="Filter to A, B or C.")] = None,
    ingredient: Annotated[
        str | None, typer.Option("--ingredient", "-i", help="Show one ingredient's history.")
    ] = None,
    history: Annotated[int, typer.Option("--history", help="Observations to list.")] = 6,
    backfill: Annotated[
        bool,
        typer.Option(
            "--backfill",
            help="Measure drift for counts recorded before drift existed, then run the gate.",
        ),
    ] = False,
    apply_waste: Annotated[
        str | None,
        typer.Option("--apply-waste", help="Adopt the suggested waste_factor for this ingredient."),
    ] = None,
    explain: Annotated[
        bool,
        typer.Option(
            "--explain",
            help=(
                "Attribute each gap: over-ordering (expiry write-offs) or a recipe error. "
                "The two have OPPOSITE fixes (spec 5.2)."
            ),
        ),
    ] = False,
) -> None:
    """Drift history and auto-order gate status per ingredient (spec 5.2)."""
    from cafeops.db.repositories.drift import SqlDriftRepository
    from cafeops.db.repositories.ingredient import SqlIngredientRepository
    from cafeops.db.repositories.par import SqlParLevelRepository
    from cafeops.domain.drift import mean_abs_drift_pct
    from cafeops.services.record_count import (
        apply_waste_suggestion,
        backfill_drift_observations,
        explain_drift_history,
        gate_status,
    )

    if backfill:
        with session_scope() as session:
            report = backfill_drift_observations(session)
            console.print(f"[bold]backfill[/bold] {report.summary()}")
            for warning in report.warnings[:10]:
                console.print(f"  [yellow]warning[/yellow] {warning}")
            for outcome in report.decisions:
                if outcome.decision.changed or outcome.decision.alert:
                    console.print(
                        f"  {outcome.ingredient.name}: {outcome.decision.action.value}"
                        f" -- {outcome.decision.reason}"
                    )

    if apply_waste is not None:
        with session_scope() as session:
            found = _resolve_ingredient(session, apply_waste)
            changed = apply_waste_suggestion(session, ingredient_id=found.id)
            if changed is None:
                console.print(
                    f"[yellow]{found.name}: the latest observation proposes no change "
                    "to waste_factor.[/yellow]"
                )
            else:
                old, new = changed
                console.print(
                    f"[green]{found.name}: waste_factor {old:.3f} -> {new:.3f}[/green]"
                    "  (a deliberate retune; the next count judges it)"
                )

    tiers = None
    if tier:
        try:
            tiers = (Tier(tier.strip().upper()),)
        except ValueError as exc:
            raise typer.BadParameter(f"{tier!r}: expected A, B or C") from exc

    with session_scope() as session:
        ingredient_repo = SqlIngredientRepository(session)
        drift_repo = SqlDriftRepository(session)
        par_repo = SqlParLevelRepository(session)

        if ingredient is not None:
            subjects = [_resolve_ingredient(session, ingredient)]
        else:
            subjects = ingredient_repo.list_tracked(tiers=tiers)

        table = Table(
            title="Drift and the auto-order gate  (spec 5.2)", title_style="bold", width=118
        )
        table.add_column("Tier", justify="center")
        table.add_column("Ingredient", no_wrap=True)
        table.add_column("Recent obs", justify="right")
        table.add_column("Latest", justify="right")
        table.add_column("Mean |drift|", justify="right")
        table.add_column("Verdict")
        table.add_column("Auto", justify="center")
        table.add_column("Streak", justify="center")

        detail = []
        for subject in subjects:
            rows = drift_repo.history(subject.id, limit=max(history, 2))
            decision = gate_status(session, ingredient=subject)
            pcts = [row.drift_pct for row in rows]
            rolling = mean_abs_drift_pct(pcts)
            audit = par_repo.audit(subject.id)
            enabled = bool(audit and audit.auto_order_enabled)
            style = _verdict_style(decision.verdict)
            table.add_row(
                subject.tier.value,
                subject.name,
                str(len(rows)),
                f"[{style}]{pcts[0]:+.2f}%[/{style}]" if pcts else "-",
                f"{rolling:.2f}%" if rolling is not None else "-",
                f"[{style}]{decision.verdict.value}[/{style}]" if decision.verdict else "-",
                "[green]ON[/green]" if enabled else "off",
                f"{decision.clean_streak}/{decision.required_streak}",
            )
            if ingredient is not None:
                detail.append((subject.name, (subject, rows, decision, audit)))

        console.print(table)
        if explain:
            _print_attribution(session, subjects, history, explain_drift_history)
        console.print(
            "[dim]Drift = (theoretical - counted) / max(counted, epsilon) * 100. The gate "
            "reads |drift|: over-stating stock and under-stating it are both untrustworthy. "
            f"Recent obs and the mean are over the last --history observations ({history}); "
            "the streak is counted over the stored series.[/dim]"
        )
        _print_detail(detail)


def _print_attribution(session, subjects, history: int, explain_drift_history) -> None:
    """Spec 5.2's v2 diagnostic: which problem is this, and therefore what do you fix.

    Over-ordering and a bad recipe have opposite fixes, so an undifferentiated drift
    percentage sends the owner the wrong way roughly half the time. `EXPIRED` movements
    are the evidence that separates them.
    """
    from cafeops.domain.drift import DriftCause

    styles = {
        DriftCause.EXPIRY: "red",
        DriftCause.MEASUREMENT: "yellow",
        DriftCause.MIXED: "magenta",
        DriftCause.NEGLIGIBLE: "green",
    }

    table = Table(
        title="Drift attribution -- is this over-ordering, or the recipe?  (spec 5.2)",
        title_style="bold",
        width=118,
    )
    table.add_column("Ingredient", no_wrap=True)
    table.add_column("Observed")
    table.add_column("Gap", justify="right", no_wrap=True)
    table.add_column("Expired", justify="right", no_wrap=True)
    table.add_column("Unexplained", justify="right", no_wrap=True)
    table.add_column("Expiry share", justify="right")
    table.add_column("Verdict")

    seen: list[tuple[str, object, str, str]] = []
    swept_late = 0
    for subject in subjects:
        for row in explain_drift_history(session, ingredient_id=subject.id, limit=history):
            explanation = row.explanation
            style = styles[explanation.cause]
            share = explanation.expiry_share
            if row.sweep_ran_after_count:
                swept_late += 1
            table.add_row(
                subject.name,
                f"{explanation.observed_at.astimezone(settings.tz):%Y-%m-%d}",
                format_qty(explanation.gap_qty, subject.unit),
                format_qty(explanation.expired_qty, subject.unit),
                format_qty(explanation.unexplained_loss_qty, subject.unit),
                "-" if share is None else f"{share * 100:.0f}%",
                f"[{style}]{explanation.headline}[/{style}]",
            )
            # One sentence per (ingredient, cause): repeating the same instruction once
            # per observation is how a report stops being read.
            if explanation.cause is not DriftCause.NEGLIGIBLE and not any(
                name == subject.name and cause is explanation.cause for name, cause, _, _ in seen
            ):
                action = explanation.action
                note = explanation.surplus_note
                if note is not None:
                    action = f"{action}  ALSO: {note}"
                seen.append((subject.name, explanation.cause, style, action))

    console.print()
    console.print(table)
    console.print(
        "[dim]Gap = theoretical - counted. Expired = EXPIRED write-offs dated inside the "
        "same window, recomputed from the ledger. Unexplained = the part no write-off "
        "accounts for. The share is measured against the whole loss (unexplained + "
        "written off), because a write-off the sweep has already booked is inside "
        "theoretical and so shrinks the gap rather than inflating it.[/dim]"
    )
    if swept_late:
        console.print(
            f"[dim]{swept_late} observation(s) have a write-off booked AFTER the count that "
            "measured them. Not a discrepancy -- somebody counted a short shelf and the sweep "
            "later named the reason. The gate acted on what was stored at the time; the live "
            "figure above is what to fix from.[/dim]"
        )
    if not seen:
        console.print(
            "[green]Nothing to fix: no ingredient shows a material loss over these windows.[/green]"
        )
        return
    console.print()
    for name, _cause, style, action in seen[:12]:
        console.print(f"[{style}]{name}[/{style}]: {action}")


def _print_detail(detail) -> None:
    from cafeops.domain.tiers import would_clear_gate
    from cafeops.domain.units import format_qty

    for name, payload in detail:
        subject, rows, decision, audit = payload
        console.print(f"\n[bold]{name}[/bold] -- {len(rows)} observation(s), newest first")
        inner = Table(show_header=True)
        inner.add_column("Observed")
        inner.add_column("Theoretical", justify="right")
        inner.add_column("Counted", justify="right")
        inner.add_column("Drift", justify="right")
        inner.add_column("waste_factor at count", justify="right")
        for row in rows:
            inner.add_row(
                f"{row.observed_at.astimezone(settings.tz):%Y-%m-%d %H:%M}",
                format_qty(row.theoretical_qty, subject.unit),
                format_qty(row.counted_qty, subject.unit),
                f"{row.drift_pct:+.2f}%",
                f"{row.waste_factor_at_count:.3f}",
            )
        console.print(inner)
        console.print(f"  gate: {decision.action.value} -- {decision.reason}")
        if subject.tier is not Tier.A and would_clear_gate(
            recent_drift_pcts=[row.drift_pct for row in rows]
        ):
            console.print(
                f"  [cyan]promotion candidate:[/cyan] this history would clear the gate, but tier"
                f" {subject.tier.value} is not auto-ordered. Promoting it to A is a deliberate"
                " human decision (spec 4.5), never automatic."
            )
        if audit is not None:
            console.print(
                f"  par_level: auto_order_enabled={audit.auto_order_enabled}"
                f" granted_at={audit.granted_at} revoked_at={audit.revoked_at}"
            )
            console.print(f"  reason on record: {audit.reason}")


@app.command()
def info() -> None:
    """Show configuration and database state. Says what is NOT wired yet."""
    from sqlalchemy import func, select

    from cafeops.db.models import (
        DrinkTemplate,
        Ingredient,
        LegacyStagedRecipe,
        MenuItem,
        MenuItemCost,
        Sale,
        StockMovement,
        Supplier,
        TemplateComponent,
    )

    with session_scope() as session:
        # There is no `recipe_line` table -- composition replaced it (spec 4.2).
        # `info` counted one until now and could not run at all.
        counts = {
            "ingredients": session.scalar(select(func.count(Ingredient.id))),
            "menu items": session.scalar(select(func.count(MenuItem.id))),
            "drink templates": session.scalar(select(func.count(DrinkTemplate.id))),
            "template components": session.scalar(select(func.count(TemplateComponent.id))),
            "menu items costed": session.scalar(select(func.count(MenuItemCost.id))),
            "staged legacy lines": session.scalar(select(func.count(LegacyStagedRecipe.id))),
            "suppliers": session.scalar(select(func.count(Supplier.id))),
            "sales": session.scalar(select(func.count(Sale.id))),
            "stock movements": session.scalar(select(func.count(StockMovement.id))),
        }

    table = Table(title="cafeops", title_style="bold", show_header=False)
    table.add_column("key")
    table.add_column("value")
    table.add_row("database", settings.database_url)
    table.add_row("timezone", settings.local_timezone)
    table.add_row(
        "Lightspeed",
        "configured" if settings.lightspeed_configured else "[yellow]fixtures only[/yellow]",
    )
    table.add_row(
        "Telegram",
        "configured" if settings.telegram_bot_token else "[yellow]not configured[/yellow]",
    )
    for key, value in counts.items():
        table.add_row(key, str(value))
    console.print(table)
    console.print(
        "[dim]Not yet built: Telegram bot, scheduled jobs, read-only API, "
        "web frontend. Template proposals need human confirmation before they "
        "are materialised.[/dim]"
    )


# --------------------------------------------------------------------------
# composition: proposals, templates, edits, cost rollup   (Agent B)
# --------------------------------------------------------------------------


def _money(pence: object, *, signed: bool = False, dp: int = 3) -> str:
    """Pence -> pounds. 'unknown' when the cost is not known.

    Never '£0.00' for an unknown cost: invariant 6 turns on the difference between
    "costs nothing" and "we do not know what it costs".
    """
    if pence is None:
        return "[yellow]unknown[/yellow]"
    value = Decimal(str(pence)) / 100
    sign = "+" if (signed and value >= 0) else ("-" if signed else "")
    return f"{sign}GBP {abs(value) if signed else value:.{dp}f}"


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}%"


def _render_preview(preview: object, header: str) -> None:
    """Spec 5.5's impact preview, in the shape the brief asks for."""
    console.print(f"\n[bold]{header}[/bold]")
    console.print(f"  Affects {preview.affected_item_count} menu item(s)")
    console.print(
        f"  Cost per item        {_money(preview.cost_delta_pence_per_item, signed=True)}"
    )
    console.print(
        f"  COGS, last 30 days   {_money(preview.monthly_cogs_delta_pence, signed=True, dp=2)}"
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


def _pence(value: object, *, signed: bool = False) -> str:
    """A pence figure at one decimal place. 'unknown' when it is not known."""
    if value is None:
        return "[yellow]unknown[/yellow]"
    dec = Decimal(str(value))
    sign = "+" if (signed and dec >= 0) else ("-" if signed else "")
    shown = abs(dec) if signed else dec
    # Drop the decimal above 1000p: a whole cake at 3000p/min does not need a tenth
    # of a penny, and the column is narrower than the number is precise.
    places = 0 if abs(dec) >= 1000 else 1
    return f"{sign}{shown:.{places}f}p"


def _prep(prep: object) -> str:
    """Compact prep time. A trailing `*` marks an ESTIMATE; the footnote says so."""
    if prep is None or prep.seconds is None:
        return "[yellow]-[/yellow]"
    return f"{prep.seconds}s{'*' if prep.is_estimate else ''}"


def _range(value: object, spread: object, *, signed: bool = False) -> str:
    """One figure when the affected items agree, else the range they span.

    Never an average: a mean across items that disagree describes no menu item, and
    the label beside it says "per item".
    """
    if value is not None:
        return _money(value, signed=signed)
    if spread is not None:
        low, high = spread
        return f"{_money(low, signed=signed)} to {_money(high, signed=signed)} [dim](varies)[/dim]"
    return "[yellow]unknown[/yellow]"


def _render_labour(labour: object) -> None:
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
            f"{_pence(worst_min.before.margin_per_minute_pence)} -> "
            f"{_pence(worst_min.after.margin_per_minute_pence)}"
        )
    if labour.rank_moves:
        console.print("  Margin/minute order MOVED among the affected items:")
        for label, was, now in labour.rank_moves[:6]:
            console.print(f"    {label}: #{was} -> #{now}")
    elif labour.ranking_after is not None and labour.ranking_after.ranked:
        console.print("  [dim]margin/minute order unchanged among the affected items[/dim]")
    for warning in labour.warnings:
        console.print(f"  [yellow]warning[/yellow] {warning}")


def _render_rollup(rollup: object) -> None:
    if rollup is None:
        return
    console.print(f"  [dim]{rollup.summary()}[/dim]")
    for warning in rollup.warnings[:5]:
        console.print(f"  [yellow]warning[/yellow] {warning}")
    if len(rollup.warnings) > 5:
        console.print(f"  [dim]... {len(rollup.warnings) - 5} more warning(s)[/dim]")


@app.command()
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


@app.command(name="materialise-template")
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

    session = SessionFactory()
    try:
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
    finally:
        session.close()


@app.command()
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


@app.command()
def components(
    template: Annotated[str, typer.Argument(help="Template name.")],
    as_of: Annotated[str, typer.Option("--as-of", help="Which day's recipe to show.")] = "now",
) -> None:
    """Show one template's live components, with the ids `edit-recipe` takes."""
    from cafeops.db.repositories.composition import SqlCompositionRepository

    at = _parse_as_of(as_of)
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


@app.command(name="edit-recipe")
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

    session = SessionFactory()
    try:
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
    finally:
        session.close()


@app.command(name="cost-rollup")
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

    at = _parse_as_of(as_of)
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


def _ingredient_by_name(session, raw: str):
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


@app.command(name="menu-costs")
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
            _money(row.price_pence, dp=2),
            _money(row.cost_pence),
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


@app.command()
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
            _pence(lo.margin_per_minute_pence) if lo else "",
            str(index + 1) if ro else "",
            ro.label if ro else "",
            _pence(ro.margin_per_minute_pence) if ro else "",
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
                f"{_pence(entry.item.margin_per_minute_pence)}/min"
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


@app.command()
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

    on = _parse_as_of(as_of).astimezone(settings.tz).date()
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


@app.command(name="prep-times")
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


@app.command(name="set-price")
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

    session = SessionFactory()
    try:
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
            f"[bold]{snapshot.name}[/bold]: {_money(old_per_unit, dp=4)} -> "
            f"{_money(new_per_unit, dp=4)} per {unit.value} "
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
        session.commit()
        console.print(f"[green]committed[/green] {report.summary()}")
        for warning in report.warnings[:5]:
            console.print(f"  [yellow]warning[/yellow] {warning}")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# --------------------------------------------------------------------------
# simulate  --  forecast and ordering (spec 5.3, 5.4)
# --------------------------------------------------------------------------

SIM_CAVEATS = (
    "Every order below is a DRAFT for human confirmation (invariant 1), so "
    "par_level.auto_order_enabled does not gate it unless --require-auto-order is "
    "passed. That flag is the unattended path, where invariant 2 does gate it.",
    "This replay reads only what was knowable on each order date: on-hand at local "
    "midnight that morning, history to the previous day. No hindsight.",
    "Shelf lives are ESTIMATE defaults, not measurements (ARCHITECTURE.md 8F.1). They "
    "CAP order quantities (invariant 4), so a wrong one either wastes stock or causes a "
    "stockout. The ~15 perishables that actually move are worth confirming first.",
)


def _sim_terms_caveat(terms) -> str:
    """The placeholder caveat, built from the column rather than a hard-coded list."""
    invented = [t.name for t in terms if t.terms_are_placeholders]
    real = [t.name for t in terms if not t.terms_are_placeholders]
    return (
        f"{len(invented)} of {len(terms)} suppliers' terms are INVENTED PLACEHOLDERS: "
        f"{', '.join(invented)}. Lead time, delivery weekdays, cutoff, minimum order and "
        "free-delivery threshold were never confirmed with any of them "
        "(ARCHITECTURE.md 8F.4). Every cover window below is only as good as they are, "
        "and nothing in the forecaster or the sizing has been tuned against them. Only "
        f"{' and '.join(real)} have terms anybody has checked."
    )


def _sim_history_range(session) -> tuple[date, date] | None:
    """First and last LOCAL day with a SALE movement."""
    from sqlalchemy import func, select

    from cafeops.db.models import MovementType, StockMovement

    row = session.execute(
        select(
            func.min(StockMovement.occurred_at),
            func.max(StockMovement.occurred_at),
        ).where(StockMovement.type == MovementType.SALE)
    ).first()
    if row is None or row[0] is None:
        return None
    tz = settings.tz
    return row[0].astimezone(tz).date(), row[1].astimezone(tz).date()


def _sim_order_dates(start: date, end: date, weekday: int) -> list[date]:
    """Every `weekday` in (start, end]. One order run per week, per supplier."""
    first = start + timedelta(days=1)
    offset = (weekday - first.isoweekday()) % 7
    day = first + timedelta(days=offset)
    dates: list[date] = []
    while day <= end:
        dates.append(day)
        day += timedelta(days=7)
    return dates


def _sim_delivery_text(spec) -> str:
    from cafeops.domain.forecast import WEEKDAY_NAMES

    if not spec.delivery_weekdays:
        return "any day (walk-in)"
    return "/".join(WEEKDAY_NAMES[d] for d in spec.delivery_weekdays)


def _sim_qty(qty: Decimal, unit) -> str:
    return format_qty(qty, unit)


def _sim_print_line(outcome) -> None:
    """The working for one ordered line. Invariant 7 withholds the low-confidence figure."""
    from cafeops.domain.ordering import pounds

    line = outcome.line
    candidate = outcome.candidate
    cover = candidate.cover
    unit = candidate.unit
    tag = "[magenta]TOP-UP[/magenta] " if line.is_top_up else ""
    console.print(
        f"    {tag}[bold]{line.ingredient_name}[/bold] [{candidate.tier.value}]"
        f"  pack {_sim_qty(line.pack.pack_size, line.pack.pack_unit)} @ "
        f"{pounds(line.pack.price_pence)}"
    )
    console.print(
        f"      cover {cover.length}d {cover.days[0]}..{cover.days[-1]}"
        f"  = lead {cover.lead_time_days} + gap {cover.days_until_next_delivery}"
        f" + safety {cover.safety_days}"
    )
    if candidate.is_capped:
        # SPEC 5.4 / INVARIANT 4: the deliberate under-order has to be visible, and
        # visible NEXT TO the quantity rather than in a note further down the page.
        shelf = candidate.shelf_life
        detail = ""
        if shelf is not None and shelf.shelf_life_days is not None:
            detail = (
                f"  (shelf life {shelf.shelf_life_days}d - transit {shelf.transit_buffer_days}d)"
            )
        console.print(
            f"      [yellow]CAPPED: effective cover {candidate.effective_cover_days}d of "
            f"{cover.length}d -- {line.cap_reason}[/yellow]{detail}"
        )
    if line.low_confidence:
        # INVARIANT 7: the reason goes in place of the number, not beside it.
        console.print("      [yellow]forecast WITHHELD -- low confidence:[/yellow]")
        for reason in line.confidence_reasons:
            console.print(f"        [yellow]{reason}[/yellow]")
        console.print(
            f"      on-hand {_sim_qty(line.on_hand_qty, unit)}"
            f"   open POs {_sim_qty(line.on_open_pos_qty, unit)}"
            f"   -> [bold]{line.packs} pack(s)[/bold] {pounds(line.line_total_pence)}"
        )
    else:
        window = (
            f"{candidate.effective_cover_days}d of {cover.length}d"
            if candidate.is_capped
            else f"{cover.length}d"
        )
        console.print(
            f"      forecast {_sim_qty(line.forecast_qty, unit)} over {window}"
            + (
                f" (uncapped {_sim_qty(candidate.full_forecast_qty, unit)})"
                if candidate.is_capped
                else ""
            )
            + f"  - on-hand {_sim_qty(line.on_hand_qty, unit)}"
            f"  - open POs {_sim_qty(line.on_open_pos_qty, unit)}"
            f"  = need {_sim_qty(line.need_qty, unit)}"
        )
        console.print(
            f"      base_daily {_sim_qty(candidate.forecast.base_daily, unit)}/day"
            f"   packs = ceil({_sim_qty(line.need_qty, unit)} / "
            f"{_sim_qty(candidate.pack_qty(), unit)}) = [bold]{line.packs}[/bold]"
            f"   {pounds(line.line_total_pence)}"
        )
    console.print(
        f"      resulting on-hand {_sim_qty(line.resulting_on_hand, unit)}"
        f"   par [{_sim_qty(candidate.par.min_qty, unit)} .. "
        f"{_sim_qty(candidate.par.max_qty, unit)}]"
        + (f"   [red]CLAMPED by {line.clamped}[/red]" if line.clamped else "")
    )
    if outcome.note:
        console.print(f"      [dim]{outcome.note}[/dim]")


def _sim_print_sourcing(result, *, verbose: bool) -> None:
    """Every sourcing decision, and what each one cost or saved. Spec 4.4.

    All of them, not only the switches. A decision to keep the incumbent because the
    alternate is DEARER is the case that proves the comparison is running at all, and
    the two deliberately worse seeded alternates (Monolith beans, Amazon cups) exist to
    be shown losing. Only ingredients with a real alternate reach `choices`, so this
    stays a handful of lines rather than a wall.
    """
    from cafeops.domain.ordering import pounds

    choices = result.split.choices
    if not choices:
        return
    switched = [c for c in choices if not c.chosen.is_preferred]
    rejected = [c for c in choices if c.cheaper_rejected is not None]
    console.print(
        f"\n[bold]sourcing[/bold]  {len(choices)} ingredient(s) with a real choice: "
        f"{len(switched)} switched, {len(rejected)} kept a dearer supplier on purpose"
    )
    for choice in choices:
        if not choice.alternatives and not verbose:
            continue
        marker = "[green]SWITCHED[/green]" if not choice.chosen.is_preferred else "KEPT"
        console.print(f"  {marker} {choice.reason}")
        if choice.cheaper_rejected is not None and choice.forgone_saving_pence is not None:
            console.print(
                f"    [yellow]forgone saving {pounds(int(choice.forgone_saving_pence))}"
                f"[/yellow] -- a decision, not an oversight (spec 4.4)"
            )


def _sim_print_emergency(session, result, *, order_date, commit: bool) -> None:
    """The Tesco run: what could not wait, and the premium it cost. Spec 4.4."""
    from datetime import UTC, datetime
    from datetime import time as dtime
    from decimal import ROUND_HALF_UP

    from cafeops.domain.ordering import pounds
    from cafeops.domain.sourcing import emergency_premium_pence
    from cafeops.services.build_order import record_emergency_lines

    def _pence(value: Decimal) -> int:
        # ROUND_HALF_UP, not int() truncation: `record_emergency_lines` rounds the same
        # way before writing `tesco_routing.premium_pence`, and a live figure that
        # truncates while the logged one rounds would show two different premiums for
        # the same routing a moment apart (invariant 8 -- the numbers must agree).
        return int(value.to_integral_value(rounding=ROUND_HALF_UP))

    plan = result.emergency
    if not plan.lines and not plan.notes:
        return
    if plan.lines:
        premium = plan.total_premium_pence
        console.print(
            f"\n[bold red]Tesco emergency routing[/bold red]  {len(plan.lines)} line(s)"
            + (
                f", retail premium {pounds(_pence(premium))} over the scheduled suppliers"
                if premium is not None
                else ", premium not computable -- a unit price is missing"
            )
        )
        for line in plan.lines:
            console.print(
                f"    [bold]{line.ingredient_name}[/bold] {_sim_qty(line.qty, line.unit)}"
                + (
                    f"  retail {line.retail_unit_price_pence:.2f}p/{line.unit.value}"
                    if line.retail_unit_price_pence is not None
                    else "  retail price unknown"
                )
                + (
                    f"  vs preferred {line.preferred_unit_price_pence:.2f}p/{line.unit.value}"
                    if line.preferred_unit_price_pence is not None
                    else ""
                )
                + (
                    f"  = [red]premium {pounds(_pence(emergency_premium_pence(line)))}[/red]"
                    if emergency_premium_pence(line) is not None
                    else ""
                )
            )
            console.print(f"      [dim]{line.reason}[/dim]")
    for note in plan.notes:
        console.print(f"  [yellow]note[/yellow] {note}")
    if commit and plan.lines:
        at = datetime.combine(order_date, dtime(9, 0), tzinfo=settings.tz).astimezone(UTC)
        written = record_emergency_lines(session, result, at=at)
        console.print(
            f"  [green]logged {len(written)} routing(s) to tesco_routing[/green] -- the "
            "accumulated log is the argument for fixing the ordering cadence, so it is "
            "data rather than a note (spec 4.4)"
        )


@app.command()
def simulate(
    supplier: Annotated[
        str | None, typer.Option("--supplier", help="Only this supplier (name, case-insensitive).")
    ] = None,
    weeks: Annotated[
        int | None, typer.Option("--weeks", help="Only the last N weekly order runs.")
    ] = None,
    order_weekday: Annotated[
        int, typer.Option("--order-weekday", help="ISO weekday to place orders on (1=Mon).")
    ] = 1,
    cadence_days: Annotated[
        int,
        typer.Option(
            "--cadence-days",
            help=(
                "Reordering interval for the cover window's middle term. 0 = the literal "
                "spec reading (the supplier's next available slot), which assumes "
                "reordering at every opportunity."
            ),
        ),
    ] = 7,
    min_order_pence: Annotated[
        int | None,
        typer.Option(
            "--min-order-pence",
            help=(
                "WHAT-IF: override every supplier's minimum order. Six of the eight stored "
                "minimums are invented placeholders, so asking 'what if it were really X' "
                "is the only honest way to exercise the top-up."
            ),
        ),
    ] = None,
    order_time: Annotated[
        str | None,
        typer.Option(
            "--order-time",
            help=(
                "Local time of day the order is placed, HH:MM. Past a supplier's cutoff "
                "the lead time starts tomorrow and the cover window grows a day -- which "
                "is how a Tesco run happens. Omitted, no cutoff is applied."
            ),
        ),
    ] = None,
    free_delivery_multiple: Annotated[
        float,
        typer.Option(
            "--free-delivery-multiple",
            help=(
                "How far below a free-delivery threshold it is worth buying stock to save "
                "the fee, as a multiple of the fee. An INTERPRETATION of spec 5.4, which "
                "says to top up below the threshold but not at what price. 0 never tops up "
                "for a fee."
            ),
        ),
    ] = 3.0,
    require_auto_order: Annotated[
        bool,
        typer.Option(
            "--require-auto-order",
            help=(
                "Only order ingredients that have EARNED auto-ordering through drift "
                "history (invariant 2). This is the unattended path a scheduled job takes."
            ),
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Show the working for candidates that were NOT ordered."),
    ] = False,
    commit: Annotated[
        bool,
        typer.Option("--commit", help="Write each week's suggestion as a DRAFT purchase order."),
    ] = False,
) -> None:
    """Replay the seeded days and show what would have been ordered each week.

    There is no test suite, so this output is the proof of the arithmetic: every
    ordered line shows its cover window, forecast total, on-hand, open-PO quantity,
    need, pack division, resulting on-hand, and any clamp or top-up.
    """
    from datetime import time as dtime

    from sqlalchemy import select

    from cafeops.db.models import Ingredient, ParLevel
    from cafeops.db.repositories.sourcing import SqlSourcingRepository
    from cafeops.db.repositories.stock import SqlStockRepository
    from cafeops.db.repositories.supplier import SqlSupplierRepository
    from cafeops.domain.forecast import WEEKDAY_NAMES, daily_series, dow_factors
    from cafeops.domain.ordering import pounds
    from cafeops.services.build_order import ForecastKnobs, build_split, create_draft_po

    if order_weekday < 1 or order_weekday > 7:
        raise typer.BadParameter(f"--order-weekday must be 1..7 (Mon..Sun), got {order_weekday}")
    cadence: int | None = None if cadence_days == 0 else cadence_days
    if cadence is not None and cadence < 1:
        raise typer.BadParameter("--cadence-days must be 0 (spec literal) or a positive integer")
    placed_at: dtime | None = None
    if order_time is not None:
        try:
            hours, _, minutes = order_time.partition(":")
            placed_at = dtime(int(hours), int(minutes or 0))
        except ValueError as exc:
            raise typer.BadParameter(f"--order-time must be HH:MM, got {order_time!r}") from exc

    knobs = ForecastKnobs.from_settings()

    with session_scope() as session:
        span = _sim_history_range(session)
        if span is None:
            console.print(
                "[yellow]No SALE movements in the ledger. Run `cafeops seed --demo` "
                "and `cafeops expand` first.[/yellow]"
            )
            return
        start, end = span
        terms_by_id = SqlSourcingRepository(session).terms_by_id()
        suppliers = SqlSupplierRepository(session).list_all()
        if supplier:
            wanted = supplier.strip().lower()
            suppliers = [s for s in suppliers if s.name.lower() == wanted]
            if not suppliers:
                raise typer.BadParameter(f"{supplier!r}: no such supplier")
        # The minimum override is applied inside sizing (`build_split`), not to the
        # supplier list: the top-up is part of sizing, and re-labelling the header while
        # the lines came from the stored minimum would show an order nobody built.

        order_dates = _sim_order_dates(start, end, order_weekday)
        if weeks is not None:
            order_dates = order_dates[-weeks:]

        console.rule("[bold]cafeops simulate[/bold]  spec 5.3 forecast + 5.4 order sizing")
        console.print(
            f"history      {start} .. {end}  ({(end - start).days + 1} local days)\n"
            f"order runs   {len(order_dates)} x {WEEKDAY_NAMES[order_weekday]}"
            f"  ({', '.join(str(d) for d in order_dates)})\n"
            f"forecast     alpha {knobs.ewma_alpha}, EWMA window {knobs.ewma_window_days}d, "
            f"dow window {knobs.dow_window_weeks}w, clamp "
            f"[{knobs.dow_factor_min}, {knobs.dow_factor_max}], "
            f"min history {knobs.min_history_days}d\n"
            f"cover gap    "
            + (
                f"{cadence}-day reordering cadence"
                if cadence is not None
                else "literal spec: the supplier's next available delivery slot"
            )
        )
        if min_order_pence is not None:
            console.print(
                f"[magenta]WHAT-IF: every supplier minimum overridden to "
                f"{pounds(min_order_pence)}[/magenta]"
            )
        console.print(f"[yellow]CAVEAT[/yellow] {_sim_terms_caveat(list(terms_by_id.values()))}")
        for caveat in SIM_CAVEATS:
            console.print(f"[yellow]CAVEAT[/yellow] {caveat}")
        if placed_at is not None:
            console.print(
                f"[magenta]orders placed at {placed_at} local: any supplier whose cutoff "
                "is earlier loses a delivery cycle, and its cover window grows to pay for "
                "it.[/magenta]"
            )
        earned = list(
            session.scalars(
                select(Ingredient.name)
                .join(ParLevel, ParLevel.ingredient_id == Ingredient.id)
                .where(ParLevel.auto_order_enabled.is_(True))
                .order_by(Ingredient.name)
            )
        )
        console.print(
            f"auto-order    {len(earned)} ingredient(s) have earned it"
            + (f": {', '.join(earned)}" if earned else " (the drift gate has not run yet)")
        )
        if require_auto_order:
            console.print(
                "[magenta]--require-auto-order: only those ingredients are considered "
                "(invariant 2). Everything else is left for a human.[/magenta]"
            )
        if commit:
            console.print(
                "[red]--commit: each week's suggestion is written as a DRAFT purchase "
                "order. Later weeks then see those drafts as open-PO quantity, which is "
                "correct but makes the run non-idempotent.[/red]"
            )

        drafts: list[tuple[date, str, int]] = []
        for order_date in order_dates:
            console.rule(f"[bold]{order_date} {WEEKDAY_NAMES[order_date.isoweekday()]}[/bold]")
            result = build_split(
                session,
                order_date=order_date,
                knobs=knobs,
                reorder_cadence_days=cadence,
                require_auto_order=require_auto_order,
                supplier_ids=[s.id for s in suppliers] if supplier else None,
                min_order_pence=min_order_pence,
                order_time=placed_at,
                # Through `str` so no float reaches a money calculation (invariant 11).
                free_delivery_top_up_multiple=Decimal(str(free_delivery_multiple)),
            )
            for plan in result.plans:
                suggestion = plan.suggestion
                spec = suggestion.supplier
                terms = terms_by_id.get(spec.id)
                window = suggestion.cover_window
                placeholder = (
                    " [yellow](terms are PLACEHOLDERS)[/yellow]"
                    if (terms is not None and terms.terms_are_placeholders)
                    else ""
                )
                fee_text = ""
                if terms is not None and terms.delivery_fee_pence > 0:
                    fee_text = f"  delivery {pounds(terms.delivery_fee_pence)}"
                    if terms.free_delivery_threshold_pence is not None:
                        fee_text += f" (free over {pounds(terms.free_delivery_threshold_pence)})"
                cutoff_text = (
                    f"  cutoff {terms.cutoff_time}"
                    if terms is not None and terms.cutoff_time is not None
                    else ""
                )
                console.print(
                    f"\n[bold cyan]{spec.name}[/bold cyan]{placeholder}"
                    f"  lead {spec.lead_time_days}d"
                    f"  delivers {_sim_delivery_text(spec)}"
                    f"  min order {pounds(spec.min_order_pence)}"
                    f"  channel {spec.order_channel.value}"
                    f"{cutoff_text}{fee_text}"
                )
                console.print(
                    f"  target delivery {suggestion.target_delivery_date}"
                    f"  |  widest cover {window.length}d "
                    f"{window.days[0]}..{window.days[-1]}"
                    f"  = lead {window.lead_time_days} + gap "
                    f"{window.days_until_next_delivery} + safety {window.safety_days}"
                )

                if cadence is None and window.days_until_next_delivery == 1:
                    console.print(
                        "  [yellow]note: the gap term came out at 1 day, so this window "
                        "assumes reordering tomorrow. For a weekly rhythm pass "
                        "--cadence-days 7 -- otherwise every order here is a seventh of "
                        "what a week needs.[/yellow]"
                    )

                ordered = plan.ordered
                if ordered:
                    status = "met" if suggestion.meets_minimum else "[red]NOT MET[/red]"
                    console.print(
                        f"  [green]{len(ordered)} line(s), {pounds(suggestion.total_pence)}"
                        f"[/green]  minimum {pounds(spec.min_order_pence)} {status}"
                        + (
                            "  [magenta]TOPPED UP[/magenta]"
                            if suggestion.min_order_topped_up
                            else ""
                        )
                    )
                    for outcome in ordered:
                        _sim_print_line(outcome)
                else:
                    console.print("  [dim]nothing to order[/dim]")

                # Not-ordered candidates, loudest first. A par ceiling that suppressed a
                # real need is a finding; forty "already covered" lines are noise, and
                # the below-par-floor list is already aggregated into the order notes.
                not_ordered = [o for o in plan.outcomes if o.line is None]
                for outcome in not_ordered:
                    if outcome.data_error or outcome.clamp_blocked or verbose:
                        console.print(f"  [dim]- {outcome.note}[/dim]")
                floors = sum(1 for o in not_ordered if o.below_par_floor)
                quiet = sum(
                    1
                    for o in not_ordered
                    if not (o.data_error or o.clamp_blocked or o.below_par_floor)
                )
                if floors and not verbose:
                    console.print(
                        f"  [dim]- {floors} candidate(s) under their par floor with nothing "
                        f"forecast to move them; named in the note below[/dim]"
                    )
                if quiet:
                    console.print(
                        f"  [dim]- {quiet} further candidate(s) already covered "
                        f"(--verbose to see them)[/dim]"
                    )
                for note in suggestion.notes:
                    console.print(f"  [yellow]note[/yellow] {note}")

                if commit and suggestion.lines:
                    po_id = create_draft_po(session, plan)
                    if po_id is not None:
                        drafts.append((order_date, spec.name, po_id))
                        console.print(f"  [green]wrote DRAFT purchase order {po_id}[/green]")

            _sim_print_sourcing(result, verbose=verbose)
            _sim_print_emergency(session, result, order_date=order_date, commit=commit)
            for note in result.split.notes:
                console.print(f"[yellow]run note[/yellow] {note}")

        # The weekday shape the forecaster actually found, for one busy ingredient.
        console.rule("[bold]day-of-week factors discovered[/bold]")
        stock_repo = SqlStockRepository(session)
        reference = session.scalar(
            select(Ingredient).where(Ingredient.name == "Whole milk")
        ) or session.scalar(select(Ingredient).where(Ingredient.tier == Tier.A))
        if reference is not None:
            history = stock_repo.daily_consumption(
                reference.id,
                since=end - timedelta(days=knobs.dow_window_weeks * 7 - 1),
                until=end,
            )
            series = daily_series(
                history, start=end - timedelta(days=knobs.dow_window_weeks * 7 - 1), end=end
            )
            factors, notes = dow_factors(
                series,
                factor_min=knobs.dow_factor_min,
                factor_max=knobs.dow_factor_max,
            )
            table = Table(title=f"{reference.name}, {len(series)} day(s) to {end}")
            table.add_column("Weekday")
            table.add_column("Days", justify="right")
            table.add_column("Mean", justify="right")
            table.add_column("Factor", justify="right")
            per_day: dict[int, list[Decimal]] = {}
            for day, qty in series:
                per_day.setdefault(day.isoweekday(), []).append(qty)
            for weekday, label in WEEKDAY_NAMES.items():
                values = per_day.get(weekday, [])
                mean = sum(values, Decimal("0")) / Decimal(len(values)) if values else Decimal("0")
                table.add_row(
                    label,
                    str(len(values)),
                    format_qty(mean, reference.unit),
                    f"{factors[weekday]:.3f}",
                )
            console.print(table)
            for note in notes:
                console.print(f"  [yellow]{note}[/yellow]")
            console.print(
                "[dim]Zero-sales days count as zeros; days before the first observation "
                "do not. A declared closed day is excluded from both the history and the "
                "forecast -- see domain/forecast.py.[/dim]"
            )

        if drafts:
            console.print()
            for order_date, name, po_id in drafts:
                console.print(f"DRAFT {po_id}  {order_date}  {name}")
        console.print(
            "\n[dim]Every order above is a DRAFT. CONFIRMED and SENT need a recorded "
            "human and are refused by ck_po_confirmed_requires_human otherwise "
            "(invariant 1).[/dim]"
        )


# --------------------------------------------------------------------------
# receive / expiry-sweep / open-batch -- the batch lifecycle (spec 4.1)
# --------------------------------------------------------------------------


def _parse_expiry(raw: str | None) -> datetime | None:
    """A date read off a carton. End of that local day, because a best-before date
    means "good through this day", not "good until midnight at its start"."""
    if raw is None:
        return None
    try:
        parsed = date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise typer.BadParameter(f"{raw!r}: expected YYYY-MM-DD (the date on the carton)") from exc
    return datetime.combine(parsed, time.max, tzinfo=settings.tz).astimezone(UTC)


@app.command()
def receive(
    po_line: Annotated[
        int | None, typer.Option("--po-line", help="po_line.id being received.")
    ] = None,
    ingredient: Annotated[
        str | None,
        typer.Option(
            "--ingredient",
            "-i",
            help="Ad-hoc delivery with no order behind it (a Tesco run): ingredient name or id.",
        ),
    ] = None,
    packs: Annotated[
        int | None, typer.Option("--packs", help="Packs that arrived. --po-line only.")
    ] = None,
    qty: Annotated[
        str | None, typer.Option("--qty", help="Quantity in the ingredient's stocking unit.")
    ] = None,
    expires: Annotated[
        str | None,
        typer.Option(
            "--expires",
            help="YYYY-MM-DD from the carton. Omitted means assumed, and the receipt says so.",
        ),
    ] = None,
    at: Annotated[
        str, typer.Option("--at", help="'now', 'today', or YYYY-MM-DD. When it arrived.")
    ] = "now",
    by: Annotated[str, typer.Option("--by", help="Who took the delivery. Recorded.")] = "cli",
    price_pence: Annotated[
        str | None,
        typer.Option("--price-pence", help="Ad-hoc only: what it cost per stocking unit."),
    ] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
) -> None:
    """Receive a delivery: create a batch with its expiry and a linked DELIVERY movement."""
    from cafeops.services.receive_delivery import ReceiveRefused, receive_adhoc, receive_po_line

    if (po_line is None) == (ingredient is None):
        raise typer.BadParameter(
            "give exactly one of --po-line (a confirmed order line) or --ingredient "
            "(an ad-hoc delivery with no order behind it)"
        )
    parsed_qty = None if qty is None else Decimal(qty)
    received_at = _parse_as_of(at)
    expires_at = _parse_expiry(expires)

    with session_scope() as session:
        try:
            if po_line is not None:
                receipt = receive_po_line(
                    session,
                    po_line_id=po_line,
                    received_packs=packs,
                    received_qty=parsed_qty,
                    expires_at=expires_at,
                    received_at=received_at,
                    received_by=by,
                    note=note,
                )
            else:
                assert ingredient is not None
                found = _resolve_ingredient(session, ingredient)
                if parsed_qty is None:
                    raise typer.BadParameter("--qty is required for an ad-hoc delivery")
                receipt = receive_adhoc(
                    session,
                    ingredient_id=found.id,
                    qty=parsed_qty,
                    expires_at=expires_at,
                    received_at=received_at,
                    received_by=by,
                    unit_cost_pence=None if price_pence is None else Decimal(price_pence),
                    note=note,
                )
        except ReceiveRefused as exc:
            console.print(f"[red]REFUSED[/red] {exc}")
            raise typer.Exit(code=1) from exc

        table = Table(
            title=f"{receipt.ingredient_name} -- delivery received",
            title_style="bold",
            show_header=False,
        )
        table.add_column("key")
        table.add_column("value")
        table.add_row("batch", str(receipt.batch_id))
        table.add_row("quantity", format_qty(receipt.qty, receipt.unit))
        table.add_row(
            "received", f"{receipt.received_at.astimezone(settings.tz):%Y-%m-%d %H:%M %Z}"
        )
        if receipt.expires_at is None:
            table.add_row("expires", "[dim]never (this ingredient does not spoil)[/dim]")
        else:
            style = "yellow" if receipt.expiry_was_assumed else "green"
            table.add_row(
                "expires",
                f"[{style}]{receipt.expires_at.astimezone(settings.tz):%Y-%m-%d}"
                f" ({receipt.shelf_life_days_left}d)"
                + (" -- ASSUMED, not read off the carton" if receipt.expiry_was_assumed else "")
                + f"[/{style}]",
            )
        table.add_row("unit cost", f"{receipt.unit_cost_pence:.4f}p per {receipt.unit.value}")
        table.add_row("batch value", f"GBP {receipt.value_pence / 100:.2f}")
        table.add_row("DELIVERY movements", str(receipt.movements_written))
        if receipt.po_line_id is not None:
            table.add_row("po_line", str(receipt.po_line_id))
        if receipt.order_completed:
            table.add_row("order", "[green]every line received -> RECEIVED[/green]")
        console.print(table)
        for warning in receipt.warnings:
            console.print(f"[yellow]warning[/yellow] {warning}")
        console.print(
            "[dim]The batch is sealed: opened_at is NULL, so the effective expiry is the "
            "unopened one. The first FIFO draw opens it and the open-life clock starts "
            "then (services/receive_delivery.py).[/dim]"
        )


@app.command(name="expiry-sweep")
def expiry_sweep_cmd(
    at: Annotated[
        str, typer.Option("--at", help="'now', 'today', or YYYY-MM-DD. Sweep as of this instant.")
    ] = "now",
    ingredient: Annotated[
        str | None, typer.Option("--ingredient", "-i", help="One ingredient only.")
    ] = None,
    short_dated_days: Annotated[
        int, typer.Option("--short-dated-days", help="Also report stock expiring within N days.")
    ] = 3,
    dry_run: Annotated[
        bool, typer.Option("--dry-run/--commit", help="Show the write-offs without booking them.")
    ] = False,
) -> None:
    """Write off batches that reached their expiry with stock left, and say what it cost."""
    from cafeops.jobs.expiry_sweep import sweep_expiry

    swept_at = _parse_as_of(at)
    with session_scope() as session:
        target = None if ingredient is None else _resolve_ingredient(session, ingredient).id
        report = sweep_expiry(
            session,
            at=swept_at,
            ingredient_id=target,
            short_dated_days=short_dated_days,
            dry_run=dry_run,
        )

        console.print(
            f"[bold]expiry sweep[/bold] as of "
            f"{report.swept_at.astimezone(settings.tz):%Y-%m-%d %H:%M %Z}: {report.summary()}"
        )
        if report.write_offs:
            table = Table(title="EXPIRED -- written off", title_style="bold")
            table.add_column("Ingredient", no_wrap=True)
            table.add_column("Batch", justify="right")
            table.add_column("Qty", justify="right", no_wrap=True)
            table.add_column("Expired", no_wrap=True)
            table.add_column("Days ago", justify="right")
            table.add_column("Value lost", justify="right")
            table.add_column("Why")
            for write_off in report.write_offs:
                why = "open life" if write_off.expired_after_opening else "carton date"
                if write_off.expiry_was_assumed:
                    why += " [yellow](ASSUMED)[/yellow]"
                table.add_row(
                    write_off.ingredient_name,
                    str(write_off.batch_id),
                    format_qty(write_off.qty, write_off.unit),
                    f"{write_off.expired_at.astimezone(settings.tz):%Y-%m-%d}",
                    str(write_off.days_overdue),
                    (
                        "[dim]unpriced[/dim]"
                        if write_off.loss_pence is None
                        else f"[red]GBP {write_off.loss_pence / 100:.2f}[/red]"
                    ),
                    why,
                )
            console.print(table)
            console.print(
                f"[red]total written off: GBP {report.total_loss_pence / 100:.2f}[/red]"
                "  -- this is real money, and it is the number that says the cover window "
                "is too long, not that the recipe is wrong."
            )
        if report.short_dated:
            console.print(
                f"\n[yellow]{len(report.short_dated)} batch(es) expiring within "
                f"{short_dated_days} days -- still sellable:[/yellow]"
            )
            for soon in report.short_dated[:12]:
                console.print(f"  {soon.line()}")
        for warning in report.warnings:
            console.print(f"[yellow]warning[/yellow] {warning}")
        console.print(
            "[dim]Idempotent: stock_batch.expired_at and an existing EXPIRED movement both "
            "guard the write-off, so a second run books nothing twice.[/dim]"
        )


@app.command(name="open-batch")
def open_batch_cmd(
    batch: Annotated[int, typer.Option("--batch", help="stock_batch.id that was opened.")],
    at: Annotated[str, typer.Option("--at", help="'now', 'today', or YYYY-MM-DD.")] = "now",
) -> None:
    """Record that a pack was opened, shortening its expiry to its open life.

    Normally unnecessary: the first FIFO draw stamps `opened_at` on its own. Use this
    for a carton opened for prep rather than a sale, or one opened out of FIFO order.
    """
    from cafeops.services.receive_delivery import open_batch

    with session_scope() as session:
        changed, message = open_batch(session, batch_id=batch, at=_parse_as_of(at))
    console.print(f"[{'green' if changed else 'yellow'}]{message}[/]")


# --------------------------------------------------------------------------
# Sub-apps, each defined next to the code it drives (spec 4.6, spec 9).
# --------------------------------------------------------------------------

from cafeops.agent.commands import app as _agent_app  # noqa: E402
from cafeops.integrations.channels.commands import app as _channels_app  # noqa: E402
from cafeops.integrations.lightspeed.commands import app as _pos_app  # noqa: E402
from cafeops.integrations.payments.commands import app as _payments_app  # noqa: E402
from cafeops.services.confirm_commands import shelf_life_app as _shelf_life_app  # noqa: E402
from cafeops.services.confirm_commands import supplier_app as _supplier_app  # noqa: E402

app.add_typer(_channels_app, name="channels")
# The operator path for the doctor's two standing warnings. Without these the
# only way to confirm a supplier term or a shelf life was to edit a seed file.
app.add_typer(_supplier_app, name="supplier")
app.add_typer(_shelf_life_app, name="shelf-life")
app.add_typer(_agent_app, name="agent")
app.add_typer(_pos_app, name="pos")
# The missing half of Money & P&L: what the cafe actually took (ARCHITECTURE 8T).
app.add_typer(_payments_app, name="payments")


# --------------------------------------------------------------------------
# emergency-report  --  the panic-buy log (spec 4.4)
# --------------------------------------------------------------------------


@app.command(name="emergency-report")
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


# --------------------------------------------------------------------------
# serve / api-fixtures  --  the read-only API the web app consumes (spec 10)
# --------------------------------------------------------------------------


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host", help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="Bind port.")] = 8000,
    reload: Annotated[
        bool, typer.Option("--reload/--no-reload", help="Auto-reload on code change.")
    ] = False,
) -> None:
    """Run the read-only back-office API under uvicorn. ONE worker, deliberately.

    Spec 3: one box, one SQLite file, one writer. A second worker would contend on the
    same file and make things worse, so there is no `--workers` flag to reach for
    (ARCHITECTURE.md 8F.8). TLS belongs to Caddy in front, not here.

    Binds to loopback by default. The app is behind a reverse proxy in the real
    deployment, and a back office listening on 0.0.0.0 by default is one firewall rule
    away from being the whole business on the open internet.
    """
    import uvicorn

    from cafeops.api.security import api_password

    if api_password() is None:
        console.print(
            "[yellow]CAFEOPS_API_PASSWORD is not set.[/yellow] The server will start, but "
            "every endpoint except /api/health will answer 503 rather than serve the "
            "business unauthenticated. Set it in .env and restart.\n"
            "[dim]Spec 1: a single shared password, no user management.[/dim]"
        )
    console.print(
        f"[bold]cafeops api[/bold] on http://{host}:{port}  "
        f"docs http://{host}:{port}/api/docs  db {settings.database_url}"
    )
    uvicorn.run(
        "cafeops.api.app:app",
        host=host,
        port=port,
        reload=reload,
        workers=1,
        log_level="info",
    )


@app.command(name="api-fixtures")
def api_fixtures(
    out: Annotated[
        Path, typer.Option("--out", help="Directory to write the JSON fixtures into.")
    ] = Path("web/fixtures"),
    password: Annotated[
        str | None,
        typer.Option(
            "--password",
            help="Shared password to present. Defaults to CAFEOPS_API_PASSWORD.",
        ),
    ] = None,
    allow_errors: Annotated[
        bool,
        typer.Option(
            "--allow-errors",
            help="Publish even if an endpoint did not return 200. Only useful when the "
            "error response IS the fixture you want.",
        ),
    ] = False,
) -> None:
    """Dump one REAL JSON response per endpoint, so the frontend can build against fixtures.

    Not hand-written samples: the app is driven in-process over ASGI and each file is
    what the endpoint actually returned. A fixture that is generated by running the code
    cannot drift from it silently, which a checked-in sample always eventually does.

    Auth is exercised rather than bypassed -- the dump presents the password as a client
    would, so a 401 in `index.json` means the frontend would get one too.
    """
    from cafeops.api.examples import dump_examples
    from cafeops.api.security import api_password

    secret = password or api_password()
    if secret is None:
        console.print(
            "[red]No password, so every protected endpoint would answer 503.[/red] "
            "Pass --password or set CAFEOPS_API_PASSWORD.\n"
            "[dim]Refusing rather than writing error bodies: these files are what the "
            "frontend builds against, and a directory of 503s looks exactly like a "
            "directory of fixtures until a screen renders blank.[/dim]"
        )
        raise typer.Exit(2)

    # Build into a scratch directory and publish only once every endpoint answered 200.
    # Writing in place meant a failed dump replaced good fixtures with error bodies --
    # which happened, and cost somebody a restore from backup. The artefact is either
    # complete or the old one is still there.
    staging = out.parent / f".{out.name}.staging"
    if staging.exists():
        shutil.rmtree(staging)
    written = dump_examples(staging, password=secret)
    bad_now = [name for name, status, _ in written if status != 200]
    if bad_now and not allow_errors:
        console.print(
            f"[red]{len(bad_now)} endpoint(s) did not return 200: "
            f"{', '.join(bad_now)}[/red]\n"
            f"[dim]Nothing was published; {out} is untouched. The staged attempt is in "
            f"{staging} if you want to look at it. Pass --allow-errors to publish "
            "anyway, which is only useful when the error IS the fixture.[/dim]"
        )
        raise typer.Exit(1)

    if out.exists():
        shutil.rmtree(out)
    staging.rename(out)
    table = Table(title=f"{len(written)} fixture(s) -> {out}", title_style="bold")
    table.add_column("Endpoint", no_wrap=True)
    table.add_column("Status", justify="right")
    table.add_column("Bytes", justify="right")
    for name, status, size in written:
        style = "green" if status == 200 else "red"
        table.add_row(name, f"[{style}]{status}[/{style}]", f"{size:,}")
    console.print(table)
    bad = [name for name, status, _ in written if status != 200]
    if bad:
        console.print(f"[red]{len(bad)} endpoint(s) did not return 200: {', '.join(bad)}[/red]")
    console.print("[dim]index.json lists every file with the query behind it.[/dim]")


@app.command()
def doctor(
    verbose: Annotated[
        bool, typer.Option("--verbose", help="Show the checks that passed as well.")
    ] = False,
) -> None:
    """Is this install operable? Run this first when something looks wrong.

    Exits non-zero only on FAIL, so it is safe from cron. A WARN means a number is
    resting on a guess worth replacing; it does not mean the system is broken.
    """
    from cafeops.services.doctor import Severity, run_doctor

    with session_scope() as session:
        report = run_doctor(session)

    style = {
        Severity.FAIL: "bold red",
        Severity.WARN: "yellow",
        Severity.INFO: "dim",
        Severity.OK: "green",
    }
    shown = [c for c in report.checks if verbose or c.severity is not Severity.OK]
    table = Table(title="cafeops doctor", title_style="bold", show_lines=False)
    table.add_column("", justify="center", no_wrap=True)
    table.add_column("Check", no_wrap=True)
    table.add_column("Detail")
    for check in shown:
        table.add_row(
            f"[{style[check.severity]}]{check.severity.value}[/]",
            check.name,
            check.detail + (f"\n[cyan]-> {check.fix}[/cyan]" if check.fix else ""),
        )
    if shown:
        console.print(table)

    passed = sum(1 for c in report.checks if c.severity is Severity.OK)
    if report.is_operable:
        console.print(
            f"[green]Operable.[/green] {passed} check(s) passed, "
            f"{len(report.warnings)} warning(s)."
            + ("" if verbose else "  [dim]--verbose shows what passed.[/dim]")
        )
    else:
        console.print(
            f"[bold red]NOT operable.[/bold red] {len(report.failures)} failure(s) — "
            "the system is producing wrong numbers or will not run. Fix those first."
        )
    raise typer.Exit(report.exit_code())


@app.command(name="scheduler-run")
def scheduler_run() -> None:
    """Run the APScheduler process in the foreground. This is a long-running unit.

    Exists so the scheduler starts the same way every other process does --
    `cafeops serve`, `cafeops bot-run`, `cafeops scheduler-run` -- rather than through
    `python -c "from cafeops.jobs.scheduler import main; main()"`. A deployment that has
    to reach past the CLI into a module path is one rename away from a service that
    silently fails to start, and the scheduler failing silently means no sync, no
    expansion, no expiry sweep and no draft orders, with nothing on screen to say so.

    Use `cafeops jobs` to see what is scheduled and why, without starting anything.
    """
    from cafeops.jobs.scheduler import main as scheduler_main

    console.print(
        "[bold]Starting the scheduler.[/bold] Ctrl-C to stop. "
        "Run [cyan]cafeops jobs[/cyan] to see the schedule without starting it."
    )
    scheduler_main()


if __name__ == "__main__":
    app()


# --------------------------------------------------------------------------
# bot and jobs (agent E)
# --------------------------------------------------------------------------


@app.command(name="bot-preview")
def bot_preview_cmd(
    flow: Annotated[
        str,
        typer.Argument(
            help=(
                "Which flow to drive: start, digest, orders, count, checklist, delivery, "
                "stranger, or all."
            )
        ),
    ] = "digest",
    supplier: Annotated[
        str | None,
        typer.Option("--supplier", help="orders: only the card for this supplier."),
    ] = None,
    answer: Annotated[
        str, typer.Option("--answer", help="count: the quantity to type for the first item.")
    ] = "12",
    packs: Annotated[
        str,
        typer.Option(
            "--packs",
            help=(
                "delivery: how many packs arrived. checklist: how many packs to request "
                "after 'running low' -- 0 presses 'do not order' instead."
            ),
        ),
    ] = "2",
    expiry: Annotated[
        str | None,
        typer.Option(
            "--expiry",
            help=(
                "delivery: the date on the pack, DD.MM. Pass --no-expiry to press "
                "'no date on the pack' instead and get an ASSUMED expiry."
            ),
        ),
    ] = "30.09",
    no_expiry: Annotated[
        bool, typer.Option("--no-expiry", help="delivery: there is no date on the pack.")
    ] = False,
    full_count: Annotated[
        bool, typer.Option("--full", help="count: the weekly full walk instead of tier A.")
    ] = False,
    ok: Annotated[
        bool, typer.Option("--ok", help="checklist: answer 'enough' instead of 'running low'.")
    ] = False,
    adjust: Annotated[
        bool, typer.Option("--adjust/--no-adjust", help="orders: press +1 on the first line.")
    ] = True,
    confirm: Annotated[
        bool, typer.Option("--confirm/--no-confirm", help="orders: press Confirm.")
    ] = True,
    buttons: Annotated[
        bool, typer.Option("--buttons/--no-buttons", help="Show the inline keyboards.")
    ] = True,
    commit: Annotated[
        bool,
        typer.Option(
            "--commit/--dry-run",
            help="Keep what the flows write. Default rolls it back.",
        ),
    ] = False,
) -> None:
    """Drive the REAL bot handlers locally and print the Russian they produce.

    No Telegram and no token: the dispatcher, routers, filters, FSM and keyboards are
    the real ones, and only the HTTP session is replaced by one that records outgoing
    calls. Nothing can reach a real cafe. See `cafeops/bot/preview.py`.

    **It is the database that needs the care, not the wire.** These are the real
    handlers, so `delivery` really receives a delivery, `orders` really confirms a
    purchase order and `count` really writes a count. "Nothing is sent" was only ever
    about Telegram, and reading it as "nothing happens" left a preview run's batches
    and movements sitting in a live ledger.

    So this now defaults to **--dry-run**, like every other writing command here
    (`import-legacy`, `channels import`, `materialise-template`): the flows run
    against a real session inside a transaction that is rolled back at the end, so
    what you read is exactly what would have been written. Pass `--commit` to keep it.
    """
    import asyncio

    from cafeops.bot.preview import FLOWS, Preview, render

    wanted = flow.strip().lower()
    names = list(FLOWS) if wanted == "all" else [wanted]
    unknown = [name for name in names if name not in FLOWS]
    if unknown:
        raise typer.BadParameter(f"unknown flow(s) {unknown}; choose from {sorted(FLOWS)} or 'all'")

    kwargs: dict[str, object] = {}

    async def drive(name: str, factory: object | None) -> str:
        preview = Preview(factory=factory)  # type: ignore[arg-type]
        try:
            if name == "orders":
                sent = await FLOWS[name](preview, supplier=supplier, adjust=adjust, confirm=confirm)
            elif name == "count":
                sent = await FLOWS[name](preview, express=not full_count, answer=answer)
            elif name == "checklist":
                # `--packs 0` presses BTN_CHECKLIST_NO_ORDER instead: marking an item low
                # without naming a quantity is a real answer, and the flow has to be able
                # to show it.
                sent = await FLOWS[name](
                    preview, low=not ok, packs=None if packs.strip() in ("", "0") else packs
                )
            elif name == "delivery":
                sent = await FLOWS[name](preview, packs=packs, expiry=None if no_expiry else expiry)
            else:
                sent = await FLOWS[name](preview, **kwargs)
            return render(sent, show_buttons=buttons)
        finally:
            await preview.close()

    from contextlib import ExitStack

    from sqlalchemy.orm import sessionmaker

    from cafeops.db.base import engine as _engine

    with ExitStack() as stack:
        factory: object | None = None
        if not commit:
            # A real session on a real connection, inside a transaction nobody
            # commits: the handlers' own `session.commit()` calls flush without
            # committing the outer transaction, and the rollback at the end undoes
            # all of it.
            #
            # `rollback_only`, NOT `create_savepoint`. Measured on this stack:
            # create_savepoint LEAKS -- pysqlite's legacy implicit-transaction
            # handling turns the RELEASE of a savepoint into a real commit, so the
            # outer rollback has nothing left to undo and the "dry run" writes for
            # real. rollback_only needs no savepoints and was verified to roll back.
            connection = stack.enter_context(_engine.connect())
            transaction = connection.begin()
            stack.callback(transaction.rollback)
            factory = sessionmaker(
                bind=connection, join_transaction_mode="rollback_only", expire_on_commit=False
            )

        mode = (
            "[yellow]--commit: what these flows write is KEPT[/yellow]"
            if commit
            else "dry run: nothing is sent, and what the flows write is rolled back"
        )
        for name in names:
            console.rule(f"[bold]cafeops bot-preview {name}[/bold]  ({mode})")
            text = asyncio.run(drive(name, factory))
            if not text.strip():
                console.print("[yellow](the bot answered nothing)[/yellow]")
            else:
                print(text)
            console.print()


@app.command(name="jobs")
def jobs_cmd(
    run: Annotated[
        str | None,
        typer.Option(
            "--run",
            help=(
                "Fire one job by hand: daily_sync, nightly_expand, expiry_sweep, "
                "pre_delivery_orders, digest, drift_report."
            ),
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help=(
                "pre_delivery_orders: ignore the idempotency guard and write a second "
                "draft for a delivery date that already has one."
            ),
        ),
    ] = False,
    order_date: Annotated[
        str | None,
        typer.Option("--order-date", help="pre_delivery_orders: replay a past day, YYYY-MM-DD."),
    ] = None,
) -> None:
    """List the schedule, or fire one job by hand.

    With no options this prints every trigger and the idempotency key that makes a late run
    safe. Nothing is scheduled by this command -- `cafeops.jobs.scheduler:main` is the
    long-running unit.
    """
    import asyncio
    import logging
    from datetime import date as _date

    from cafeops.jobs.scheduler import JOBS, describe_schedule

    # Jobs report through the logger, because in production they run headless under
    # systemd. Firing one by hand has to show that same output rather than nothing.
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if run is None:
        print(describe_schedule())
        return
    name = run.strip()
    if name not in JOBS:
        raise typer.BadParameter(f"unknown job {name!r}; choose from {sorted(JOBS)}")

    if name == "pre_delivery_orders":
        from cafeops.jobs.pre_delivery_orders import run_pre_delivery_orders

        when = _date.fromisoformat(order_date) if order_date else None
        with session_scope() as session:
            report = run_pre_delivery_orders(session, order_date=when, force=force)
        print(report.summary())
        for outcome in report.outcomes:
            if outcome.skipped and outcome.due:
                console.print(f"  [yellow]{outcome.supplier_name}: {outcome.skipped}[/yellow]")
        for warning in report.warnings:
            console.print(f"  [red]{warning}[/red]")
        return

    asyncio.run(JOBS[name]())
    console.print(f"[green]{name} finished. See the log lines above.[/green]")


@app.command(name="bot-run")
def bot_run_cmd() -> None:
    """Start the Telegram bot. Refuses without a token and an owner chat id."""
    from cafeops.bot.app import BotNotConfigured
    from cafeops.bot.app import main as bot_main

    try:
        bot_main()
    except BotNotConfigured as exc:
        # A clean sentence and a distinct exit code, not a traceback. 78 is sysexits
        # EX_CONFIG: "not configured" is a stable state, not a crash, and it must be
        # distinguishable from one. Exiting 1 here made both supervisors restart the bot
        # every few seconds forever, which looks exactly like a crash loop and buries any
        # real error under two log lines a second. systemd stops on 78 via
        # RestartPreventExitStatus; compose bounds it with `restart: on-failure:3`.
        console.print(f"[yellow]{exc}[/yellow]")
        raise typer.Exit(code=78) from None
