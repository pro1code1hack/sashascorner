"""`cafeops portal ...` and `cafeops browser-worker`.

docs/agents/BROWSER-ORDERING.md §6 and §8. `connect` is the one command that opens a
HEADED browser on the machine it runs on: a person signs in (2FA and all) in the
window, the profile keeps the cookies, and -- when `CAFEOPS_BROWSER_SESSION_KEY` is
set -- the storage state is encrypted into `supplier_session` so it can be moved to
the server with `export-session` / `import-session`. Nothing here ever types a
password; the person does, in the window.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.table import Table
from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.cli._common import console
from cafeops.config import settings
from cafeops.db.base import session_scope
from cafeops.db.models import BrowserJobStatus, Supplier

portal_app = typer.Typer(
    no_args_is_help=True,
    help="Supplier web-shop integrations: sign-ins, basket staging jobs, the worker queue. "
    "Nothing here submits an order (invariant 1).",
)


def _resolve_supplier(session: Session, text: str) -> Supplier:
    """Exact id, else case-insensitive name."""
    if text.strip().isdigit():
        found = session.get(Supplier, int(text.strip()))
        if found is not None:
            return found
    rows = list(session.scalars(select(Supplier).where(Supplier.archived_at.is_(None))))
    for row in rows:
        if row.name.lower() == text.strip().lower():
            return row
    partial = [row for row in rows if text.strip().lower() in row.name.lower()]
    if len(partial) == 1:
        return partial[0]
    names = ", ".join(f"{row.id}={row.name}" for row in rows)
    raise typer.BadParameter(f"no supplier {text!r}; known: {names}")


def _when(value: datetime | None) -> str:
    return "-" if value is None else f"{value.astimezone(settings.tz):%d %b %H:%M}"


_STATE_STYLE = {"connected": "green", "expired": "red", "check failed": "red"}


def _job_line(job: Any) -> str:
    if job is None:
        return "-"
    return f"#{job.id} {job.kind.value.lower().replace('_', ' ')} {job.status.value.lower()}"


# ==========================================================================
# list / check / stage / jobs
# ==========================================================================


@portal_app.command(name="list")
def list_cmd() -> None:
    """Every supplier: portal adapter, sign-in state, auto-stage, last job."""
    from cafeops.services.browser_jobs import integrations_overview

    with session_scope() as session:
        rows = integrations_overview(session)
        table = Table(title="Supplier portals", title_style="bold", width=118)
        table.add_column("id", justify="right")
        table.add_column("Supplier")
        table.add_column("Portal")
        table.add_column("Tiers")
        table.add_column("Sign-in")
        table.add_column("Account")
        table.add_column("Auto")
        table.add_column("Last job")
        table.add_column("Can stage?")
        for row in rows:
            state = row.session.status.value.lower().replace("_", " ")
            style = _STATE_STYLE.get(state, "dim")
            tiers = ", ".join(t.replace("_", " ") for t in row.tiers)
            if row.portal is not None and row.portal.prefers_headed:
                tiers += " (headed)"
            table.add_row(
                str(row.supplier_id),
                row.supplier_name,
                row.portal.label if row.portal else "[dim]none[/dim]",
                tiers or "-",
                f"[{style}]{state}[/{style}]",
                row.session.account_label or "-",
                "yes" if row.auto_stage else "",
                _job_line(row.last_job),
                "[green]yes[/green]" if row.can_stage else f"[dim]{row.cannot_stage_reason}[/dim]",
            )
        console.print(table)
    worker = "on" if settings.browser_worker_enabled else "OFF (CAFEOPS_BROWSER_WORKER_ENABLED)"
    key = "set" if settings.browser_session_key else "not set (no session export/import)"
    console.print(
        f"[dim]worker: {worker} | session key: {key} | model: {settings.browser_model}[/dim]"
    )


@portal_app.command()
def check(
    supplier: Annotated[str, typer.Argument(help="Supplier id or name.")],
    run_now: Annotated[bool, typer.Option("--run-now", help="Run the worker once, here.")] = False,
    by: Annotated[str, typer.Option("--by", help="Who asked.")] = "cli",
) -> None:
    """Queue a CHECK_SESSION job: is the stored sign-in still valid?"""
    from cafeops.services.browser_jobs import BrowserJobRefused, enqueue_session_check

    with session_scope() as session:
        row = _resolve_supplier(session, supplier)
        try:
            job = enqueue_session_check(session, supplier_id=row.id, requested_by=by, via="cli")
        except BrowserJobRefused as exc:
            console.print(f"[red]refused:[/red] {exc}")
            raise typer.Exit(code=1) from exc
        job_id = job.id
    console.print(f"queued job #{job_id} (check session for {supplier})")
    if run_now:
        _run_once_and_report(job_id)


@portal_app.command()
def stage(
    po_id: Annotated[int, typer.Argument(help="A DRAFT or CONFIRMED purchase order id.")],
    run_now: Annotated[bool, typer.Option("--run-now", help="Run the worker once, here.")] = False,
    by: Annotated[str, typer.Option("--by", help="Who asked.")] = "cli",
) -> None:
    """Stage an order's basket: a cart link right now when the adapter can build one,
    else a queued STAGE_BASKET job. The basket is filled; a person pays."""
    from cafeops.services.browser_jobs import BrowserJobRefused, enqueue_stage_basket, get_job

    with session_scope() as session:
        try:
            job = enqueue_stage_basket(session, po_id=po_id, requested_by=by, via="cli")
        except BrowserJobRefused as exc:
            console.print(f"[red]refused:[/red] {exc}")
            raise typer.Exit(code=1) from exc
        job_id = job.id
        count = len(job.params.get("lines", []))
        done_inline = job.status is BrowserJobStatus.SUCCEEDED
        partial = job.params.get("cart_link")
    if done_inline:
        with session_scope() as session:
            _print_job(get_job(session, job_id=job_id))
        return
    note = ""
    if isinstance(partial, dict):
        note = (
            f"; a cart link covers {len(partial.get('covered') or [])} of them and the "
            "worker opens it first"
        )
    console.print(f"queued job #{job_id}: stage {count} line(s) of order {po_id}{note}")
    if run_now:
        _run_once_and_report(job_id)


def _run_once_and_report(job_id: int) -> None:
    from cafeops.agent.browser.worker import run_worker
    from cafeops.services.browser_jobs import get_job

    ran = run_worker(once=True)
    if ran == 0:
        console.print("[yellow]the worker found nothing to run[/yellow]")
        return
    with session_scope() as session:
        job = get_job(session, job_id=job_id)
        _print_job(job)


def _print_job(job: Any) -> None:
    console.print(
        f"[bold]job #{job.id}[/bold] {job.kind.value} [bold]{job.status.value}[/bold] "
        f"supplier={job.supplier_id} po={job.purchase_order_id or '-'} by={job.requested_by} "
        f"({job.requested_via})"
    )
    console.print(
        f"  started {_when(job.started_at)} finished {_when(job.finished_at)} "
        f"steps={job.steps_total} model_calls={job.model_calls} "
        f"tokens={job.input_tokens}/{job.output_tokens}"
    )
    if job.error:
        console.print(f"  [red]error:[/red] {job.error}")
    if job.needs_human_reason:
        console.print(f"  [yellow]needs a person:[/yellow] {job.needs_human_reason}")
    if job.proposal_id:
        console.print(f"  proposal #{job.proposal_id} (Agents page)")
    result = job.result or {}
    tier = result.get("tier")
    if tier or result.get("tiers_used"):
        used = ", ".join(str(t).replace("_", " ") for t in result.get("tiers_used") or [])
        console.print(
            f"  tier: [bold]{str(tier).replace('_', ' ') if tier else 'none'}[/bold]"
            + (f" (used: {used})" if used else "")
        )
    if result.get("basket_url"):
        label = "cart link" if tier == "cart_link" else "basket"
        console.print(f"  {label}: {result['basket_url']}")
    for warning in result.get("warnings") or []:
        console.print(f"  [yellow]warning:[/yellow] {warning}")
    if job.steps:
        table = Table(title="steps", title_style="bold", width=118)
        table.add_column("#", justify="right")
        table.add_column("src")
        table.add_column("member")
        table.add_column("outcome")
        table.add_column("output / reason")
        table.add_column("url")
        for step in job.steps:
            style = {"OK": "green", "ERROR": "yellow", "REFUSED": "red"}[step.outcome.value]
            table.add_row(
                str(step.seq),
                step.source.value,
                step.member,
                f"[{style}]{step.outcome.value}[/{style}]",
                (step.refusal_reason or step.output or "")[:70],
                (step.url or "")[:40],
            )
        console.print(table)


@portal_app.command()
def jobs(
    job: Annotated[int | None, typer.Option("--job", help="One job, with its steps.")] = None,
    status: Annotated[str | None, typer.Option("--status", help="QUEUED|RUNNING|...")] = None,
    limit: Annotated[int, typer.Option("--limit")] = 20,
) -> None:
    """The job queue, newest first."""
    from cafeops.services.browser_jobs import get_job, list_jobs

    with session_scope() as session:
        if job is not None:
            _print_job(get_job(session, job_id=job))
            return
        chosen = BrowserJobStatus(status.strip().upper()) if status else None
        rows, more = list_jobs(session, status=chosen, limit=limit)
        if not rows:
            console.print("[yellow]no jobs[/yellow]")
            return
        table = Table(title="browser_job", title_style="bold", width=118)
        table.add_column("id", justify="right")
        table.add_column("kind")
        table.add_column("status")
        table.add_column("supplier", justify="right")
        table.add_column("po", justify="right")
        table.add_column("by")
        table.add_column("queued")
        table.add_column("finished")
        table.add_column("steps", justify="right")
        for row in rows:
            style = {
                "SUCCEEDED": "green",
                "FAILED": "red",
                "NEEDS_HUMAN": "yellow",
                "RUNNING": "cyan",
            }.get(row.status.value, "")
            table.add_row(
                str(row.id),
                row.kind.value.lower().replace("_", " "),
                f"[{style}]{row.status.value}[/{style}]",
                str(row.supplier_id),
                str(row.purchase_order_id or "-"),
                row.requested_by,
                _when(row.created_at),
                _when(row.finished_at),
                str(row.steps_total),
            )
        console.print(table)
        if more:
            console.print("[dim]more; raise --limit[/dim]")


# ==========================================================================
# connect / export / import / forget
# ==========================================================================


@portal_app.command()
def connect(
    supplier: Annotated[str, typer.Argument(help="Supplier id or name.")],
    by: Annotated[str, typer.Option("--by", help="Who is signing in.")] = "cli",
) -> None:
    """Open a HEADED browser on this machine, wait for a person to sign in, store it."""
    from cafeops.agent.browser.session import SessionKeyMissing, SignInCheckFailed, check_signed_in
    from cafeops.agent.browser.types import BrowserAction
    from cafeops.db.models import SupplierSession
    from cafeops.integrations.suppliers.portals.base import PortalNeedsHuman, portal_for_supplier
    from cafeops.services.browser_jobs import import_session_state

    with session_scope() as session:
        row = _resolve_supplier(session, supplier)
        portal = portal_for_supplier(row)
        if portal is None:
            console.print(f"[red]{row.name} has no portal adapter[/red]")
            raise typer.Exit(code=1)
        session_row = session.scalar(
            select(SupplierSession).where(SupplierSession.supplier_id == row.id)
        )
        if session_row is None:
            session_row = SupplierSession(supplier_id=row.id)
            session.add(session_row)
            session.flush()
        supplier_id, supplier_name = row.id, row.name
        # The profile dir is computed and persisted by open_supplier_browser.
        from cafeops.agent.browser.session import open_supplier_browser

        console.print(f"opening {portal.label} for {supplier_name} in a browser window...")
        executor = open_supplier_browser(session_row, portal, headless=False)
        session.commit()
    try:
        executor.execute(BrowserAction("navigate", {"url": portal.policy.login_url}))
        console.print(
            "[bold]Sign in in the window (2FA and all), then press Enter here.[/bold] "
            "Nothing is typed for you; the password never reaches this program."
        )
        input()
        try:
            signed_in, label = check_signed_in(executor, portal)
        except (SignInCheckFailed, PortalNeedsHuman) as exc:
            console.print(f"[red]could not check the sign-in: {exc}[/red]")
            raise typer.Exit(code=1) from exc
        if not signed_in:
            console.print("[red]the portal does not show a signed-in account; nothing stored[/red]")
            raise typer.Exit(code=1)
        state: dict[str, Any] | None = None
        export = getattr(executor, "export_storage_state", None)
        if callable(export):
            state = export()
    finally:
        executor.close()
    with session_scope() as session:
        try:
            import_session_state(
                session,
                supplier_id=supplier_id,
                storage_state=state,
                storage_state_enc=None,
                account_label=label,
                connected_by=by,
            )
            stored = "storage state stored (encrypted) and profile kept"
        except SessionKeyMissing:
            import_session_state(
                session,
                supplier_id=supplier_id,
                storage_state=None,
                storage_state_enc=None,
                account_label=label,
                connected_by=by,
            )
            stored = (
                "profile kept on this machine only: CAFEOPS_BROWSER_SESSION_KEY is unset, so "
                "the sign-in was not stored for export"
            )
    console.print(f"[green]connected[/green] {supplier_name} as {label or '(account)'}; {stored}")


@portal_app.command(name="export-session")
def export_session(
    supplier: Annotated[str, typer.Argument(help="Supplier id or name.")],
    out: Annotated[Path, typer.Option("--out", help="Where to write the encrypted blob.")],
) -> None:
    """Write the encrypted storage state to a file, to scp to the server."""
    from cafeops.db.models import SupplierSession

    with session_scope() as session:
        row = _resolve_supplier(session, supplier)
        session_row = session.scalar(
            select(SupplierSession).where(SupplierSession.supplier_id == row.id)
        )
        if session_row is None or not session_row.storage_state_enc:
            console.print(
                f"[red]{row.name} has no stored storage state[/red] "
                "(connect with a session key set)"
            )
            raise typer.Exit(code=1)
        out.write_bytes(bytes(session_row.storage_state_enc))
        out.chmod(0o600)
    console.print(f"wrote {out} ({out.stat().st_size} bytes, Fernet-encrypted)")


@portal_app.command(name="import-session")
def import_session(
    supplier: Annotated[str, typer.Argument(help="Supplier id or name.")],
    file: Annotated[Path, typer.Option("--file", help="The blob from export-session.")],
    by: Annotated[str, typer.Option("--by", help="Who signed in.")],
    account: Annotated[str | None, typer.Option("--account", help="Masked account label.")] = None,
) -> None:
    """Import an encrypted storage state exported on another machine."""
    from cafeops.services.browser_jobs import BrowserJobRefused, import_session_state

    blob = file.read_bytes()
    with session_scope() as session:
        row = _resolve_supplier(session, supplier)
        try:
            session_row = import_session_state(
                session,
                supplier_id=row.id,
                storage_state=None,
                storage_state_enc=blob,
                account_label=account,
                connected_by=by,
            )
        except (BrowserJobRefused, ValueError) as exc:
            console.print(f"[red]refused:[/red] {exc}")
            raise typer.Exit(code=1) from exc
        console.print(
            f"[green]{row.name}[/green] is {session_row.status.value.lower()} "
            f"(by {session_row.connected_by}); the worker seeds its profile on first use"
        )


@portal_app.command()
def forget(
    supplier: Annotated[str, typer.Argument(help="Supplier id or name.")],
    by: Annotated[str, typer.Option("--by", help="Who is forgetting it.")],
) -> None:
    """Drop the stored sign-in and delete the profile directory."""
    from cafeops.services.browser_jobs import forget_session

    with session_scope() as session:
        row = _resolve_supplier(session, supplier)
        session_row = forget_session(session, supplier_id=row.id, by=by)
        console.print(f"{row.name}: {session_row.status.value.lower()} ({session_row.last_error})")


# ==========================================================================
# The worker entry point (mounted as `cafeops browser-worker`)
# ==========================================================================


def worker_command(
    once: Annotated[
        bool, typer.Option("--once", help="Run at most one queued job, then exit.")
    ] = False,
) -> None:
    """Run the browser job worker: the only process that opens a browser."""
    from cafeops.agent.browser.worker import run_worker
    from cafeops.logging_setup import configure_logging

    configure_logging()
    ran = run_worker(once=once)
    if once:
        console.print(f"ran {ran} job(s)")


__all__ = ["portal_app", "worker_command"]
