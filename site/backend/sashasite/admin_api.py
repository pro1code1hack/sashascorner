"""Admin API: auth, bookings, messages, café settings and the dashboard summary.

Contract: ``site/ADMIN.md``. Every route here sits under ``/api/admin``; every
non-GET one needs ``X-Admin: 1`` (router-level ``require_csrf``), and every one
but login/logout needs ``require_admin_session`` -- in production that is the Café
Ops back office's ``X-Site-Service-Key``, the only way in. Login and the password
change answer 410 whenever a service key is configured (one password: the back
office's); they work only as a dev fallback without one. Every write is audited in
``site_admin_audit``.
"""

from __future__ import annotations

import datetime as dt
import importlib
import logging
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from sqlalchemy import func, select

from sashasite import auth
from sashasite import booking as bk
from sashasite import settings_store as st
from sashasite import slots as sl
from sashasite.db import SiteBooking, SiteContactMessage, SiteSlotItem, session_scope, utcnow
from sashasite.notify import password_changed_text, send_owner
from sashasite.ratelimit import client_ip, login_rate_limit, password_rate_limit
from sashasite.schemas import (
    AdminBookingIn,
    AdminBookingOut,
    AdminBookingPatchIn,
    BookingStatus,
    DayOut,
    LoginIn,
    MenuSummaryOut,
    MeOut,
    MessageOut,
    MessagePatchIn,
    MessageStatus,
    OkOut,
    PasswordChangeIn,
    SummaryOut,
    TodayOut,
    WeekDayOut,
)

log = logging.getLogger("sashasite.admin")

router = APIRouter(prefix="/api/admin", dependencies=[Depends(auth.require_csrf)])
session_required = [Depends(auth.require_admin_session)]


def _notify_safely(text: str) -> None:
    """Background task: a Telegram failure must never surface anywhere."""
    try:
        send_owner(text)
    except Exception:
        log.exception("telegram notice failed")


# --- auth -----------------------------------------------------------------------


@router.post("/login", response_model=OkOut, dependencies=[Depends(login_rate_limit)])
def login(body: LoginIn, request: Request, response: Response) -> OkOut:
    if not auth.cookie_login_enabled():
        auth.audit("login.refused_back_office_only", request=request)
        raise auth.back_office_only()
    try:
        source = auth.check_password(body.password)
    except auth.AdminDisabled as exc:
        raise HTTPException(
            503, detail="Admin is disabled: no password is set (SITE_ADMIN_PASSWORD)."
        ) from exc
    except PermissionError as exc:
        auth.audit("login.failed", request=request)
        raise HTTPException(401, detail="Wrong password.") from exc
    with session_scope() as session:
        token = auth.create_session(session, request)
        auth.audit("login", {"password_source": source}, request=request, session=session)
    auth.set_session_cookie(response, request, token)
    return OkOut()


@router.post("/logout", status_code=204)
def logout(request: Request) -> Response:
    """Revokes this browser's session (if any) and clears the cookie. Idempotent."""
    token = request.cookies.get(auth.COOKIE_NAME)
    if token:
        with session_scope() as session:
            if auth.revoke_token(session, token):
                auth.audit("logout", request=request, session=session)
    out = Response(status_code=204)
    auth.clear_session_cookie(out, request)
    return out


@router.get("/me", response_model=MeOut, dependencies=session_required)
def me() -> MeOut:
    with session_scope() as session:
        source = auth.password_source(session)
    return MeOut(password_source=source or "env")


@router.post(
    "/password",
    response_model=OkOut,
    dependencies=[*session_required, Depends(password_rate_limit)],
)
def change_password(
    body: PasswordChangeIn, request: Request, response: Response, tasks: BackgroundTasks
) -> OkOut:
    if not auth.cookie_login_enabled():
        raise auth.back_office_only()
    try:
        auth.check_password(body.current)
    except (PermissionError, auth.AdminDisabled) as exc:
        auth.audit("password.change_failed", request=request)
        raise HTTPException(400, detail="The current password is wrong.") from exc
    ip = client_ip(request)
    with session_scope(immediate=True) as session:
        revoked = auth.set_password(session, body.new)
        token = auth.create_session(session, request)
        auth.audit("password.change", {"sessions_revoked": revoked}, ip=ip, session=session)
    auth.set_session_cookie(response, request, token)
    tasks.add_task(_notify_safely, password_changed_text(ip=ip, revoked=revoked))
    return OkOut()


# --- bookings -------------------------------------------------------------------


@router.get("/bookings", response_model=list[AdminBookingOut], dependencies=session_required)
def bookings(
    from_: dt.date | None = Query(None, alias="from"),
    to: dt.date | None = Query(None),
    status: BookingStatus | Literal["all"] | None = Query(None),
) -> list[AdminBookingOut]:
    wanted = None if status in (None, "all") else status
    return [bk.admin_out(b) for b in bk.list_bookings(from_, to, wanted)]


@router.get("/bookings/day", response_model=DayOut, dependencies=session_required)
def bookings_day(date: dt.date = Query(...)) -> DayOut:
    return bk.day_view(st.live_cafe(), date)


@router.post(
    "/bookings", status_code=201, response_model=AdminBookingOut, dependencies=session_required
)
def booking_create(body: AdminBookingIn, request: Request) -> AdminBookingOut:
    try:
        b = bk.admin_create(st.live_cafe(), body)
    except bk.NotBookable as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    except bk.SlotFull as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    auth.audit(
        "booking.create",
        {
            "id": b.id,
            "reference": b.reference,
            "date": b.local_date.isoformat(),
            "time": b.local_time,
            "party": b.party,
            "override_capacity": body.override_capacity,
        },
        request=request,
    )
    return bk.admin_out(b)


