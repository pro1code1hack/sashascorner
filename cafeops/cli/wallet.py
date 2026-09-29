"""`cafeops wallet ...` -- set up, check and preview the wallet passes.

A Typer group mounted by `cafeops/cli/__init__.py` (`app.add_typer(wallet_app, name="wallet")`).
The owner never runs these; whoever sets up the Apple and Google accounts does, with
docs/loyalty/WALLET-SETUP.md open beside them.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from cafeops.cli._common import console
from cafeops.integrations.wallet.config import WalletNotConfigured, wallet_settings

if TYPE_CHECKING:
    from cafeops.services.loyalty.card_view import CardView

wallet_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Apple / Google Wallet passes for Sasha's Corner Rewards.",
)

CHECKLIST = """\
Accounts (docs/loyalty/WALLET-SETUP.md has the detail):
  Apple
    [ ] D-U-N-S number for the company (free, ~5 working days)
    [ ] Apple Developer Program, enrolled as an ORGANISATION ($99/yr)
    [ ] Pass Type ID registered (e.g. pass.uk.co.sashascorner.rewards)
    [ ] Pass Type ID certificate created from a CSR, exported to cert + key PEM
    [ ] Apple WWDR G4 intermediate downloaded
  Google
    [ ] Google Pay & Wallet Console: issuer account created, business profile done
    [ ] Google Wallet API enabled in a Google Cloud project
    [ ] Service account created, JSON key downloaded, added as a user on the issuer
    [ ] `cafeops wallet google-class-sync`, then request publishing (class review)"""


def _cert_line(label: str, path: Path | None) -> None:
    if path is None or not path.is_file():
        return
    from cryptography import x509

    data = path.read_bytes()
    try:
        cert = (
            x509.load_pem_x509_certificate(data)
            if b"-----BEGIN" in data
            else x509.load_der_x509_certificate(data)
        )
    except ValueError as exc:
        console.print(f"  [red]{label}: unreadable ({exc})[/red]")
        return
    expires = cert.not_valid_after_utc
    days = (expires - datetime.now(UTC)).days
    colour = "red" if days < 0 else "yellow" if days < 30 else "green"
    console.print(
        f"  {label}: {cert.subject.rfc4514_string()[:80]}\n"
        f"    expires [{colour}]{expires:%Y-%m-%d} ({days} days)[/{colour}]"
    )


def _outbox_line() -> None:
    """Pending and failed pushes: the first place to look when "my pass didn't update"."""
    from sqlalchemy import func, select
    from sqlalchemy.exc import SQLAlchemyError

    from cafeops.db.base import session_scope
    from cafeops.db.models.loyalty import WalletPushOutbox
    from cafeops.integrations.wallet.sync import BACKOFF

    try:
        with session_scope() as session:
            pending = session.scalar(select(func.count()).where(WalletPushOutbox.done_at.is_(None)))
            retrying = session.scalar(
                select(func.count()).where(
                    WalletPushOutbox.done_at.is_(None), WalletPushOutbox.attempts > 0
                )
            )
            last_error = session.scalars(
                select(WalletPushOutbox.last_error)
                # Still retrying, or given up; a row that later succeeded carries only
                # a note ("google not configured"), not a failure.
                .where(
                    WalletPushOutbox.attempts > 0,
                    (WalletPushOutbox.done_at.is_(None))
                    | (WalletPushOutbox.attempts > len(BACKOFF)),
                )
                .order_by(WalletPushOutbox.id.desc())
                .limit(1)
            ).first()
    except SQLAlchemyError:
        console.print("\noutbox: table not there yet (run the migrations)")
        return
    console.print(f"\noutbox: {pending} pending, {retrying} retrying")
    if last_error:
        console.print(f"  latest failure: {last_error}")


@wallet_app.command()
def doctor(
    online: Annotated[
        bool, typer.Option("--online", help="Also fetch a Google token and read the class.")
    ] = False,
) -> None:
    """What is configured, what is missing, and when the certificates expire."""
    cfg = wallet_settings
    console.print(f"public URL: {cfg.base_url}")
    console.print(f"  webServiceURL: {cfg.apple_web_service_url()}")

    console.print("\n[bold]Apple Wallet[/bold]")
    if cfg.apple_configured:
        console.print("  [green]configured[/green]")
        console.print(f"  pass type: {cfg.apple_pass_type_id}  team: {cfg.apple_team_id}")
        console.print(f"  APNs: {'sandbox' if cfg.apple_apns_use_sandbox else 'production'}")
        try:
            from cafeops.integrations.wallet.apple import signing_material

            material = signing_material(cfg)
            uid = material.cert.subject.rfc4514_string()
            if cfg.apple_pass_type_id and cfg.apple_pass_type_id not in uid:
                console.print(
                    "  [red]certificate is not for this Pass Type ID -- Wallet will reject "
                    "every pass[/red]"
                )
            if cfg.apple_team_id and f"OU={cfg.apple_team_id}" not in uid:
                console.print(
                    "  [red]certificate's team (OU) is not CAFEOPS_WALLET_APPLE_TEAM_ID[/red]"
                )
        except (WalletNotConfigured, ValueError, TypeError) as exc:
            console.print(f"  [red]signing material does not load: {exc}[/red]")
    else:
        console.print("  [yellow]not configured[/yellow] -- missing:")
        for item in cfg.apple_missing():
            console.print(f"    {item}")
    _cert_line("pass certificate", cfg.apple_cert_path)
    _cert_line("WWDR intermediate", cfg.apple_wwdr_path)

    console.print("\n[bold]Google Wallet[/bold]")
    if cfg.google_configured:
        from cafeops.integrations.wallet import google

        sa = google.service_account(cfg)
        console.print("  [green]configured[/green]")
        console.print(f"  issuer: {cfg.google_issuer_id}  class: {cfg.google_class_id}")
        console.print(f"  service account: {sa.client_email}")
        if online:
            try:
                google.access_token(cfg)
                console.print("  token: [green]ok[/green]")
                resp = google._request("GET", f"/loyaltyClass/{cfg.google_class_id}")
                if resp.status_code == 200:
                    console.print(f"  class reviewStatus: {resp.json().get('reviewStatus')}")
                else:
                    console.print(f"  class: HTTP {resp.status_code} (run google-class-sync)")
            except google.GoogleWalletError as exc:
                console.print(f"  [red]{exc}[/red]")
    else:
        console.print("  [yellow]not configured[/yellow] -- missing:")
        for item in cfg.google_missing():
            console.print(f"    {item}")

    from cafeops.integrations.wallet.assets import APPLE_FILES
    from cafeops.integrations.wallet.config import ASSETS_DIR

    absent = [n for n in APPLE_FILES if not (ASSETS_DIR / n).is_file()]
    if absent:
        console.print(f"\n[red]pass artwork missing in {ASSETS_DIR}: {', '.join(absent)}[/red]")
        console.print("  run `cafeops wallet assets`")
    from cafeops.integrations.wallet.stickers import stale_copies

    stale = stale_copies()
    if stale:
        console.print("\n[red]sticker copies differ from assets/pass/stickers/:[/red]")
        for path in stale:
            console.print(f"    {path}")
        console.print("  run `cafeops wallet assets` (the web card would show other art)")

    _outbox_line()

    if not (cfg.apple_configured and cfg.google_configured):
        console.print("\n" + CHECKLIST)


def _demo_view(stamps: int) -> CardView:
    from cafeops.services.loyalty.card_view import CardView, RewardView

    now = datetime.now(UTC)
    rewards: tuple[RewardView, ...] = ()
    if stamps >= 8:
        rewards = (
            RewardView(
                id=1, kind="STAMP_CARD", label="Free drink ready", issued_at=now, expires_at=None
            ),
        )
        stamps = 0
    return CardView(
        card_id="00000000-0000-4000-8000-000000000000",
        first_name="Sasha",
        member_since=date(2026, 9, 28),
        program_name="Sasha's Corner Rewards",
        stamps_required=8,
        stamps_current=stamps,
        reward_text="Any drink, on us",
        rewards=rewards,
        qr_payload="SC1:00000000-0000-4000-8000-000000000000:deadbeef",
        auth_token="demo-token-demo-token-demo-token",
        updated_at=now,
        voided=False,
        marketing_opt_in=False,
    )


@wallet_app.command()
def preview(
    card_id: Annotated[str | None, typer.Argument(help="Card id (the pass serial).")] = None,
    out: Annotated[Path, typer.Option("--out", help="Directory to write into.")] = Path(
        "pass-preview"
    ),
    demo: Annotated[
        int | None,
        typer.Option("--demo", help="No database: a fake card with this many stamps (8 = reward)."),
    ] = None,
) -> None:
    """Write the unsigned pass folder (pass.json, images, manifest) and, when Apple is
    configured, the signed .pkpass beside it."""
    from cafeops.integrations.wallet.apple import build_pkpass, write_unsigned_preview

    if demo is not None:
        view = _demo_view(demo)
        message = None
    elif card_id:
        from cafeops.db.base import session_scope
        from cafeops.integrations.wallet.apple_webservice import latest_message
        from cafeops.services.loyalty.card_view import card_view

        with session_scope() as session:
            view = card_view(session, card_id)
            message = latest_message(session, card_id)
    else:
        raise typer.BadParameter("give a CARD_ID or --demo N")
    folder = out / f"{view.card_id}.pass"
    for path in write_unsigned_preview(view, folder, latest_message=message):
        console.print(f"  {path}")
    try:
        pkpass = out / f"{view.card_id}.pkpass"
        pkpass.write_bytes(build_pkpass(view, latest_message=message))
        console.print(f"signed: {pkpass}")
    except WalletNotConfigured as exc:
        console.print(f"[yellow]not signed: {exc}[/yellow]")
    if wallet_settings.google_configured:
        from cafeops.integrations.wallet.google import save_url

        console.print(f"google save link: {save_url(view)[:80]}...")


@wallet_app.command()
def strips(
    out: Annotated[Path, typer.Option("--out", help="Directory to write into.")] = Path("strips"),
    required: Annotated[int, typer.Option(help="Stamps on a full card.")] = 8,
) -> None:
    """Render every strip (0..required stamps at @1x/@2x/@3x, and each with the reward
    sticker as `-reward`) for a look."""
    from cafeops.integrations.wallet.strips import SCALES, strip_png, strip_version

    out.mkdir(parents=True, exist_ok=True)
    count = 0
    for n in range(required + 1):
        for reward in (False, True):
            for scale in SCALES:
                suffix = ("-reward" if reward else "") + ("" if scale == 1 else f"@{scale}x")
                data = strip_png(n, required, scale, reward)
                (out / f"strip-{n}-{required}{suffix}.png").write_bytes(data)
                count += 1
    console.print(f"{count} strips in {out} (art version {strip_version()})")


@wallet_app.command()
def assets(
    out: Annotated[Path | None, typer.Option("--out", help="Default: assets/pass/.")] = None,
) -> None:
    """Regenerate the committed pass artwork (icon, logo, Google logo, stamp SVG) from
    the brand line-art in site/web/public/brand/, check the stamp stickers and copy them
    from assets/pass/stickers/ (canonical) to site/web/public/stickers/ and
    web/public/stickers/."""
    from cafeops.integrations.wallet.assets import generate
    from cafeops.integrations.wallet.config import ASSETS_DIR

    for path in generate(out or ASSETS_DIR):
        console.print(f"  {path}")


@wallet_app.command("google-class-sync")
def google_class_sync() -> None:
    """Create or update the Google LoyaltyClass. Run once, then after changing the
    programme's name, logo or rules (an approved class may go back to review)."""
    from cafeops.integrations.wallet import google

    try:
        action = google.upsert_class()
    except WalletNotConfigured as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    except google.GoogleWalletError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"class {wallet_settings.google_class_id} {action}")
    console.print(
        "Next: in the Google Pay & Wallet Console, open the class and request publishing. "
        "Until it is approved only the issuer's test accounts can save the pass."
    )


__all__ = ["wallet_app"]
