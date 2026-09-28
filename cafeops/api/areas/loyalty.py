"""The public loyalty API: join, the card, wallets, recovery, unsubscribe (CONTRACT §4).

Open routes -- no back-office password -- served on the public domain through Caddy
(`/api/loyalty/*`). What stands in for auth:

- card-scoped routes need the card's token (`X-Card-Token` header or `?t=`), compared in
  constant time; a wrong token and a missing card are the same 404;
- join and recovery are rate-limited per client IP and carry a honeypot field;
- recovery answers the same whether or not the contact is a member.

Thin like every area router: parse, hand the work to a view on a worker thread, return,
then kick the wallet drain after the response has gone.
"""

from __future__ import annotations

import asyncio
import html
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from cafeops.api.areas import loyalty_views as views
from cafeops.api.areas.loyalty_edge import (
    deliver_after_commit,
    is_wallet_not_configured,
    kick_wallets,
    limit,
    wallet_not_configured_response,
)
from cafeops.api.areas.loyalty_schemas import (
    CardState,
    JoinIn,
    JoinProgramIn,
    JoinResult,
    PreferencesIn,
    ProgramOut,
    ProgramsOut,
    RecoverIn,
    RecoverOut,
    VerifyIn,
)
from cafeops.api.runtime import in_session
from cafeops.api.security import client_ip
from cafeops.services.loyalty.consent import unsubscribe
from cafeops.services.loyalty.errors import LoyaltyError
from cafeops.services.loyalty.recovery import BAD_CODE
from cafeops.services.loyalty.wallets import wallet_module

open_router = APIRouter(prefix="/api/loyalty", tags=["loyalty"])

CardToken = Annotated[str | None, Header(alias="X-Card-Token")]
TokenQuery = Annotated[str | None, Query(alias="t", max_length=64)]


def _token(header: str | None, query: str | None) -> str | None:
    return header or query


def _honeypot(value: str) -> None:
    if value.strip():
        raise LoyaltyError(400, "rejected", "Something went wrong. Please try again.")


@open_router.get("/program", response_model=ProgramOut, summary="The stamp card's rules.")
async def program() -> ProgramOut:
    return await in_session(views.program_view)


@open_router.get(
    "/programs",
    response_model=ProgramsOut,
    summary="Phase 3: every programme open to join (the main card first).",
)
async def programs() -> ProgramsOut:
    return await in_session(views.programs_view)


@open_router.post(
    "/join",
    response_model=JoinResult,
    status_code=status.HTTP_201_CREATED,
    summary="Join: one member, one card. 409 already_member offers recovery.",
)
async def join(body: JoinIn, request: Request, background: BackgroundTasks) -> JoinResult:
    _honeypot(body.website)
    limit("join", client_ip(request))
    result = await in_session(lambda s: views.join_view(s, body))
    background.add_task(kick_wallets)
    return result


@open_router.get("/card/{card_id}", response_model=CardState, summary="The card, for the web card.")
async def card(card_id: str, x_card_token: CardToken = None, t: TokenQuery = None) -> CardState:
    token = _token(x_card_token, t)
    return await in_session(lambda s: views.card_state(s, card_id, token))


@open_router.get(
    "/card/{card_id}/apple.pkpass",
    summary="The Apple Wallet pass. 503 when Apple Wallet is not configured.",
    response_class=Response,
)
async def apple_pass(
    card_id: str, x_card_token: CardToken = None, t: TokenQuery = None
) -> Response:
    token = _token(x_card_token, t)
    view = await in_session(lambda s: views.card_for_wallet(s, card_id, token))
    apple = wallet_module("apple")
    if apple is None or not hasattr(apple, "build_pkpass"):
        return wallet_not_configured_response()
    try:
        data = await asyncio.to_thread(apple.build_pkpass, view)
    except Exception as exc:
        if is_wallet_not_configured(exc):
            return wallet_not_configured_response(None, exc)
        raise
    return Response(
        content=data,
        media_type="application/vnd.apple.pkpass",
        headers={
            "Content-Disposition": 'attachment; filename="sashas-corner.pkpass"',
            "Cache-Control": "no-store",
        },
    )


@open_router.get(
    "/card/{card_id}/google",
    summary="302 to the Google Wallet save link. 503 when Google Wallet is not configured.",
    response_class=RedirectResponse,
    status_code=302,
)
async def google_pass(
    card_id: str, x_card_token: CardToken = None, t: TokenQuery = None
) -> Response:
    token = _token(x_card_token, t)
    view = await in_session(lambda s: views.card_for_wallet(s, card_id, token))
    google = wallet_module("google")
    if google is None or not hasattr(google, "save_url"):
        return wallet_not_configured_response()
    try:
        url = await asyncio.to_thread(google.save_url, view)
    except Exception as exc:
        if is_wallet_not_configured(exc):
            return wallet_not_configured_response(None, exc)
        raise
    return RedirectResponse(url=url, status_code=302)


