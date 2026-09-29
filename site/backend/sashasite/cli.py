"""sashasite CLI."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from pathlib import Path

import typer
from sqlalchemy import func, select, text

from sashasite.config import BACKEND_ROOT, get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _write_json(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload + "\n", encoding="utf-8")
    typer.echo(f"wrote {path.resolve()}")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8100, reload: bool = False) -> None:
    """Run the API with uvicorn."""
    import uvicorn

    uvicorn.run(
        "sashasite.app:app",
        host=host,
        port=port,
        reload=reload,
        proxy_headers=get_settings().trust_proxy,
    )


@app.command()
def bookings(
    date: str | None = typer.Option(None, help="YYYY-MM-DD; default: all upcoming"),
) -> None:
    """Print bookings as a table."""
    from sashasite.booking import list_bookings, local_tz

    if date:
        day = dt.date.fromisoformat(date)
        rows = list_bookings(day, day)
    else:
        rows = list_bookings(dt.datetime.now(local_tz()).date(), None)
    if not rows:
        typer.echo("no bookings")
        return
    typer.echo(
        f"{'date':<10}  {'time':<5}  {'ref':<7}  {'pax':>3}  {'status':<9}  "
        f"{'name':<24}  {'phone':<16}  notes"
    )
    for b in rows:
        typer.echo(
            f"{b.local_date.isoformat():<10}  {b.local_time:<5}  {b.reference:<7}  "
            f"{b.party:>3}  {b.status:<9}  {b.name[:24]:<24}  {(b.phone or ''):<16}  "
            f"{(b.notes or '').replace(chr(10), ' ')[:60]}"
        )


@app.command("menu-export")
def menu_export(out: Path = typer.Option(..., help="Where to write the /api/menu JSON")) -> None:
    """Write the public menu JSON (the Astro build renders it as crawlable HTML).
    Same source rules as /api/menu: ops when it has categories, else the board."""
    from sashasite.menu_source import build_menu

    menu = build_menu()
    _write_json(out, menu.model_dump_json(indent=2))
    n = sum(len(c.items) for c in menu.categories)
    typer.echo(f"  source {menu.source}, version {menu.version}, {n} items")
    for c in menu.categories:
        typer.echo(f"  {c.slug:<14} {len(c.items):>3} items")
    typer.echo(f"  extras: {len(menu.extras.items)}")


@app.command("menu-meta-seed")
def menu_meta_seed() -> None:
    """LEGACY. Copy the board's blurbs, descriptions and signature flags into the
    site's own menu overlay. Refused once the ops shop tables exist: the website
    menu is edited in the back office's Menu items then (`cafeops shop adopt-website-menu`
    carries the old rows over). Idempotent: names with a row are left alone."""
    from sashasite.menu_source import seed_overlay

    try:
        res = seed_overlay()
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"categories added: {res.categories_added}; items added: {res.items_added}, "
        f"already set: {res.items_existing}"
    )
    for board_name, ops_name in res.mapped_to_ops:
        typer.echo(f"  also under the ops name: {board_name} -> {ops_name}")


@app.command("info-export")
def info_export(out: Path = typer.Option(..., help="Where to write the /api/info JSON")) -> None:
    """Write the café info JSON (from the DB settings, as /api/info serves it)."""
    from sashasite.app import info_out
    from sashasite.settings_store import live_cafe

    _write_json(out, info_out(live_cafe()).model_dump_json(indent=2))


@app.command("media-add")
def media_add(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    alt: str = typer.Option("", "--alt", help="Describe the photo for screen readers"),
    slot: str | None = typer.Option(
        None, "--slot", help="Also place it: appends to a multiple slot, replaces a single one"
    ),
) -> None:
    """Add a photo to the library (same processing as the admin upload)."""
    from sashasite import media as md
    from sashasite import slots as sl
    from sashasite.schemas import Focal, SlotItemIn

    try:
        m, created = md.add_media(file.read_bytes(), file.name, alt)
    except md.MediaError as exc:
        typer.echo(f"refused ({exc.status}): {exc}", err=True)
        raise typer.Exit(1) from exc
    state = "added" if created else "already in the library as"
    typer.echo(f"{state} #{m.id}: {m.width}x{m.height}, variants {m.widths}, alt {m.alt!r}")
    if created is False and alt and alt != m.alt:
        typer.echo("  (alt left unchanged on the existing photo; edit it in the admin)")

    if slot is None:
        return
    d = sl.registry().get(slot)
    if d is None:
        typer.echo(f"unknown slot {slot!r}; see config/slots.toml", err=True)
        raise typer.Exit(1)
    items = sl.current_items(slot)
    new = SlotItemIn(media_id=m.id, focal=Focal())
    if not d.multiple:
        items = [new]
    elif any(i.media_id == m.id for i in items):
        typer.echo(f"  already in {slot}")
        return
    elif len(items) >= d.max:
        typer.echo(f"slot {slot!r} is full ({d.max}); remove one in the admin first", err=True)
        raise typer.Exit(1)
    else:
        items.append(new)
    try:
        out = sl.replace_slot(slot, items)
    except sl.SlotError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"  {slot}: {len(out.items)}/{d.max} photo(s)")


@app.command("media-list")
def media_list() -> None:
    """Print the photo library and where each photo is used."""
    from sashasite import media as md
    from sashasite import slots as sl

    rows = md.list_media()
    if not rows:
        typer.echo("no photos")
        return
    known = sl.registry().keys
    typer.echo(f"{'id':>4}  {'size':>9}  {'kB':>6}  {'variants':<13}  {'alt':<40}  used in")
    for m, uses in rows:
        where = ", ".join(
            f"{u.slot_key}[{u.position}]" + ("" if u.slot_key in known else "(orphan)")
            for u in uses
        )
        typer.echo(
            f"{m.id:>4}  {f'{m.width}x{m.height}':>9}  {m.bytes // 1024:>6}  {m.widths:<13}  "
            f"{(m.alt or '(no alt text)')[:40]:<40}  {where or '-'}"
        )


@app.command("slots-export")
def slots_export(
    out: Path = typer.Option(..., help="Where to write the /api/slots JSON"),
) -> None:
    """Write the /api/slots JSON, so the static build carries the photos too."""
    from sashasite.slots import build_slots

    slots = build_slots()
    _write_json(out, slots.model_dump_json(indent=2))
    for key, s in slots.slots.items():
        typer.echo(f"  {key:<28} {len(s.items)}/{s.max}")


from sashasite.events_cli import register as _register_events  # noqa: E402

_register_events(app)  # events, events-export, events-seed-demo


@app.command()
def doctor(
    full: bool = typer.Option(False, "--full", help="Print every drift row, not the first 15"),
) -> None:
    """Is this install operable? Exit 1 on any hard failure."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from sashasite.db import get_engine
    from sashasite.drift import format_report, menu_drift
    from sashasite.menu import load_board

    failed = False

    def line(ok: bool, msg: str, hard: bool = True) -> None:
        nonlocal failed
        mark = "ok  " if ok else ("FAIL" if hard else "warn")
        typer.echo(f"[{mark}] {msg}")
        if not ok and hard:
            failed = True

    s = get_settings()
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
            count_sql = text("SELECT COUNT(*) FROM menu_item WHERE active = 1")
            n: int = conn.execute(count_sql).scalar_one()
        line(True, f"database reachable ({s.database_url}); {n} active menu_item rows")
    except Exception as exc:
        line(False, f"database: {type(exc).__name__}: {exc}")
        raise typer.Exit(1) from exc

    head = ScriptDirectory.from_config(Config(str(BACKEND_ROOT / "alembic.ini"))).get_current_head()
    try:
        with get_engine().connect() as conn:
            current = conn.execute(text("SELECT version_num FROM site_alembic_version")).scalar()
    except Exception:
        current = None
    line(
        current == head,
        f"migrations: at {current or 'nothing'}, head is {head}"
        + ("" if current == head else " -- run `uv run alembic upgrade head`"),
    )

    try:
        board = load_board()
        line(True, f"menu_board.toml valid: {len(board.category)} categories")
    except Exception as exc:
        line(False, f"menu_board.toml invalid: {exc}")
        board = None

    if board is not None:
        _doctor_menu(line)

    # Drift is a report: it never changes the exit code.
    if board is not None:
        try:
            for row in format_report(menu_drift(board), limit=None if full else 15):
                typer.echo(row)
        except Exception as exc:
            typer.echo(f"[warn] menu drift report unavailable: {type(exc).__name__}: {exc}")

    _doctor_media(line)

    from sashasite.auth import password_source
    from sashasite.db import SiteAdminSession, SiteSetting, session_scope
    from sashasite.settings_store import live_cafe

    try:
        cafe = live_cafe()
        with session_scope() as session:
            stamps = session.scalars(select(SiteSetting.updated_at)).all()
            source = password_source(session)
            n_sessions = (
                session.scalar(
                    select(func.count())
                    .select_from(SiteAdminSession)
                    .where(SiteAdminSession.expires_at > dt.datetime.now(dt.UTC))
                )
                or 0
            )
        line(
            True,
            f"cafe settings in site_setting (last edit {max(stamps):%Y-%m-%d %H:%M} UTC); "
            f"{len(cafe.closures)} closure(s); {cafe.booking.covers_per_slot} covers per slot",
        )
    except Exception as exc:
        line(False, f"cafe settings unreadable: {type(exc).__name__}: {exc}")
        raise typer.Exit(1) from exc
    line(bool(cafe.email), f"cafe email: {cafe.email or '(empty -- no mailbox yet)'}", hard=False)
    line(
        cafe.confirmed,
        "cafe facts confirmed"
        if cafe.confirmed
        else f"cafe facts UNCONFIRMED placeholders: {', '.join(cafe.unconfirmed_fields)}",
        hard=False,
    )
    # One password (owner, 2026-09-28): the website admin is reached only through the
    # back office. Without SITE_SERVICE_KEY the site's own password is still a door.
    if s.service_key:
        line(
            True,
            "admin access: only through the back office (SITE_SERVICE_KEY set); the "
            f"site's own password and cookie sign-in are off. Admin: {s.ops_url}/#/website",
        )
        if source is not None or n_sessions:
            typer.echo(
                "[info] a leftover site password/sessions exist but are refused; "
                "`sashasite admin-sessions --revoke-all` tidies the sessions"
            )
    else:
        line(
            False,
            "SITE_SERVICE_KEY not set: the back office cannot reach the website admin, and "
            "the site's own password/cookie sign-in is still a separate way in "
            f"(password {'set' if source else 'NOT set'}; {n_sessions} active session(s)). "
            "Set SITE_SERVICE_KEY in .env to the same long random value for both apps.",
            hard=False,
        )
    line(
        s.telegram_configured,
        "telegram configured: "
        + ("yes" if s.telegram_configured else "no (notifications are skipped)"),
        hard=False,
    )
    if failed:
        raise typer.Exit(1)


