"""`cafeops shop ...`: the operator's commands for Order online."""

from __future__ import annotations

import typer
from rich.console import Console

from cafeops.db.base import session_scope
from cafeops.services.shop.push import generate_vapid_keys
from cafeops.services.shop.website_menu import adopt_website_menu

__all__ = ["shop_app"]

shop_app = typer.Typer(help="Order online (click & collect).", no_args_is_help=True)
console = Console()


@shop_app.command("vapid-keys")
def vapid_keys() -> None:
    """Print a fresh VAPID key pair for Web Push (docs/shop/CONTRACT.md 3c).

    Put both in .env and never rotate them: every browser that subscribed to an order's
    status did so against the public key, and a new pair silently orphans them all.
    """
    public, private = generate_vapid_keys()
    console.print("[bold]Add to .env (once; never change):[/bold]")
    console.print(f"CAFEOPS_VAPID_PUBLIC_KEY={public}")
    console.print(f"CAFEOPS_VAPID_PRIVATE_KEY={private}")
    console.print("CAFEOPS_VAPID_SUBJECT=mailto:hello@sashascorner.co.uk")


@shop_app.command("adopt-website-menu")
def adopt_website_menu_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="Report only; write nothing."),
) -> None:
    """Carry the website's old menu copy into the shop catalogue (DECISIONS.md 29).

    The public menu page now reads `shop_product` / `shop_category` for descriptions,
    signature marks, visibility and category blurbs. This copies what the site's own
    `site_menu_*_meta` rows held into shop fields that are still empty -- once, and never
    over something set in the back office. Safe to re-run; the site tables are untouched.
    """
    with session_scope() as session:
        rep = adopt_website_menu(session, dry_run=dry_run)
    if not rep.site_tables_present:
        console.print("no site_menu_* tables in this database: nothing to carry over")
        return
    verb = "would copy" if dry_run else "copied"
    console.print(
        f"{verb} from {rep.site_items} site item row(s) and {rep.site_categories} category "
        f"row(s): {len(rep.descriptions)} description(s), {len(rep.featured)} signature -> "
        f"featured, {len(rep.hidden)} hidden product(s), {len(rep.blurbs)} blurb(s), "
        f"{len(rep.hidden_categories)} hidden categor(y/ies)"
    )
    if rep.unmatched_items:
        console.print(
            f"  {len(rep.unmatched_items)} site item row(s) name no shop product (board-only "
            f"or retired names), e.g. {', '.join(rep.unmatched_items[:5])}"
        )
    if rep.unmatched_categories:
        console.print(f"  no shop category for: {', '.join(rep.unmatched_categories)}")
