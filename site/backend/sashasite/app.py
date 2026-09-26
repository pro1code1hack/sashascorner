"""FastAPI app. All routes under /api; JSON, snake_case."""

from __future__ import annotations

import datetime as dt
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from sashasite import booking as bk
from sashasite import media as md
from sashasite import slots as sl
from sashasite.config import CafeFacts, get_cafe, get_settings, warn_unconfirmed
from sashasite.db import SiteBooking, SiteContactMessage, session_scope, utcnow
from sashasite.mediaserve import BodyLimit, VariantFiles
from sashasite.menu import cached_menu, load_board
from sashasite.notify import booking_text, contact_text, send_owner
from sashasite.ratelimit import rate_limit, upload_rate_limit
from sashasite.schemas import (
    AddressOut,
    AdminBookingOut,
    AvailabilityOut,
    BookingCreatedOut,
    BookingIn,
    BookingInfoOut,
    BookingOut,
    ContactIn,
    GeoOut,
    HoursClosedOut,
    HoursOpenOut,
    ImageSlotOut,
    ImageSlotsOut,
    InfoOut,
    MediaDeletedOut,
    MediaOut,
    MediaPatchIn,
    MenuOut,
    OkOut,
    SlotPutIn,
    SocialsOut,
)

log = logging.getLogger("sashasite")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    cafe = get_cafe()  # validate config at startup, not on first request
    load_board()  # a bad price or duplicate id fails startup, loudly
    reg = sl.registry()  # likewise a bad slots.toml
    log.info("slots.toml: %d slot(s); media in %s", len(reg.slot), get_settings().media_dir)
    warn_unconfirmed(cafe)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Sasha's Corner site API",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )
    app.add_middleware(
        BodyLimit, method="POST", path_prefix="/api/admin/media", limit=md.MAX_REQUEST_BYTES
    )
    register(app)
    media_dir = md.media_dir()
    # Same files twice: /media is canonical (Caddy in prod), /api/media-files is
    # for the Astro dev server, which proxies only /api.
    app.mount("/media", VariantFiles(directory=media_dir), name="media")
    app.mount("/api/media-files", VariantFiles(directory=media_dir), name="media-files")
    return app


def info_out(cafe: CafeFacts) -> InfoOut:
    hours: list[HoursOpenOut | HoursClosedOut] = []
    for h in sorted(cafe.hours, key=lambda h: h.weekday):
        if h.closed or h.open is None or h.close is None:
            hours.append(HoursClosedOut(weekday=h.weekday))
        else:
            hours.append(
                HoursOpenOut(
                    weekday=h.weekday,
                    open=h.open.strftime("%H:%M"),
                    close=h.close.strftime("%H:%M"),
                )
            )
    b = cafe.booking
    return InfoOut(
        name=cafe.name,
        address=AddressOut(**cafe.address.model_dump()),
        geo=GeoOut(**cafe.geo.model_dump()),
        phone=cafe.phone,
        email=cafe.email,
        hours=hours,
        socials=SocialsOut(**cafe.socials.model_dump()),
        booking=BookingInfoOut(
            max_party=b.max_party,
            slot_minutes=b.slot_minutes,
            horizon_days=b.horizon_days,
            min_lead_minutes=b.min_lead_minutes,
        ),
        confirmed=cafe.confirmed,
    )


def _booking_out(b: SiteBooking) -> BookingOut:
    return BookingOut(
        reference=b.reference,
        manage_token=b.manage_token,
        status="confirmed" if b.status == "confirmed" else "cancelled",
        date=b.local_date,
        time=b.local_time,
        party=b.party,
        name=b.name,
        created_at=b.created_at,
    )


_basic = HTTPBasic(auto_error=False)