def _doctor_menu(line: Callable[..., None]) -> None:
    """Which source the website menu is served from, what the public sees, and
    what the admin should look at (unassigned items, orphaned overlay rows)."""
    from sashasite.menu_source import build_snapshot, orphans, public_menu

    try:
        snap = build_snapshot()
        menu = public_menu(snap)
    except Exception as exc:
        line(False, f"website menu unbuildable: {type(exc).__name__}: {exc}")
        return
    counts = ", ".join(f"{c.slug}={len(c.items)}" for c in menu.categories)
    n = sum(len(c.items) for c in menu.categories)
    line(
        True,
        f"website menu source: {snap.source}, presentation from "
        f"{'the back office (shop tables)' if snap.overlay == 'shop' else snap.overlay} "
        f"(version {menu.version}); "
        f"{len(menu.categories)} categories, {n} public items ({counts})",
    )
    for w in snap.warnings:
        line(False, w, hard=False)
    if snap.unassigned:
        typer.echo(
            "  unassigned (no category, not on the website): "
            + ", ".join(i.name for i in snap.unassigned[:30])
            + (" ..." if len(snap.unassigned) > 30 else "")
        )
    if snap.overlay == "site":
        orph = orphans()
        line(
            not (orph.categories or orph.items),
            "legacy overlay rows for names no longer on any menu: "
            + (", ".join([*orph.categories, *orph.items]) or "none"),
            hard=False,
        )


