"""Back-office CLI. The owner never sees this -- her only interface is Telegram."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from cafeops.config import settings
from cafeops.db.base import SessionFactory, session_scope
from cafeops.domain.types import Tier
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

    if not demo:
        console.print("[green]done[/green] (pass --demo for the full scenario)")
        return

    with session_scope() as session:
        console.print("\n[bold]Building[/bold] demo composition and sales")
        demo_report = seed_demo(session, days=days)

    from cafeops.services.expand_recipes import expand_pending

    with session_scope() as session:
        console.print("[bold]Expanding[/bold] sales into stock movements")
        expansion = expand_pending(session)
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
) -> None:
    """Ingest Lightspeed sales for a date window. Idempotent on lightspeed_line_id."""
    from cafeops.integrations.lightspeed.sync import sync_window

    try:
        since = date.fromisoformat(from_)
        until = date.fromisoformat(to)
    except ValueError as exc:
        raise typer.BadParameter(f"expected YYYY-MM-DD: {exc}") from exc
    if since > until:
        raise typer.BadParameter(f"--from {since} is after --to {until}")

    if not fixtures and not settings.lightspeed_configured:
        raise typer.BadParameter(
            "Lightspeed is not configured (fixtures only) -- pass --fixtures, "
            "or set CAFEOPS_LIGHTSPEED_CLIENT_ID / CAFEOPS_LIGHTSPEED_CLIENT_SECRET "
            "(and the refresh token + business id) in .env."
        )

    with session_scope() as session:
        result = sync_window(session, since=since, until=until, fixtures=fixtures)

    console.print(f"[bold]cafeops sync[/bold] {since}..{until}")
    for line in result.lines():
        style = (
            "yellow"
            if line.strip().startswith(("UNRESOLVED", "AMBIGUOUS", "SUBSTITUTION"))
            else None
        )
        console.print(f"  {line}" if style is None else f"  [{style}]{line}[/{style}]")
    console.print("[green]done[/green]")


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
    table.add_row(
        "auto_order_enabled",
        ("[green]True[/green]" if decision.auto_order_enabled else "False")
        + (" (changed)" if decision.changed else " (unchanged)"),
    )
    table.add_row("clean streak", f"{decision.clean_streak} of {decision.required_streak}")
    console.print(table)

    for text in outcome.notes:
        console.print(f"[dim]note: {text}[/dim]")
    if outcome.alert:
        console.print(
            f"[bold red]ALERT[/bold red] {ing.name}: theoretical stock is not "
            "trustworthy. Auto-ordering is OFF and every order for it needs a human."
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
) -> None:
    """Drift history and auto-order gate status per ingredient (spec 5.2)."""
    from cafeops.db.repositories.drift import SqlDriftRepository
    from cafeops.db.repositories.ingredient import SqlIngredientRepository
    from cafeops.db.repositories.par import SqlParLevelRepository
    from cafeops.domain.drift import mean_abs_drift_pct
    from cafeops.services.record_count import (
        apply_waste_suggestion,
        backfill_drift_observations,
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
        console.print(
            "[dim]Drift = (theoretical - counted) / max(counted, epsilon) * 100. The gate "
            "reads |drift|: over-stating stock and under-stating it are both untrustworthy. "
            f"Recent obs and the mean are over the last --history observations ({history}); "
            "the streak is counted over the stored series.[/dim]"
        )
        _print_detail(detail)


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
    table.add_column("Proposal", no_wrap=True)
    table.add_column("Items", justify="right")
    table.add_column("Sizes")
    table.add_column("Fixed", justify="right")
    table.add_column("Axes")
    table.add_column("Conflicts", justify="right")
    for proposal in found[:limit]:
        axes = ", ".join(f"{a.name}x{a.option_count}" for a in proposal.axes) or "-"
        conflict_text = f"[yellow]{len(proposal.conflicts)}[/yellow]" if proposal.conflicts else "-"
        table.add_row(
            proposal.name,
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


@app.command(name="materialise-template")
def materialise_template_cmd(
    name: Annotated[str, typer.Argument(help="Proposal name, as printed by `cafeops proposals`.")],
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
        preview_component_qty_change,
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
            preview = preview_component_qty_change(session, component, qty_by_size=qty_by_size)
            _render_preview(preview, header)
            console.print(
                "\n[yellow]PREVIEW: nothing written. Re-run with --commit to apply from "
                "today.[/yellow]"
            )
            return

        result = apply_component_qty_change(
            session, component, qty_by_size=qty_by_size, actor=actor
        )
        _render_preview(result.preview, header)
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
        new_per_unit = Decimal(pack_cost) / size if size else None
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
    "CakeSmiths and Cups Direct lead times, delivery weekdays and minimum orders are "
    "INVENTED PLACEHOLDERS (ARCHITECTURE.md 8.2). Every cover window below is only as "
    "good as they are, and nothing in the forecaster or the sizing has been tuned "
    "against them. Confirm the real terms with each supplier before trusting an order "
    "size. Tesco's terms (walk-in, any day, lead 0, no minimum) are real.",
    "Every order below is a DRAFT for human confirmation (invariant 1), so "
    "par_level.auto_order_enabled does not gate it unless --require-auto-order is "
    "passed. That flag is the unattended path, where invariant 2 does gate it.",
    "This replay reads only what was knowable on each order date: on-hand at local "
    "midnight that morning, history to the previous day. No hindsight.",
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
        console.print(
            f"      forecast {_sim_qty(line.forecast_qty, unit)}"
            f"  - on-hand {_sim_qty(line.on_hand_qty, unit)}"
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
                "WHAT-IF: override every supplier's minimum order. The stored minimums for "
                "CakeSmiths and Cups Direct are invented placeholders, so asking 'what if "
                "it were really X' is the only honest way to exercise the top-up."
            ),
        ),
    ] = None,
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
    from dataclasses import replace as dc_replace

    from sqlalchemy import select

    from cafeops.db.models import Ingredient, ParLevel
    from cafeops.db.repositories.stock import SqlStockRepository
    from cafeops.db.repositories.supplier import PLACEHOLDER_TERMS, SqlSupplierRepository
    from cafeops.domain.forecast import WEEKDAY_NAMES, daily_series, dow_factors
    from cafeops.domain.ordering import pounds
    from cafeops.services.build_order import ForecastKnobs, build_order_plan, create_draft_po

    if order_weekday < 1 or order_weekday > 7:
        raise typer.BadParameter(f"--order-weekday must be 1..7 (Mon..Sun), got {order_weekday}")
    cadence: int | None = None if cadence_days == 0 else cadence_days
    if cadence is not None and cadence < 1:
        raise typer.BadParameter("--cadence-days must be 0 (spec literal) or a positive integer")

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
        suppliers = SqlSupplierRepository(session).list_all()
        if supplier:
            wanted = supplier.strip().lower()
            suppliers = [s for s in suppliers if s.name.lower() == wanted]
            if not suppliers:
                raise typer.BadParameter(f"{supplier!r}: no such supplier")
        if min_order_pence is not None:
            suppliers = [dc_replace(s, min_order_pence=min_order_pence) for s in suppliers]

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
        for caveat in SIM_CAVEATS:
            console.print(f"[yellow]CAVEAT[/yellow] {caveat}")
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
            for spec in suppliers:
                plan = build_order_plan(
                    session,
                    supplier_id=spec.id,
                    order_date=order_date,
                    knobs=knobs,
                    reorder_cadence_days=cadence,
                    require_auto_order=require_auto_order,
                )
                suggestion = plan.suggestion
                if min_order_pence is not None:
                    # Re-size against the what-if minimum rather than re-labelling the
                    # result: the top-up is part of sizing, not presentation.
                    from cafeops.domain.ordering import build_suggestion

                    plan = build_suggestion(
                        supplier=spec,
                        target_delivery_date=suggestion.target_delivery_date,
                        cover_window=suggestion.cover_window,
                        candidates=tuple(o.candidate for o in plan.outcomes),
                    )
                    suggestion = plan.suggestion

                window = suggestion.cover_window
                placeholder = (
                    " [yellow](terms are PLACEHOLDERS)[/yellow]"
                    if (spec.name in PLACEHOLDER_TERMS)
                    else ""
                )
                console.print(
                    f"\n[bold cyan]{spec.name}[/bold cyan]{placeholder}"
                    f"  lead {spec.lead_time_days}d"
                    f"  delivers {_sim_delivery_text(spec)}"
                    f"  min order {pounds(spec.min_order_pence)}"
                    f"  channel {spec.order_channel.value}"
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


if __name__ == "__main__":
    app()
