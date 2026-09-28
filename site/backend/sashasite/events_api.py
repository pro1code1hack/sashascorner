"""HTTP for events: the public list and RSVP, and the owner's admin.

Public (no auth):
  GET  /api/events                    published events that have not ended, soonest first
  POST /api/events/{slug}/rsvp        201 | 404 no such upcoming event | 409 full | 422 | 429

Admin (session cookie; ``X-Admin: 1`` on writes; every write audited):
  GET    /api/admin/events                       every event, newest first, with counts
  POST   /api/admin/events                       201 | 409 slug taken | 422
  PATCH  /api/admin/events/{id}                  partial update
  DELETE /api/admin/events/{id}[?force=1]        409 while it has RSVPs, unless force
  GET    /api/admin/events/{id}/rsvps            every RSVP, cancelled ones too
  PATCH  /api/admin/events/{id}/rsvps/{rsvp_id}  {cancelled: bool}
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Self

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from sashasite import events as ev
from sashasite.auth import audit, require_admin_session
from sashasite.db import SiteMedia, session_scope, utcnow
from sashasite.media import media_urls
from sashasite.notify import ru_date, ru_guests, send_owner
from sashasite.ratelimit import rate_limit

log = logging.getLogger("sashasite.events")

HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"
SLUG = r"^[a-z0-9]+(-[a-z0-9]+)*$"
PHONE = re.compile(r"^\+?[\d\s()-]{7,20}$")


def _strip_or_none(v: object) -> object:
    if isinstance(v, str):
        return v.strip() or None
    return v


# --- shapes ------------------------------------------------------------------------


class EventImageOut(BaseModel):
    media_id: int
    src: str
    srcset: str
    width: int
    height: int
    alt: str
    blur: str


class EventOut(BaseModel):
    slug: str
    title: str
    starts_at: dt.datetime
    ends_at: dt.datetime | None
    #: Café-local calendar date and wall-clock times (Europe/London).
    date: dt.date
    start: str
    end: str | None
    description: str
    price_pence: int | None
    capacity: int | None
    #: None when there is no capacity limit.
    places_left: int | None
    full: bool
    image: EventImageOut | None


class EventsOut(BaseModel):
    generated_at: dt.datetime
    max_party: int
    events: list[EventOut]


class RsvpIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=30)
    party: int = Field(ge=1, le=ev.MAX_PARTY)
    note: str | None = Field(default=None, max_length=500)
    website: str = ""  # honeypot: humans never see it, so it must stay empty

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v

    _blank = field_validator("email", "phone", "note", mode="before")(_strip_or_none)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        if v is not None and not PHONE.match(v):
            raise ValueError("Please enter a phone number, e.g. 07123 456789.")
        return v

    @model_validator(mode="after")
    def _contact(self) -> Self:
        if self.email is None and self.phone is None:
            raise ValueError("Please give an email address or a phone number.")
        return self


class RsvpOut(BaseModel):
    ok: bool = True
    slug: str
    party: int
    places_left: int | None


class AdminEventIn(BaseModel):
    #: Omit to derive one from the title and date.
    slug: str | None = Field(default=None, max_length=80, pattern=SLUG)
    title: str = Field(min_length=1, max_length=120)
    date: dt.date
    start: str = Field(pattern=HHMM)
    end: str | None = Field(default=None, pattern=HHMM)
    description: str = Field(default="", max_length=4000)
    price_pence: int | None = Field(default=None, ge=0, le=100_000)
    capacity: int | None = Field(default=None, ge=1, le=500)
    image_media_id: int | None = None
    published: bool = False

    _blank = field_validator("slug", "end", mode="before")(_strip_or_none)

    @field_validator("title", "description", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class AdminEventPatchIn(BaseModel):
    slug: str | None = Field(default=None, max_length=80, pattern=SLUG)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    date: dt.date | None = None
    start: str | None = Field(default=None, pattern=HHMM)
    #: Send null to clear the end time.
    end: str | None = Field(default=None, pattern=HHMM)
    description: str | None = Field(default=None, max_length=4000)
    price_pence: int | None = Field(default=None, ge=0, le=100_000)
    capacity: int | None = Field(default=None, ge=1, le=500)
    image_media_id: int | None = None
    published: bool | None = None

    _blank = field_validator("end", mode="before")(_strip_or_none)

    @field_validator("title", "description", "slug", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class AdminEventOut(BaseModel):
    id: int
    slug: str
    title: str
    date: dt.date
    start: str
    end: str | None
    starts_at: dt.datetime
    ends_at: dt.datetime | None
    description: str
    price_pence: int | None
    capacity: int | None
    image_media_id: int | None
    image: EventImageOut | None
    published: bool
    #: Ended (or started, for RSVPs: a started event takes no more).
    past: bool
    seats_taken: int
    rsvps: int
    created_at: dt.datetime
    updated_at: dt.datetime


class AdminRsvpOut(BaseModel):
    id: int
    name: str
    email: str | None
    phone: str | None
    party: int
    note: str | None
    created_at: dt.datetime
    cancelled_at: dt.datetime | None


class RsvpPatchIn(BaseModel):
    cancelled: bool


class DeletedOut(BaseModel):
    id: int
    rsvps_deleted: int


# --- output helpers ----------------------------------------------------------------


def _image(m: SiteMedia | None) -> EventImageOut | None:
    if m is None:
        return None
    src, srcset = media_urls(m)
    return EventImageOut(
        media_id=m.id,
        src=src,
        srcset=srcset,
        width=m.width,
        height=m.height,
        alt=m.alt,
        blur=m.blur,
    )


def event_out(session: Session, e: ev.SiteEvent) -> EventOut:
    day, start = ev.local_parts(e.starts_at)
    end = ev.local_parts(e.ends_at)[1] if e.ends_at else None
    left = None if e.capacity is None else max(e.capacity - ev.seats_taken(session, e.id), 0)
    return EventOut(
        slug=e.slug,
        title=e.title,
        starts_at=e.starts_at,
        ends_at=e.ends_at,
        date=day,
        start=start,
        end=end,
        description=e.description,
        price_pence=e.price_pence,
        capacity=e.capacity,
        places_left=left,
        full=left == 0,
        image=_image(ev.image_of(session, e)),
    )


def build_events() -> EventsOut:
    """The public list; also what ``sashasite events-export`` writes. Before the
    ``site_0006`` migration has run there are no events: an empty list, not a 500
    (and not a failed Docker build, which runs the export)."""
    try:
        with session_scope() as session:
            events = [event_out(session, e) for e in ev.upcoming(session)]
    except OperationalError as exc:
        if "no such table" not in str(exc):
            raise
        log.warning("site_event is missing: run `alembic upgrade head` (serving no events)")
        events = []
    return EventsOut(generated_at=utcnow(), max_party=ev.MAX_PARTY, events=events)


def admin_event_out(session: Session, e: ev.SiteEvent) -> AdminEventOut:
    day, start = ev.local_parts(e.starts_at)
    return AdminEventOut(
        id=e.id,
        slug=e.slug,
        title=e.title,
        date=day,
        start=start,
        end=ev.local_parts(e.ends_at)[1] if e.ends_at else None,
        starts_at=e.starts_at,
        ends_at=e.ends_at,
        description=e.description,
        price_pence=e.price_pence,
        capacity=e.capacity,
        image_media_id=e.image_media_id,
        image=_image(ev.image_of(session, e)),
        published=e.published,
        past=e.starts_at <= utcnow(),
        seats_taken=ev.seats_taken(session, e.id),
        rsvps=ev.rsvp_count(session, e.id),
        created_at=e.created_at,
        updated_at=e.updated_at,
    )


def rsvp_text(r: ev.RsvpResult) -> str:
    """Owner notice, in Russian like the rest of the bot."""
    day, start = ev.local_parts(r.starts_at)
    contact = ", ".join(x for x in (r.phone, r.email) if x)
    text = f"Запись на мероприятие «{r.title}» ({ru_date(day)}, {start}): {r.name}, "
    text += f"{ru_guests(r.party)} — {contact}"
    if r.note:
        text += f"; заметка: {r.note}"
    if r.places_left is not None:
        text += f"; осталось мест: {r.places_left}"
    return text


# --- public ------------------------------------------------------------------------

public_router = APIRouter(prefix="/api/events")


@public_router.get("", response_model=EventsOut)
def list_events(response: Response) -> EventsOut:
    response.headers["Cache-Control"] = "public, max-age=30"
    return build_events()


@public_router.post(
    "/{slug}/rsvp",
    status_code=201,
    response_model=RsvpOut,
    dependencies=[Depends(rate_limit)],
    responses={404: {"description": "No such upcoming event"}, 409: {"description": "Full"}},
)
def rsvp(slug: str, body: RsvpIn, tasks: BackgroundTasks) -> RsvpOut:
    if body.website:
        # Honeypot tripped: look successful, store nothing, tell nobody.
        log.info("event rsvp honeypot tripped; discarded")
        return RsvpOut(slug=slug, party=body.party, places_left=None)
    try:
        r = ev.create_rsvp(
            slug,
            name=body.name,
            email=str(body.email) if body.email else None,
            phone=body.phone,
            party=body.party,
            note=body.note,
        )
    except ev.EventNotFound as exc:
        raise HTTPException(404, detail="This event isn't taking replies any more.") from exc
    except ev.EventFull as exc:
        raise HTTPException(
            409, detail={"error": "full", "message": str(exc), "places_left": max(exc.left, 0)}
        ) from exc
    tasks.add_task(_notify_safely, rsvp_text(r))
    return RsvpOut(slug=r.slug, party=r.party, places_left=r.places_left)


def _notify_safely(text: str) -> None:
    try:
        send_owner(text)
    except Exception:
        log.exception("telegram notice failed")


# --- admin -------------------------------------------------------------------------

admin_router = APIRouter(prefix="/api/admin/events", dependencies=[Depends(require_admin_session)])


def _get(session: Session, event_id: int) -> ev.SiteEvent:
    e = session.get(ev.SiteEvent, event_id)
    if e is None:
        raise HTTPException(404, detail="No such event.")
    return e


def _check_media(session: Session, media_id: int | None) -> None:
    if media_id is not None and session.get(SiteMedia, media_id) is None:
        raise HTTPException(
            422,
            detail=[
                {
                    "loc": ["body", "image_media_id"],
                    "msg": "That photo is not in the library.",
                    "type": "value_error",
                }
            ],
        )


def _times(day: dt.date, start: str, end: str | None) -> tuple[dt.datetime, dt.datetime | None]:
    s = ev.to_utc(day, start)
    e = ev.to_utc(day, end) if end else None
    if e is not None and e <= s:
        raise HTTPException(
            422,
            detail=[
                {"loc": ["body", "end"], "msg": "Must be after the start.", "type": "value_error"}
            ],
        )
    return s, e


def _free_slug(session: Session, base: str) -> str:
    slug, n = base, 2
    while session.scalar(select(ev.SiteEvent.id).where(ev.SiteEvent.slug == slug)) is not None:
        slug = f"{base}-{n}"
        n += 1
    return slug


def _slug_taken() -> HTTPException:
    return HTTPException(
        409, detail="Another event already uses that web address (slug). Pick another."
    )


@admin_router.get("", response_model=list[AdminEventOut])
def admin_list() -> list[AdminEventOut]:
    with session_scope() as session:
        rows = session.scalars(
            select(ev.SiteEvent).order_by(ev.SiteEvent.starts_at.desc(), ev.SiteEvent.id.desc())
        )
        return [admin_event_out(session, e) for e in rows]


@admin_router.post("", status_code=201, response_model=AdminEventOut)
def admin_create(body: AdminEventIn, request: Request) -> AdminEventOut:
    starts, ends = _times(body.date, body.start, body.end)
    try:
        with session_scope(immediate=True) as session:
            _check_media(session, body.image_media_id)
            slug = body.slug
            if slug is None:
                slug = _free_slug(session, f"{ev.slugify(body.title)}-{body.date.isoformat()}")
            now = utcnow()
            e = ev.SiteEvent(
                slug=slug,
                title=body.title,
                starts_at=starts,
                ends_at=ends,
                description=body.description,
                price_pence=body.price_pence,
                capacity=body.capacity,
                image_media_id=body.image_media_id,
                published=body.published,
                created_at=now,
                updated_at=now,
            )
            session.add(e)
            session.flush()
            audit(
                "events.create",
                {"id": e.id, "slug": e.slug, "title": e.title, "published": e.published},
                request=request,
                session=session,
            )
            return admin_event_out(session, e)
    except IntegrityError as exc:
        raise _slug_taken() from exc


@admin_router.patch("/{event_id}", response_model=AdminEventOut)
def admin_patch(event_id: int, body: AdminEventPatchIn, request: Request) -> AdminEventOut:
    fields = body.model_dump(exclude_unset=True)
    for required in ("title", "date", "start", "slug", "description", "published"):
        if required in fields and fields[required] is None:
            raise HTTPException(
                422,
                detail=[
                    {"loc": ["body", required], "msg": "Can't be empty.", "type": "value_error"}
                ],
            )
    try:
        with session_scope(immediate=True) as session:
            e = _get(session, event_id)
            before = admin_event_out(session, e)
            if "image_media_id" in fields:
                _check_media(session, fields["image_media_id"])
            if {"date", "start", "end"} & fields.keys():
                day = fields.get("date", before.date)
                start = fields.get("start", before.start)
                end = fields["end"] if "end" in fields else before.end
                e.starts_at, e.ends_at = _times(day, start, end)
            for k in ("slug", "title", "description", "price_pence", "capacity", "published"):
                if k in fields:
                    setattr(e, k, fields[k])
            if "image_media_id" in fields:
                e.image_media_id = fields["image_media_id"]
            e.updated_at = utcnow()
            session.flush()
            after = admin_event_out(session, e)
            changed = {
                k: [getattr(before, k), getattr(after, k)]
                for k in (
                    "slug",
                    "title",
                    "date",
                    "start",
                    "end",
                    "description",
                    "price_pence",
                    "capacity",
                    "image_media_id",
                    "published",
                )
                if getattr(before, k) != getattr(after, k)
            }
            audit(
                "events.update", {"id": e.id, "changed": changed}, request=request, session=session
            )
            return after
    except IntegrityError as exc:
        raise _slug_taken() from exc


@admin_router.delete("/{event_id}", response_model=DeletedOut)
def admin_delete(event_id: int, request: Request, force: bool = Query(False)) -> DeletedOut:
    with session_scope(immediate=True) as session:
        e = _get(session, event_id)
        n = (
            session.scalar(
                select(func.count(ev.SiteEventRsvp.id)).where(ev.SiteEventRsvp.event_id == e.id)
            )
            or 0
        )
        if n and not force:
            raise HTTPException(
                409,
                detail={
                    "message": f"This event has {n} {'reply' if n == 1 else 'replies'}. "
                    "Unpublish it instead, or delete it with its replies.",
                    "rsvps": n,
                },
            )
        for r in session.scalars(select(ev.SiteEventRsvp).where(ev.SiteEventRsvp.event_id == e.id)):
            session.delete(r)
        audit(
            "events.delete",
            {"id": e.id, "slug": e.slug, "rsvps_deleted": n},
            request=request,
            session=session,
        )
        session.delete(e)
        return DeletedOut(id=event_id, rsvps_deleted=int(n))


def _rsvp_out(r: ev.SiteEventRsvp) -> AdminRsvpOut:
    return AdminRsvpOut(
        id=r.id,
        name=r.name,
        email=r.email,
        phone=r.phone,
        party=r.party,
        note=r.note,
        created_at=r.created_at,
        cancelled_at=r.cancelled_at,
    )


@admin_router.get("/{event_id}/rsvps", response_model=list[AdminRsvpOut])
def admin_rsvps(event_id: int) -> list[AdminRsvpOut]:
    with session_scope() as session:
        _get(session, event_id)
        rows = session.scalars(
            select(ev.SiteEventRsvp)
            .where(ev.SiteEventRsvp.event_id == event_id)
            .order_by(ev.SiteEventRsvp.created_at, ev.SiteEventRsvp.id)
        )
        return [_rsvp_out(r) for r in rows]


@admin_router.patch("/{event_id}/rsvps/{rsvp_id}", response_model=AdminRsvpOut)
def admin_rsvp_patch(
    event_id: int, rsvp_id: int, body: RsvpPatchIn, request: Request
) -> AdminRsvpOut:
    with session_scope(immediate=True) as session:
        r = session.get(ev.SiteEventRsvp, rsvp_id)
        if r is None or r.event_id != event_id:
            raise HTTPException(404, detail="No such reply.")
        was = r.cancelled_at is not None
        if body.cancelled and not was:
            r.cancelled_at = utcnow()
        elif not body.cancelled and was:
            # Restoring takes places again: refuse if that would overfill the event.
            e = _get(session, event_id)
            if e.capacity is not None and ev.seats_taken(session, e.id) + r.party > e.capacity:
                raise HTTPException(
                    409, detail="Restoring this reply would take the event over capacity."
                )
            r.cancelled_at = None
        if was != body.cancelled:
            audit(
                "events.rsvp.update",
                {"event_id": event_id, "rsvp_id": r.id, "cancelled": body.cancelled},
                request=request,
                session=session,
            )
        session.flush()
        return _rsvp_out(r)