@open_router.post(
    "/card/{card_id}/programs",
    response_model=JoinResult,
    status_code=status.HTTP_201_CREATED,
    summary="Phase 3: add another programme's card for this member. 409 if they have one.",
)
async def join_program(
    card_id: str,
    body: JoinProgramIn,
    request: Request,
    background: BackgroundTasks,
    x_card_token: CardToken = None,
    t: TokenQuery = None,
) -> JoinResult:
    limit("join", client_ip(request))
    token = _token(x_card_token, t)
    result = await in_session(lambda s: views.join_program_view(s, card_id, token, body.program))
    background.add_task(kick_wallets)
    return result


@open_router.patch(
    "/card/{card_id}/preferences",
    response_model=CardState,
    summary="Marketing consent on or off, from the web card. Recorded with time and source.",
)
async def preferences(
    card_id: str,
    body: PreferencesIn,
    background: BackgroundTasks,
    x_card_token: CardToken = None,
    t: TokenQuery = None,
) -> CardState:
    token = _token(x_card_token, t)
    result = await in_session(
        lambda s: views.preferences_view(s, card_id, token, marketing_opt_in=body.marketing_opt_in)
    )
    background.add_task(kick_wallets)
    return result


@open_router.delete(
    "/card/{card_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete my card: erase the member, void the card and its rewards.",
)
async def delete_card(
    card_id: str,
    background: BackgroundTasks,
    x_card_token: CardToken = None,
    t: TokenQuery = None,
) -> Response:
    token = _token(x_card_token, t)
    await in_session(lambda s: views.delete_card_view(s, card_id, token))
    background.add_task(kick_wallets)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@open_router.post(
    "/recover",
    response_model=RecoverOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send a one-time code, or say to ask staff. Same answer for strangers.",
)
async def recover(body: RecoverIn, request: Request, background: BackgroundTasks) -> RecoverOut:
    _honeypot(body.website)
    limit("recover", client_ip(request))
    out, outgoing = await in_session(lambda s: views.recover_view(s, body.contact))
    if outgoing is not None:
        background.add_task(deliver_after_commit, [outgoing])
    return out


@open_router.post(
    "/recover/verify", response_model=JoinResult, summary="Trade the code for the same card."
)
async def recover_verify(body: VerifyIn, request: Request) -> JoinResult:
    limit("verify", client_ip(request))
    result = await in_session(lambda s: views.verify_view(s, body))
    if result is None:
        # Raised after the commit so the attempt counter sticks (services/loyalty/recovery).
        raise BAD_CODE
    return result


_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · Sasha's Corner</title>
<style>body{{font-family:system-ui,sans-serif;background:#faf7f5;color:#474531;
margin:0;padding:48px 16px}}main{{max-width:28rem;margin:0 auto}}h1{{font-size:1.4rem}}
a{{color:#9b6038}}</style></head><body><main><h1>{title}</h1><p>{body}</p>
<p><a href="/">Sasha's Corner</a></p></main></body></html>"""


@open_router.get(
    "/unsubscribe",
    response_class=HTMLResponse,
    summary="One-click marketing opt-out from an emailed link.",
)
async def unsubscribe_page(
    m: Annotated[int, Query()], s: Annotated[str, Query(max_length=64)]
) -> HTMLResponse:
    ok = await in_session(lambda session: unsubscribe(session, m, s))
    if not ok:
        page = _PAGE.format(
            title="That link did not work",
            body=html.escape(
                "The unsubscribe link is incomplete. Open the web card and switch news off "
                "there, or ask us at the till."
            ),
        )
        return HTMLResponse(page, status_code=400)
    page = _PAGE.format(
        title="You are unsubscribed",
        body=html.escape(
            "We will not send you news or offers any more. Your stamp card keeps working as before."
        ),
    )
    return HTMLResponse(page)


@open_router.post("/unsubscribe", include_in_schema=False)
async def unsubscribe_one_click(
    m: Annotated[int, Query()], s: Annotated[str, Query(max_length=64)]
) -> Response:
    """RFC 8058: mail clients POST to the List-Unsubscribe URL with no page shown."""
    await in_session(lambda session: unsubscribe(session, m, s))
    return Response(status_code=200)
