"""Routes for the shell, auth, settings, setup, sync and agents. shell-agents spec.

`open_router` is unauthenticated and carries only sign-in and sign-out. Everything
else is on `router`, behind `ApiAuth` (a session token, or the password as X-API-Key).

Writes here, and what guards them:

* `POST /api/auth/session` -- rate-limited per client IP (services/auth.py).
* `POST /api/auth/password` -- needs a valid session/password AND the current
  password; revokes every session; audited; Telegram notice.
* `POST /api/sync` -- single-flight on the `sync_run` row; refused without
  Lightspeed credentials (the web never replays fixtures into the database).
* `POST /api/agents/proposals/{id}/accept|decline` -- a named person decides; accept
  routes to the service that owns that change. Nothing here creates, confirms or
  sends a purchase order (DECISIONS 1).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status

from cafeops.api.areas import shell_views as views
from cafeops.api.areas.shell_schemas import (
    AgentProposalsOut,
    AgentRunsOut,
    DecisionIn,
    DecisionOut,
    PasswordChangeIn,
    PasswordChangeOut,
    SessionOut,
    SettingsOut,
    SetupOut,
    ShellOut,
    SignInIn,
    SyncIn,
    SyncRunOut,
    SyncStartedOut,
    TelegramNotice,
)
from cafeops.api.runtime import in_session
from cafeops.api.security import ApiAuth, client_ip
from cafeops.db.base import session_scope
from cafeops.db.models import SyncTrigger
from cafeops.services import auth as auth_service
from cafeops.services.sync_runs import SyncRefused, begin_manual_sync, run_recorded_sync

log = logging.getLogger("cafeops.api.shell")

#: Unauthenticated: sign-in and sign-out only.
open_router = APIRouter(tags=["auth"])
router = APIRouter(dependencies=[ApiAuth])


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------


@open_router.post(
    "/api/auth/session",
    response_model=SessionOut,
    summary="Trade the shared password for a session token. 5 failures/min per IP.",
)
async def sign_in(body: SignInIn, request: Request, response: Response) -> SessionOut:
    ip = client_ip(request)
    ua = request.headers.get("user-agent")

    def work() -> auth_service.SignInResult:
        with session_scope() as session:
            return auth_service.sign_in(
                session, body.password, client_ip=ip, user_agent=ua, actor=body.actor
            )

    result = await asyncio.to_thread(work)
    if result.issued is None:
        headers = {"Retry-After": str(result.retry_after)} if result.retry_after else None
        if result.status == 401:
            headers = {"WWW-Authenticate": "Bearer"}
        raise HTTPException(status_code=result.status, detail=result.message, headers=headers)
    response.headers["Cache-Control"] = "no-store"
    return SessionOut(token=result.issued.token, expires_at=result.issued.expires_at)


@open_router.delete(
    "/api/auth/session",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out: revoke this session token. Idempotent.",
)
async def sign_out(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Response:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip() or None
    if token is not None:
        ip = client_ip(request)

        def work() -> None:
            with session_scope() as session:
                auth_service.revoke_session(session, token, client_ip=ip)

        await asyncio.to_thread(work)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _telegram_notice(actor: str | None, revoked: int) -> TelegramNotice:
    """Tell the owner the web password changed, so an unexpected change is seen."""
    from cafeops.bot import formatters as fmt
    from cafeops.bot.notify import notifier_for_settings

    text = fmt.password_changed(datetime.now(UTC), actor, revoked)
    notifier = notifier_for_settings()
    if not notifier.is_live:
        await notifier.send(text)
        return TelegramNotice(
            sent=False,
            detail="Not sent: CAFEOPS_TELEGRAM_BOT_TOKEN / CAFEOPS_TELEGRAM_OWNER_CHAT_ID "
            "are not set, so the notice was written to the server log instead.",
        )
    try:
        await notifier.send(text)
    except Exception as exc:  # the change stands; the notice is best-effort and says so
        log.warning("password-change Telegram notice failed: %s", exc)
        return TelegramNotice(sent=False, detail=f"Telegram refused the notice: {exc}")
    return TelegramNotice(sent=True, detail="The owner was told in Telegram.")


@router.post(
    "/api/auth/password",
    response_model=PasswordChangeOut,
    tags=["auth"],
    summary="Change the shared password. Revokes every session; audited; Telegram notice.",
)
async def change_password(
    body: PasswordChangeIn, request: Request, response: Response
) -> PasswordChangeOut:
    ip = client_ip(request)
    ua = request.headers.get("user-agent")
    session_id = getattr(request.state, "auth_session_id", None)

    def work() -> auth_service.PasswordChanged | auth_service.PasswordChangeRefused:
        with session_scope() as session:
            try:
                return auth_service.change_password(
                    session,
                    current_password=body.current_password,
                    new_password=body.new_password,
                    client_ip=ip,
                    user_agent=ua,
                    session_id=session_id,
                    actor=body.actor,
                )
            except auth_service.PasswordChangeRefused as exc:
                # Returned, not raised, so session_scope COMMITS the refusal's audit row.
                return exc

    result = await asyncio.to_thread(work)
    if isinstance(result, auth_service.PasswordChangeRefused):
        raise HTTPException(status_code=result.status, detail={"message": result.message})
    notice = await _telegram_notice(body.actor, result.revoked_sessions)
    response.headers["Cache-Control"] = "no-store"
    return PasswordChangeOut(
        token=result.issued.token,
        expires_at=result.issued.expires_at,
        revoked_sessions=result.revoked_sessions,
        message="Changed. Tell the team.",
        telegram=notice,
    )


# --------------------------------------------------------------------------
# shell, settings, setup
# --------------------------------------------------------------------------


@router.get(
    "/api/shell",
    response_model=ShellOut,
    tags=["shell"],
    summary="The frame: sync line, nav badges, banners. Cheap: counts rows only.",
)
async def shell() -> ShellOut:
    return await in_session(views.shell_view)


@router.get("/api/settings", response_model=SettingsOut, tags=["shell"])
async def settings_() -> SettingsOut:
    return await in_session(views.settings_view)


@router.get(
    "/api/setup",
    response_model=SetupOut,
    tags=["shell"],
    summary="The five Setup checklist steps, plus the doctor's other findings.",
)
async def setup() -> SetupOut:
    return await in_session(views.setup_view)


# --------------------------------------------------------------------------
# sync
# --------------------------------------------------------------------------

#: Strong references to running syncs; a bare create_task can be garbage-collected.
_SYNC_TASKS: set[asyncio.Task[None]] = set()


async def _run_sync(run_id: int, requested_by: str | None) -> None:
    try:
        await asyncio.to_thread(
            run_recorded_sync,
            trigger=SyncTrigger.MANUAL_WEB,
            fixtures=False,
            requested_by=requested_by,
            run_id=run_id,
            expand_after=True,
        )
    except Exception:  # recorded on the sync_run row as FAILED; logged here too
        log.exception("manual sync %s failed", run_id)


@router.post(
    "/api/sync",
    response_model=SyncStartedOut,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["shell"],
    summary="Sync Lightspeed now, then expand sales into stock. Single-flight.",
)
async def sync_now(body: SyncIn | None = None) -> SyncStartedOut:
    requested_by = body.requested_by if body is not None else None

    def work() -> int | SyncRefused:
        with session_scope() as session:
            try:
                return begin_manual_sync(session, requested_by=requested_by or "web")
            except SyncRefused as exc:
                return exc

    result = await asyncio.to_thread(work)
    if isinstance(result, SyncRefused):
        raise HTTPException(status_code=result.status, detail={"message": result.message})
    task = asyncio.create_task(_run_sync(result, requested_by or "web"))
    _SYNC_TASKS.add(task)
    task.add_done_callback(_SYNC_TASKS.discard)
    return SyncStartedOut(
        run_id=result, message="Syncing. Sales and stock update when it finishes."
    )


@router.get("/api/sync/{run_id}", response_model=SyncRunOut, tags=["shell"])
async def sync_run(run_id: int) -> SyncRunOut:
    return await in_session(lambda session: views.sync_run_view(session, run_id))


# --------------------------------------------------------------------------
# agents
# --------------------------------------------------------------------------


@router.get(
    "/api/agents/proposals",
    response_model=AgentProposalsOut,
    tags=["agents"],
    summary="Proposals waiting for a person, the last few decided, and orders in Telegram.",
)
async def agent_proposals(
    limit: Annotated[int, Query(ge=1, le=50, description="How many decided to return.")] = 6,
) -> AgentProposalsOut:
    return await in_session(lambda session: views.proposals_view(session, decided_limit=limit))


@router.post(
    "/api/agents/proposals/{proposal_id}/accept",
    response_model=DecisionOut,
    tags=["agents"],
    summary="Accept: runs the kind's own service. 409 if already decided.",
)
async def accept_proposal(proposal_id: int, body: DecisionIn) -> DecisionOut:
    return await in_session(
        lambda session: views.decide_view(
            session,
            proposal_id=proposal_id,
            accept=True,
            decided_by=body.decided_by,
            note=body.note,
        )
    )


@router.post(
    "/api/agents/proposals/{proposal_id}/decline",
    response_model=DecisionOut,
    tags=["agents"],
    summary="Decline: nothing changes; the decision is recorded with the person's name.",
)
async def decline_proposal(proposal_id: int, body: DecisionIn) -> DecisionOut:
    return await in_session(
        lambda session: views.decide_view(
            session,
            proposal_id=proposal_id,
            accept=False,
            decided_by=body.decided_by,
            note=body.note,
        )
    )


@router.get("/api/agents/runs", response_model=AgentRunsOut, tags=["agents"])
async def agent_runs(
    agent: Annotated[str | None, Query(max_length=60)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    before: Annotated[str | None, Query(description="ISO timestamp; rows older than it.")] = None,
) -> AgentRunsOut:
    cutoff = views.parse_before(before)
    return await in_session(
        lambda session: views.runs_view(session, agent=agent, limit=limit, before=cutoff)
    )
