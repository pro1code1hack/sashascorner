"""The PassKit web service: the five endpoints Wallet calls on `webServiceURL`.

Apple's contract ("Wallet Passes Web Service Reference"), mounted at /wallet/apple/v1:

  POST   /devices/{device}/registrations/{passType}/{serial}   register   201 new, 200 known
  GET    /devices/{device}/registrations/{passType}?passesUpdatedSince=tag
                                                     serials changed  200 JSON, 204 none
  GET    /passes/{passType}/{serial}                  latest pass      200, 304 unchanged
  DELETE /devices/{device}/registrations/{passType}/{serial}   unregister 200
  POST   /log                                         device error log 200

Card-scoped calls carry `Authorization: ApplePass <authenticationToken>`, which is the
card's `auth_token`; a wrong or missing one is 401 (also for an unknown serial, so the
answer does not reveal which serials exist). The update tag is `updated_at` as epoch
seconds; the comparison is inclusive, because a card changed in the same second the tag
was issued must not be missed -- at worst a device downloads one unchanged pass.

Public on the customer domain (Caddy forwards `/wallet/apple/*`), no shared password:
Apple cannot hold one.
"""

from __future__ import annotations

import hmac
import json
import logging
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Header, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.api.runtime import in_session
from cafeops.db.models.loyalty import LoyaltyCard, WalletPushOutbox
from cafeops.db.models.wallet import WalletAppleRegistration
from cafeops.integrations.wallet.apple import PKPASS_MIME, build_pkpass
from cafeops.integrations.wallet.config import WalletNotConfigured, wallet_settings
from cafeops.services.loyalty.card_view import card_view

log = logging.getLogger("cafeops.wallet.apple")

router = APIRouter(prefix="/wallet/apple/v1", tags=["wallet"], include_in_schema=False)

AuthHeader = Annotated[str | None, Header(alias="Authorization")]


def _presented_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme != "ApplePass" or not token.strip():
        return None
    return token.strip()


def _authorised_card(
    session: Session, serial: str, authorization: str | None
) -> LoyaltyCard | None:
    token = _presented_token(authorization)
    card = session.get(LoyaltyCard, serial)
    if card is None or token is None:
        # Still compare against something so an unknown serial costs the same time.
        hmac.compare_digest(b"x" * 64, (token or "").encode())
        return None
    if not hmac.compare_digest(card.auth_token.encode(), token.encode()):
        return None
    return card


def _our_pass_type(pass_type: str) -> bool:
    return (
        bool(wallet_settings.apple_pass_type_id) and pass_type == wallet_settings.apple_pass_type_id
    )


def _epoch(at: datetime) -> int:
    return int(at.timestamp())


def latest_message(session: Session, card_id: str) -> str | None:
    """Newest lock-screen text enqueued for the card (see `apple.py` docstring)."""
    return session.scalars(
        select(WalletPushOutbox.message)
        .where(WalletPushOutbox.card_id == card_id, WalletPushOutbox.message.is_not(None))
        .order_by(WalletPushOutbox.id.desc())
        .limit(1)
    ).first()


@router.post("/devices/{device_id}/registrations/{pass_type}/{serial}")
async def register_device(
    device_id: str,
    pass_type: str,
    serial: str,
    authorization: AuthHeader = None,
    payload: Annotated[dict[str, Any] | None, Body()] = None,
) -> Response:
    if not _our_pass_type(pass_type):
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    push_token = str((payload or {}).get("pushToken") or "").strip()
    if not push_token or len(push_token) > 200 or len(device_id) > 128:
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    def work(session: Session) -> int:
        card = _authorised_card(session, serial, authorization)
        if card is None:
            return status.HTTP_401_UNAUTHORIZED
        existing = session.scalars(
            select(WalletAppleRegistration).where(
                WalletAppleRegistration.device_library_id == device_id,
                WalletAppleRegistration.card_id == card.id,
            )
        ).first()
        if existing is not None:
            # Apple re-registers after a restore or token rotation; keep the newest token.
            existing.push_token = push_token
            return status.HTTP_200_OK
        session.add(
            WalletAppleRegistration(
                device_library_id=device_id, push_token=push_token, card_id=card.id
            )
        )
        return status.HTTP_201_CREATED

    return Response(status_code=await in_session(work))


