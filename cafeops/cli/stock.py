"""Stock: theoretical on-hand, sale expansion, the Lightspeed sync, counts, drift and
the auto-order gate, plus `info` and the password sub-app."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.table import Table

from cafeops.api.security import api_password
from cafeops.cli._common import console, parse_as_of, resolve_ingredient
from cafeops.config import settings
from cafeops.db.base import session_scope
from cafeops.domain.types import GateAlertLevel, Tier, Unit
from cafeops.domain.units import format_qty

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from cafeops.db.repositories.drift import DriftHistoryRow
    from cafeops.db.repositories.par import AutoOrderAudit
    from cafeops.domain.tiers import GateDecision
    from cafeops.domain.types import DriftVerdict, IngredientSnapshot
    from cafeops.services.record_count import CountOutcome

    #: What `drift --ingredient` collects per subject for the per-ingredient detail.
    DetailPayload = tuple[
        IngredientSnapshot, list[DriftHistoryRow], GateDecision, AutoOrderAudit | None
    ]

# --------------------------------------------------------------------------
# stock
# --------------------------------------------------------------------------


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

    at = parse_as_of(as_of)
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
    unbatched: list[tuple[str, Decimal, Unit]] = []
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
            console.print(f"  {name}: {format_qty(gap, unit)} unbatched")

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


def expand(
    limit: Annotated[int | None, typer.Option(help="Max sale lines to expand.")] = None,
) -> None:
    """Expand un-expanded sales into SALE stock movements."""
    from cafeops.services.expand_recipes import expand_pending

    with session_scope() as session:
        report = expand_pending(session, limit=limit)
    console.print(report.summary())


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

    now = parse_as_of(as_of) if as_of else datetime.now(UTC)
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
    source = "CAFEOPS_API_PASSWORD" if api_password() else "nothing (set CAFEOPS_API_PASSWORD)"
    console.print(
        f"[green]reset[/green]: {revoked} session(s) revoked; the password is now {source}"
    )


# --------------------------------------------------------------------------
# count / drift  (spec 5.2 -- the trust metric and the auto-order gate)
# --------------------------------------------------------------------------


def _verdict_style(verdict: DriftVerdict | None) -> str:
    from cafeops.domain.types import DriftVerdict

    if verdict is DriftVerdict.FORCE_MANUAL:
        return "red"
    if verdict is DriftVerdict.TUNE_WASTE_FACTOR:
        return "yellow"
    return "green"


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
        found = resolve_ingredient(session, ingredient)
        outcome = record_count(
            session,
            ingredient_id=found.id,
            counted_qty=counted,
            counted_at=parse_as_of(at),
            counted_by=by,
            note=note,
        )
        _print_outcome(outcome)


def _print_outcome(outcome: CountOutcome) -> None:
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
            found = resolve_ingredient(session, apply_waste)
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
            subjects = [resolve_ingredient(session, ingredient)]
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

        detail: list[tuple[str, DetailPayload]] = []
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
            _print_attribution(session, subjects, history)
        console.print(
            "[dim]Drift = (theoretical - counted) / max(counted, epsilon) * 100. The gate "
            "reads |drift|: over-stating stock and under-stating it are both untrustworthy. "
            f"Recent obs and the mean are over the last --history observations ({history}); "
            "the streak is counted over the stored series.[/dim]"
        )
        _print_detail(detail)


def _print_attribution(
    session: Session, subjects: Sequence[IngredientSnapshot], history: int
) -> None:
    """Spec 5.2's v2 diagnostic: which problem is this, and therefore what do you fix.

    Over-ordering and a bad recipe have opposite fixes, so an undifferentiated drift
    percentage sends the owner the wrong way roughly half the time. `EXPIRED` movements
    are the evidence that separates them.
    """
    from cafeops.domain.drift import DriftCause
    from cafeops.services.record_count import explain_drift_history

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

    seen: list[tuple[str, DriftCause, str, str]] = []
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


def _print_detail(detail: list[tuple[str, DetailPayload]]) -> None:
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


def register(app: typer.Typer) -> None:
    app.command()(stock)
    app.command()(expand)
    app.command()(ingredients)
    app.command()(sync)
    app.add_typer(password_app, name="password")
    app.command()(count)
    app.command()(drift)
    app.command()(info)