def _doctor_media(line: Callable[..., None]) -> None:
    """Slots and photos: empty slots and missing alt text warn; a broken
    slots.toml, an orphaned assignment or a missing variant file fail."""
    from sqlalchemy import select

    from sashasite import media as md
    from sashasite import slots as sl
    from sashasite.db import SiteMedia, SiteSlotItem, session_scope

    try:
        reg = sl.registry()
    except Exception as exc:
        line(False, f"slots.toml invalid: {exc}")
        return
    try:
        with session_scope() as session:
            items = session.scalars(
                select(SiteSlotItem).order_by(SiteSlotItem.slot_key, SiteSlotItem.position)
            ).all()
            no_alt = session.scalars(select(SiteMedia.id).where(SiteMedia.alt == "")).all()
            n_media = session.scalar(select(func.count()).select_from(SiteMedia)) or 0
    except Exception as exc:
        line(False, f"media tables unreadable ({type(exc).__name__}); run alembic upgrade head")
        return

    counts: dict[str, int] = {}
    for it in items:
        counts[it.slot_key] = counts.get(it.slot_key, 0) + 1
    empty = [d.key for d in reg.slot if not counts.get(d.key)]
    filled = len(reg.slot) - len(empty)
    line(
        True,
        f"slots.toml valid: {len(reg.slot)} slot(s), {filled} filled; "
        f"{n_media} photo(s) in {get_settings().media_dir}",
    )
    line(not empty, f"empty slots ({len(empty)}): {', '.join(empty) or 'none'}", hard=False)
    over = [f"{d.key} {counts[d.key]}>{d.max}" for d in reg.slot if counts.get(d.key, 0) > d.max]
    line(not over, f"slots over their max (extra not shown): {', '.join(over) or 'none'}", False)
    orphans = sorted({k for k in counts if k not in reg.keys})
    line(
        not orphans,
        "orphaned slot assignments (key no longer in slots.toml, kept but not served): "
        + (", ".join(f"{k} ({counts[k]})" for k in orphans) or "none"),
        hard=False,
    )
    missing = md.missing_files()
    line(
        not missing,
        "missing variant files: " + (", ".join(f"{i}-{w}.webp" for i, w in missing[:20]) or "none"),
    )
    line(not no_alt, f"photos without alt text: {list(no_alt) or 'none'}", hard=False)


