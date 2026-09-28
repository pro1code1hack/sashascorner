"""CLI for events: ``events``, ``events-export``, ``events-seed-demo``.

Mounted by ``sashasite.cli`` through ``register(app)``.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import typer
from sqlalchemy import delete, select

#: Demo rows are recognisable by this slug prefix, so ``--remove`` deletes only them.
DEMO_PREFIX = "demo-"

#: Ideas from the owner's spec, NOT scheduled events. Dev only.
DEMO = [
    (
        "Matcha workshop (demo)",
        12,
        "18:00",
        "19:30",
        1500,
        10,
        "Demo event for development. Not a real booking.",
    ),
    (
        "Chess night (demo)",
        19,
        "17:00",
        "19:00",
        None,
        16,
        "Demo event for development. Not a real booking.",
    ),
    (
        "Tasting evening (demo)",
        26,
        "17:30",
        "19:00",
        800,
        2,
        "Demo event for development. Capacity 2, to try the 'full' state.",
    ),
]


def register(app: typer.Typer) -> None:
    @app.command("events")
    def events_list() -> None:
        """Print every event with its RSVP count."""
        from sashasite import events as ev
        from sashasite.db import session_scope

        with session_scope() as session:
            rows = list(session.scalars(select(ev.SiteEvent).order_by(ev.SiteEvent.starts_at)))
            if not rows:
                typer.echo("no events")
                return
            for e in rows:
                day, start = ev.local_parts(e.starts_at)
                cap = (
                    f"{ev.seats_taken(session, e.id)}/{e.capacity}"
                    if e.capacity
                    else (f"{ev.seats_taken(session, e.id)}/-")
                )
                flag = "published" if e.published else "draft"
                typer.echo(f"{day} {start}  {flag:<9}  {cap:>7}  {e.slug}  {e.title}")

    @app.command("events-export")
    def events_export(
        out: Path = typer.Option(..., help="Where to write the /api/events JSON"),
    ) -> None:
        """Write the /api/events JSON (published, upcoming) for the static build."""
        from sashasite.events_api import build_events

        data = build_events()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(data.model_dump_json(indent=2) + "\n", encoding="utf-8")
        typer.echo(f"wrote {out.resolve()}")
        typer.echo(f"  {len(data.events)} upcoming event(s)")
        for e in data.events:
            typer.echo(f"  {e.date} {e.start}  {e.slug}")

    @app.command("events-seed-demo")
    def events_seed_demo(
        yes: bool = typer.Option(False, "--yes", help="Don't ask; write to the configured DB"),
        remove: bool = typer.Option(False, "--remove", help="Delete the demo events instead"),
    ) -> None:
        """DEV ONLY: add three published demo events (slugs start with 'demo-').

        They are ideas from the spec, not scheduled events: never run this against
        the production database."""
        from sashasite import events as ev
        from sashasite.config import get_settings
        from sashasite.db import session_scope, utcnow

        url = get_settings().database_url
        if not yes and not typer.confirm(f"Write demo events to {url}?"):
            raise typer.Exit(1)
        with session_scope(immediate=True) as session:
            ids = list(
                session.scalars(
                    select(ev.SiteEvent.id).where(ev.SiteEvent.slug.like(f"{DEMO_PREFIX}%"))
                )
            )
            if ids:
                session.execute(delete(ev.SiteEventRsvp).where(ev.SiteEventRsvp.event_id.in_(ids)))
                session.execute(delete(ev.SiteEvent).where(ev.SiteEvent.id.in_(ids)))
            if remove:
                typer.echo(f"removed {len(ids)} demo event(s)")
                return
            today = dt.datetime.now(ev.local_tz()).date()
            now = utcnow()
            for title, days, start, end, price, cap, desc in DEMO:
                day = today + dt.timedelta(days=days)
                session.add(
                    ev.SiteEvent(
                        slug=f"{DEMO_PREFIX}{ev.slugify(title)}",
                        title=title,
                        starts_at=ev.to_utc(day, start),
                        ends_at=ev.to_utc(day, end),
                        description=desc,
                        price_pence=price,
                        capacity=cap,
                        published=True,
                        created_at=now,
                        updated_at=now,
                    )
                )
        typer.echo(f"added {len(DEMO)} demo event(s) to {url} (remove: --remove)")