@router.get("/devices/{device_id}/registrations/{pass_type}")
async def updated_serials(
    device_id: str,
    pass_type: str,
    passes_updated_since: Annotated[str | None, Query(alias="passesUpdatedSince")] = None,
) -> Response:
    if not _our_pass_type(pass_type):
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    since: int | None = None
    if passes_updated_since:
        try:
            since = int(passes_updated_since)
        except ValueError:
            since = None  # a tag we did not issue: answer with everything

    def work(session: Session) -> list[tuple[str, datetime]]:
        rows = session.execute(
            select(LoyaltyCard.id, LoyaltyCard.updated_at)
            .join(WalletAppleRegistration, WalletAppleRegistration.card_id == LoyaltyCard.id)
            .where(WalletAppleRegistration.device_library_id == device_id)
        ).all()
        return [(r[0], r[1]) for r in rows]

    rows = await in_session(work)
    changed = [(cid, at) for cid, at in rows if since is None or _epoch(at) >= since]
    if not changed:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    body = {
        "serialNumbers": sorted(cid for cid, _ in changed),
        "lastUpdated": str(max(_epoch(at) for _, at in changed)),
    }
    return Response(content=json.dumps(body), media_type="application/json")


@router.get("/passes/{pass_type}/{serial}")
async def latest_pass(
    pass_type: str,
    serial: str,
    authorization: AuthHeader = None,
    if_modified_since: Annotated[str | None, Header(alias="If-Modified-Since")] = None,
) -> Response:
    if not _our_pass_type(pass_type):
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    ims: datetime | None = None
    if if_modified_since:
        try:
            ims = parsedate_to_datetime(if_modified_since)
            if ims.tzinfo is None:
                ims = ims.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            ims = None

    def work(session: Session) -> tuple[int, bytes | None, datetime | None]:
        card = _authorised_card(session, serial, authorization)
        if card is None:
            return status.HTTP_401_UNAUTHORIZED, None, None
        updated = card.updated_at
        if ims is not None and _epoch(updated) <= _epoch(ims):
            return status.HTTP_304_NOT_MODIFIED, None, updated
        view = card_view(session, card.id)
        return (
            status.HTTP_200_OK,
            build_pkpass(view, latest_message=latest_message(session, card.id)),
            updated,
        )

    try:
        code, data, updated = await in_session(work)
    except WalletNotConfigured:
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    headers = {}
    if updated is not None:
        headers["Last-Modified"] = format_datetime(updated.astimezone(UTC), usegmt=True)
    if code != status.HTTP_200_OK:
        return Response(status_code=code, headers=headers)
    headers["Cache-Control"] = "no-cache"
    return Response(content=data, media_type=PKPASS_MIME, headers=headers)


@router.delete("/devices/{device_id}/registrations/{pass_type}/{serial}")
async def unregister_device(
    device_id: str, pass_type: str, serial: str, authorization: AuthHeader = None
) -> Response:
    if not _our_pass_type(pass_type):
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    def work(session: Session) -> int:
        card = _authorised_card(session, serial, authorization)
        if card is None:
            return status.HTTP_401_UNAUTHORIZED
        for reg in session.scalars(
            select(WalletAppleRegistration).where(
                WalletAppleRegistration.device_library_id == device_id,
                WalletAppleRegistration.card_id == card.id,
            )
        ):
            session.delete(reg)
        return status.HTTP_200_OK

    return Response(status_code=await in_session(work))


@router.post("/log")
async def device_log(payload: Annotated[dict[str, Any] | None, Body()] = None) -> Response:
    # Wallet's own diagnostics ("signature invalid", "web service returned 500"). The
    # only way to learn why a pass silently failed to update, so keep it -- bounded,
    # because the endpoint is unauthenticated.
    logs = (payload or {}).get("logs") or []
    if isinstance(logs, list):
        for line in logs[:20]:
            log.warning("wallet device log: %s", str(line)[:500])
    return Response(status_code=status.HTTP_200_OK)


__all__ = ["latest_message", "router"]