@router.patch(
    "/bookings/{booking_id}", response_model=AdminBookingOut, dependencies=session_required
)
def booking_patch(booking_id: int, body: AdminBookingPatchIn, request: Request) -> AdminBookingOut:
    try:
        b, changes = bk.admin_patch(st.live_cafe(), booking_id, body)
    except bk.NotFound as exc:
        raise HTTPException(404, detail="No such booking.") from exc
    except bk.NotBookable as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    except bk.SlotFull as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    if changes:
        auth.audit(
            "booking.update",
            {
                "id": b.id,
                "reference": b.reference,
                "changes": changes,
                "override_capacity": body.override_capacity,
            },
            request=request,
        )
    return bk.admin_out(b)


# --- messages -------------------------------------------------------------------


def _message_out(m: SiteContactMessage) -> MessageOut:
    return MessageOut.model_validate(m, from_attributes=True)


@router.get("/messages", response_model=list[MessageOut], dependencies=session_required)
def messages(
    status: MessageStatus | Literal["all"] = Query("all"),
) -> list[MessageOut]:
    with session_scope() as session:
        q = select(SiteContactMessage).order_by(
            SiteContactMessage.created_at.desc(), SiteContactMessage.id.desc()
        )
        if status != "all":
            q = q.where(SiteContactMessage.status == status)
        return [_message_out(m) for m in session.scalars(q)]


@router.patch("/messages/{message_id}", response_model=MessageOut, dependencies=session_required)
def message_patch(message_id: int, body: MessagePatchIn, request: Request) -> MessageOut:
    with session_scope() as session:
        m = session.get(SiteContactMessage, message_id)
        if m is None:
            raise HTTPException(404, detail="No such message.")
        before = m.status
        if body.status != before:
            m.status = body.status
            if body.status == "handled":
                m.handled_at = utcnow()
            elif body.status == "new":
                m.handled_at = None
            # archived keeps handled_at as it was: when (if ever) it was dealt with
            auth.audit(
                "message.update",
                {"id": m.id, "status": [before, body.status]},
                request=request,
                session=session,
            )
        session.flush()
        return _message_out(m)


# --- settings -------------------------------------------------------------------


@router.get("/settings", response_model=st.SettingsOut, dependencies=session_required)
def settings_get() -> st.SettingsOut:
    return st.load_settings()


@router.put("/settings", response_model=st.SettingsOut, dependencies=session_required)
def settings_put(body: st.SettingsPutIn, request: Request) -> st.SettingsOut:
    try:
        out, changed = st.update_settings(body)
    except st.SettingsInvalid as exc:
        raise RequestValidationError(exc.errors) from exc
    if changed:
        auth.audit(
            "settings.update",
            {"keys": changed, "body": body.model_dump(mode="json", exclude_unset=True)},
            request=request,
        )
    return out


# --- dashboard ------------------------------------------------------------------


def _menu_summary() -> MenuSummaryOut:
    """Asks ``sashasite.menu_source.summary()`` (the menu agent's module) when it
    exists; until then the site menu comes from the board file."""
    try:
        mod = importlib.import_module("sashasite.menu_source")
        fn = getattr(mod, "summary", None)
        if fn is None:
            return MenuSummaryOut(source="board", warnings=0)
        return MenuSummaryOut.model_validate(fn(), from_attributes=True)
    except ImportError:
        return MenuSummaryOut(source="board", warnings=0)
    except Exception:
        log.exception("menu summary failed; reporting the board source")
        return MenuSummaryOut(source="board", warnings=0)


@router.get("/summary", response_model=SummaryOut, dependencies=session_required)
def summary() -> SummaryOut:
    cafe = st.live_cafe()
    now = utcnow()
    today = now.astimezone(bk.local_tz()).date()
    week_end = today + dt.timedelta(days=6)
    closed = cafe.closure(today) is not None or cafe.day(today.weekday()).closed
    with session_scope() as session:
        rows = session.execute(
            select(SiteBooking.local_date, func.count(), func.sum(SiteBooking.party))
            .where(
                SiteBooking.local_date >= today,
                SiteBooking.local_date <= week_end,
                SiteBooking.status.in_(bk.HOLDING),
            )
            .group_by(SiteBooking.local_date)
        ).all()
        per_day = {d: (int(n), int(c or 0)) for d, n, c in rows}
        upcoming = session.scalars(
            select(SiteBooking)
            .where(
                SiteBooking.local_date == today,
                SiteBooking.status == "confirmed",
                SiteBooking.starts_at >= now,
            )
            .order_by(SiteBooking.starts_at, SiteBooking.id)
            .limit(5)
        ).all()
        messages_new = session.scalar(
            select(func.count())
            .select_from(SiteContactMessage)
            .where(SiteContactMessage.status == "new")
        )
        filled = set(session.scalars(select(SiteSlotItem.slot_key).distinct()))
    week = [
        WeekDayOut(
            date=d,
            bookings=per_day.get(d, (0, 0))[0],
            covers=per_day.get(d, (0, 0))[1],
        )
        for d in (today + dt.timedelta(days=i) for i in range(7))
    ]
    return SummaryOut(
        today=TodayOut(
            date=today,
            closed=closed,
            bookings=week[0].bookings,
            covers=week[0].covers,
            capacity=cafe.booking.covers_per_slot,
            next=[bk.admin_out(b) for b in upcoming],
        ),
        week=week,
        messages_new=int(messages_new or 0),
        photos_missing=sum(1 for d in sl.registry().slot if d.key not in filled),
        menu=_menu_summary(),
    )