@app.command("admin-password")
def admin_password(
    clear: bool = typer.Option(
        False, "--clear", help="Remove the DB password: SITE_ADMIN_PASSWORD bootstraps again"
    ),
) -> None:
    """DEV ONLY: the site's own admin password, used only while SITE_SERVICE_KEY is
    unset. With the key set the back office's password is the one in force."""
    import getpass

    from sashasite import auth
    from sashasite.db import session_scope

    if not clear and not auth.cookie_login_enabled():
        typer.echo(
            "refused: SITE_SERVICE_KEY is set, so the website admin is reached only through "
            "the back office and uses its password. Change it in the back office's Settings "
            "(or `cafeops password reset`).",
            err=True,
        )
        raise typer.Exit(1)
    if clear:
        with session_scope(immediate=True) as session:
            had = auth.clear_password(session)
            if had:
                auth.audit("password.clear", ip="cli", session=session)
        typer.echo("DB password removed; all sessions signed out." if had else "no DB password")
        return
    new = getpass.getpass("New admin password: ")
    if len(new) < auth.MIN_PASSWORD_LENGTH:
        typer.echo(f"refused: at least {auth.MIN_PASSWORD_LENGTH} characters", err=True)
        raise typer.Exit(1)
    if getpass.getpass("Again: ") != new:
        typer.echo("refused: the two entries differ", err=True)
        raise typer.Exit(1)
    with session_scope(immediate=True) as session:
        revoked = auth.set_password(session, new)
        auth.audit("password.change", {"sessions_revoked": revoked}, ip="cli", session=session)
    typer.echo(f"admin password set in the DB; {revoked} session(s) signed out")


@app.command("admin-sessions")
def admin_sessions(
    revoke_all: bool = typer.Option(False, "--revoke-all", help="Sign every browser out"),
) -> None:
    """List admin sessions, or revoke them all."""
    from sashasite import auth
    from sashasite.db import SiteAdminSession, session_scope

    if revoke_all:
        with session_scope(immediate=True) as session:
            n = auth.revoke_all_sessions(session)
            auth.audit("sessions.revoke_all", {"revoked": n}, ip="cli", session=session)
        typer.echo(f"revoked {n} session(s)")
        return
    with session_scope() as session:
        rows = list(
            session.scalars(select(SiteAdminSession).order_by(SiteAdminSession.last_used_at))
        )
    if not rows:
        typer.echo("no sessions")
        return
    typer.echo(f"{'id':>4}  {'created':<16}  {'last used':<16}  {'expires':<16}  {'ip':<15}  agent")
    for r in rows:
        typer.echo(
            f"{r.id:>4}  {r.created_at:%Y-%m-%d %H:%M}  {r.last_used_at:%Y-%m-%d %H:%M}  "
            f"{r.expires_at:%Y-%m-%d %H:%M}  {(r.ip or ''):<15}  {(r.user_agent or '')[:50]}"
        )


@app.callback()
def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("alembic").setLevel(logging.WARNING)


if __name__ == "__main__":
    app()