def require_admin(creds: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    password = get_settings().admin_password
    if not password:
        raise HTTPException(503, detail="Admin is disabled: SITE_ADMIN_PASSWORD is not set.")
    ok = creds is not None and secrets.compare_digest(
        creds.password.encode("utf-8"), password.encode("utf-8")
    )
    if not ok:
        # Challenge only when no credentials were sent. A wrong password from the
        # admin page must come back as a plain 401 the page can explain; with the
        # header, browsers throw their own native password dialog over it.
        challenge = {"WWW-Authenticate": "Basic"} if creds is None else None
        raise HTTPException(401, detail="Unauthorised.", headers=challenge)


def register(app: FastAPI) -> None:
    @app.get("/api/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/info", response_model=InfoOut)
    def info() -> InfoOut:
        return info_out(get_cafe())

    @app.get("/api/menu", response_model=MenuOut)
    def menu() -> MenuOut:
        return cached_menu()

    @app.get("/api/availability", response_model=AvailabilityOut)
    def availability(date: dt.date = Query(...), party: int = Query(..., ge=1)) -> AvailabilityOut:
        cafe = get_cafe()
        if party > cafe.booking.max_party:
            raise HTTPException(
                422,
                detail=f"Online bookings are for up to {cafe.booking.max_party} people — "
                "for larger groups please use the contact form.",
            )
        with session_scope() as session:
            return bk.availability(session, cafe, date, party)

    @app.post(
        "/api/bookings",
        status_code=201,
        response_model=BookingCreatedOut,
        dependencies=[Depends(rate_limit)],
    )
    def create_booking(body: BookingIn, tasks: BackgroundTasks) -> BookingCreatedOut:
        if body.website:
            # Honeypot tripped: look successful, store nothing, tell nobody.
            log.info("booking honeypot tripped; discarded")
            return BookingCreatedOut(
                reference="SC-" + "".join(secrets.choice(bk.REF_ALPHABET) for _ in range(4)),
                manage_token=secrets.token_urlsafe(24),
                status="confirmed",
                date=body.date,
                time=body.time,
                party=body.party,
                name=body.name,
            )
        cafe = get_cafe()
        try:
            b = bk.create_booking(cafe, body)
        except bk.NotBookable as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        except bk.SlotFull as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        tasks.add_task(
            send_owner,
            booking_text(
                kind="created",
                reference=b.reference,
                day=b.local_date,
                time=b.local_time,
                party=b.party,
                name=b.name,
                phone=b.phone,
                notes=b.notes,
            ),
        )
        out = _booking_out(b)
        return BookingCreatedOut(**out.model_dump(exclude={"created_at"}))

    @app.get("/api/bookings/{manage_token}", response_model=BookingOut)
    def get_booking(manage_token: str) -> BookingOut:
        b = bk.get_booking(manage_token)
        if b is None:
            raise HTTPException(404, detail="Booking not found.")
        return _booking_out(b)

    @app.post(
        "/api/bookings/{manage_token}/cancel",
        response_model=BookingOut,
        dependencies=[Depends(rate_limit)],
    )
    def cancel_booking(manage_token: str, tasks: BackgroundTasks) -> BookingOut:
        try:
            res = bk.cancel_booking(manage_token)
        except bk.BookingPassed as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        if res is None:
            raise HTTPException(404, detail="Booking not found.")
        b, changed = res
        if changed:
            tasks.add_task(
                send_owner,
                booking_text(
                    kind="cancelled",
                    reference=b.reference,
                    day=b.local_date,
                    time=b.local_time,
                    party=b.party,
                    name=b.name,
                    phone=b.phone,
                    notes=None,
                ),
            )
        return _booking_out(b)

    @app.post(
        "/api/contact", status_code=201, response_model=OkOut, dependencies=[Depends(rate_limit)]
    )
    def contact(body: ContactIn, tasks: BackgroundTasks) -> OkOut:
        if body.website:
            log.info("contact honeypot tripped; discarded")
            return OkOut()
        with session_scope() as session:
            session.add(
                SiteContactMessage(
                    name=body.name,
                    email=str(body.email),
                    topic=body.topic,
                    message=body.message,
                    created_at=utcnow(),
                )
            )
        tasks.add_task(
            send_owner,
            contact_text(
                name=body.name, email=str(body.email), topic=body.topic, message=body.message
            ),
        )
        return OkOut()

    @app.get(
        "/api/admin/bookings",
        response_model=list[AdminBookingOut],
        dependencies=[Depends(require_admin)],
    )
    def admin_bookings(
        from_: dt.date | None = Query(None, alias="from"),
        to: dt.date | None = Query(None),
    ) -> list[AdminBookingOut]:
        return [
            AdminBookingOut(
                **_booking_out(b).model_dump(),
                id=b.id,
                email=b.email,
                phone=b.phone,
                notes=b.notes,
                cancelled_at=b.cancelled_at,
            )
            for b in bk.list_bookings(from_, to)
        ]

    # --- media & image slots ---------------------------------------------------------

    @app.get("/api/slots", response_model=ImageSlotsOut)
    def slots(response: Response) -> ImageSlotsOut:
        response.headers["Cache-Control"] = "public, max-age=30"
        return sl.build_slots()

    def _labels() -> dict[str, str]:
        return {d.key: d.label for d in sl.registry().slot}

    @app.get(
        "/api/admin/media",
        response_model=list[MediaOut],
        dependencies=[Depends(require_admin)],
    )
    def admin_media_list() -> list[MediaOut]:
        labels = _labels()
        return [md.media_out(m, uses, labels) for m, uses in md.list_media()]

    @app.post(
        "/api/admin/media",
        status_code=201,
        response_model=MediaOut,
        dependencies=[Depends(require_admin), Depends(upload_rate_limit)],
        responses={200: {"description": "Identical file already in the library"}},
    )
    def admin_media_upload(
        response: Response,
        file: UploadFile = File(...),
        alt: str = Form("", max_length=300),
    ) -> MediaOut:
        # Read one byte past the limit: enough to know it is too big, no more.
        data = file.file.read(md.MAX_UPLOAD_BYTES + 1)
        try:
            m, created = md.add_media(data, file.filename, alt)
        except md.MediaError as exc:
            raise HTTPException(exc.status, detail=str(exc)) from exc
        if not created:
            response.status_code = 200
        return md.media_out(m, md.usage_of(m.id), _labels())

    @app.patch(
        "/api/admin/media/{media_id}",
        response_model=MediaOut,
        dependencies=[Depends(require_admin)],
    )
    def admin_media_patch(media_id: int, body: MediaPatchIn) -> MediaOut:
        m = md.set_alt(media_id, body.alt)
        if m is None:
            raise HTTPException(404, detail="No such photo.")
        return md.media_out(m, md.usage_of(m.id), _labels())

    @app.delete(
        "/api/admin/media/{media_id}",
        response_model=MediaDeletedOut,
        dependencies=[Depends(require_admin)],
    )
    def admin_media_delete(media_id: int, force: bool = Query(False)) -> MediaDeletedOut:
        try:
            keys = md.delete_media(media_id, force=force)
        except md.MediaInUse as exc:
            raise HTTPException(
                409,
                detail={
                    "message": "This photo is on the site in "
                    + ", ".join(exc.keys)
                    + ". Remove it from those places first, or delete with force.",
                    "slots": exc.keys,
                },
            ) from exc
        if keys is None:
            raise HTTPException(404, detail="No such photo.")
        return MediaDeletedOut(id=media_id, unassigned=keys)

    @app.put(
        "/api/admin/slots/{key}",
        response_model=ImageSlotOut,
        dependencies=[Depends(require_admin)],
    )
    def admin_slot_put(key: str, body: SlotPutIn) -> ImageSlotOut:
        try:
            return sl.replace_slot(key, body.items)
        except sl.SlotError as exc:
            raise HTTPException(422, detail=str(exc)) from exc


app = create_app()
