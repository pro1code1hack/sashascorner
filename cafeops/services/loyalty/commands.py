"""`cafeops loyalty ...`: the operator's path into Sasha's Corner Rewards (CONTRACT §7).

Mounted by `cafeops/cli.py`. Shell access is already full trust, so these need no PIN:
adding the first manager has to be possible before any manager exists.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from cafeops.config import settings
from cafeops.db.base import session_scope
from cafeops.db.models import StaffRole
from cafeops.services.loyalty.admin import members_page
from cafeops.services.loyalty.demo import seed_demo
from cafeops.services.loyalty.errors import LoyaltyError
from cafeops.services.loyalty.staff_auth import create_device, create_staff_user
from cafeops.services.loyalty.stats import daily_summary

__all__ = ["loyalty_app"]

loyalty_app = typer.Typer(
    help="Sasha's Corner Rewards: staff, devices, members.", no_args_is_help=True
)
console = Console(width=120)


def _fail(exc: LoyaltyError) -> None:
    console.print(f"[red]{exc.detail}[/red] [dim]({exc.code})[/dim]")
    raise typer.Exit(1)


@loyalty_app.command(name="staff-add")
def staff_add(
    name: Annotated[str, typer.Argument(help="Name shown on the scanner and in the history.")],
    pin: Annotated[str, typer.Option("--pin", help="4-6 digits, unique among active staff.")],
    role: Annotated[str, typer.Option("--role", help="staff | manager | owner")] = "staff",
    telegram_id: Annotated[int | None, typer.Option("--telegram-id")] = None,
) -> None:
    """Add a person who can log in to the scanner."""
    try:
        staff_role = StaffRole[role.strip().upper()]
    except KeyError:
        raise typer.BadParameter("role must be staff, manager or owner") from None
    try:
        with session_scope() as session:
            user = create_staff_user(
                session, name=name, role=staff_role, pin=pin, telegram_id=telegram_id
            )
            console.print(
                f"[green]Added[/green] {user.name} ({staff_role.value.lower()}), id {user.id}"
            )
    except LoyaltyError as exc:
        _fail(exc)


@loyalty_app.command(name="device-pair")
def device_pair(
    name: Annotated[str, typer.Argument(help="e.g. 'Till tablet'")],
) -> None:
    """Create a device slot and print its 6-digit pairing code (valid 15 minutes)."""
    try:
        with session_scope() as session:
            new = create_device(session, name=name)
            expires = new.expires_at.astimezone(settings.tz).strftime("%H:%M")
            console.print(
                f"Pairing code [bold]{new.pairing_code}[/bold] for device {new.device_id} "
                f"({name}); enter it on /staff before {expires}."
            )
    except LoyaltyError as exc:
        _fail(exc)


@loyalty_app.command(name="members")
def members(
    q: Annotated[str | None, typer.Option("--q", help="Search name, email or phone.")] = None,
    limit: Annotated[int, typer.Option("--limit")] = 50,
) -> None:
    """List members, most recently active first."""
    try:
        with session_scope() as session:
            page = members_page(session, q=q, limit=limit)
    except LoyaltyError as exc:
        _fail(exc)
        return
    table = Table(title=f"{page.total} member(s)")
    for col in ("id", "name", "contact", "stamps", "cards done", "reward", "opt-in", "last active"):
        table.add_column(col)
    for row in page.members:
        table.add_row(
            str(row.member_id),
            row.first_name,
            row.email or row.phone or "",
            f"{row.stamps_current}/{row.stamps_required}",
            str(row.cycles_completed),
            "yes" if row.reward_available else "",
            "yes" if row.marketing_opt_in else "",
            row.last_activity_at.astimezone(settings.tz).strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)


@loyalty_app.command(name="seed-demo")
def seed_demo_cmd(
    programmes: Annotated[
        bool,
        typer.Option(
            "--programmes",
            help="Phase 3: also a Matcha club, a Cake points card, a reward catalogue on "
            "the main card; demo members dated 60 days back. Development only.",
        ),
    ] = False,
) -> None:
    """The programme (if missing) and 3 fake members. For development only."""
    try:
        with session_scope() as session:
            result = seed_demo(session, programmes=programmes)
    except LoyaltyError as exc:
        _fail(exc)
        return
    if result.program_created:
        console.print("Created the 'stamp' programme.")
    for made in result.programmes_created:
        console.print(f"Created {made}.")
    if not result.members_created:
        console.print("Demo members already exist; nothing added.")
    base = settings.loyalty_public_url.rstrip("/")
    for first_name, card_id, token in result.cards:
        console.print(f"{first_name}: {base}/c/{card_id}#t={token}")


@loyalty_app.command(name="summary")
def summary(
    day: Annotated[str | None, typer.Option("--day", help="YYYY-MM-DD, default today.")] = None,
) -> None:
    """One day's numbers: what the 19:30 Telegram message says."""
    when = date.fromisoformat(day) if day else None
    with session_scope() as session:
        s = daily_summary(session, day=when)
    console.print(
        f"{s.day.isoformat()}: {s.new_members} new member(s) ({s.members_total} total), "
        f"{s.visits} visit(s), {s.stamps} stamp(s), {s.rewards_issued} reward(s) issued, "
        f"{s.redemptions} redeemed, {s.alerts} alert(s)"
    )


@loyalty_app.command(name="pos-sync")
def pos_sync_cmd(
    days: Annotated[int, typer.Option("--days", help="Receipts closed in the last N days.")] = 14,
) -> None:
    """Link till customers to members and (if CAFEOPS_LOYALTY_AUTO_STAMP) reconcile
    auto-stamps from sales already ingested. Idempotent; `cafeops sync` does this too."""
    from datetime import timedelta

    from cafeops.services.loyalty.common import now_utc
    from cafeops.services.loyalty.pos import PosReport, auto_link, reconcile

    now = now_utc()
    with session_scope() as session:
        report = PosReport()
        report.links = auto_link(session, now=now)
        reconcile(session, since=now - timedelta(days=days), now=now, report=report)
    for line in report.lines():
        console.print(line)
