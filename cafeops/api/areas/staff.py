"""The staff scanner API (CONTRACT §5), served on the public domain at `/api/staff/*`.

Open to the internet by necessity -- the scanner is a page on the café's own site -- and
guarded instead by two keys (`services/loyalty/staff_auth.py`): `X-Staff-Device` on every
call (a registered, unrevoked device) and `Authorization: Bearer <session>` on all but
pairing and login (a PIN login on THAT device, under 12 hours old).

After a stamp the wallet drain and the alert sender run as background tasks, so the till
answers in one database transaction and never waits on Apple, Google or Telegram (SPEC:
"failure never blocks the till").
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, Query, Request, status
from fastapi.responses import Response

from cafeops.api.areas import staff_views as views
from cafeops.api.areas.loyalty_edge import kick_wallets, send_pending_alerts
from cafeops.api.areas.staff_schemas import (
    AddCardIn,
    CardOut,
    DrinksOut,
    LinkPosIn,
    LoginIn,
    LoginOut,
    LookupOut,
    MeOut,
    MigrateIn,
    PairIn,
    PairOut,
    PosCustomersOut,
    RecoveryLinkIn,
    RecoveryLinkOut,
    RedeemIn,
    RedeemOut,
    ScanIn,
    ScanResult,
    SpendIn,
    StampIn,
    StampOut,
    UndoIn,
)
from cafeops.api.runtime import in_session
from cafeops.api.security import client_ip
from cafeops.services.loyalty.staff_auth import pair_device

open_router = APIRouter(prefix="/api/staff", tags=["staff scanner"])

Device = Annotated[str | None, Header(alias="X-Staff-Device")]
Authorization = Annotated[str | None, Header()]


def _bearer(value: str | None) -> str | None:
    if value and value.lower().startswith("bearer "):
        return value[7:].strip() or None
    return None


@open_router.post("/device/pair", response_model=PairOut, summary="Pairing code -> device token.")
async def pair(body: PairIn, request: Request) -> PairOut:
    ip = client_ip(request)
    token = await in_session(
        lambda s: pair_device(
            s, pairing_code=body.pairing_code, device_name=body.device_name, client_ip=ip
        )
    )
    return PairOut(device_token=token)


@open_router.post("/login", response_model=LoginOut, summary="PIN -> 12-hour session.")
async def login(body: LoginIn, x_staff_device: Device = None) -> LoginOut:
    return await in_session(lambda s: views.login_view(s, x_staff_device, body.pin))


@open_router.post(
    "/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response, summary="End it."
)
async def logout(x_staff_device: Device = None, authorization: Authorization = None) -> Response:
    bearer = _bearer(authorization)
    await in_session(lambda s: views.logout_view(s, x_staff_device, bearer))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@open_router.get("/me", response_model=MeOut, summary="Who is logged in on this device.")
async def me(x_staff_device: Device = None, authorization: Authorization = None) -> MeOut:
    bearer = _bearer(authorization)
    return await in_session(lambda s: views.me_view(s, x_staff_device, bearer))


@open_router.post("/scan", response_model=ScanResult, summary="Read a card from its QR.")
async def scan(
    body: ScanIn, x_staff_device: Device = None, authorization: Authorization = None
) -> ScanResult:
    bearer = _bearer(authorization)
    return await in_session(lambda s: views.scan_view_(s, x_staff_device, bearer, body.payload))


@open_router.post(
    "/stamp",
    response_model=StampOut,
    summary="+1..3 stamps. 409 manager_pin_required past the 10-minute cooldown.",
)
async def stamp(
    body: StampIn,
    background: BackgroundTasks,
    x_staff_device: Device = None,
    authorization: Authorization = None,
) -> StampOut:
    bearer = _bearer(authorization)
    out, alerts = await in_session(
        lambda s: views.stamp_view(
            s,
            x_staff_device,
            bearer,
            card_id=body.card_id,
            delta=body.delta,
            manager_pin=body.manager_pin,
        )
    )
    background.add_task(kick_wallets)
    if alerts:
        background.add_task(send_pending_alerts)
    return out


@open_router.post(
    "/redeem",
    response_model=RedeemOut,
    summary="Give the free drink. With menu_item_id, a £0 sale keeps stock honest.",
)
async def redeem(
    body: RedeemIn,
    background: BackgroundTasks,
    x_staff_device: Device = None,
    authorization: Authorization = None,
) -> RedeemOut:
    bearer = _bearer(authorization)
    out = await in_session(
        lambda s: views.redeem_view(
            s,
            x_staff_device,
            bearer,
            reward_id=body.reward_id,
            menu_item_id=body.menu_item_id,
            option_id=body.option_id,
        )
    )
    background.add_task(kick_wallets)
    return out


@open_router.post(
    "/migrate", response_model=StampOut, summary="Convert a paper card (1-7 stickers), once."
)
async def migrate(
    body: MigrateIn,
    background: BackgroundTasks,
    x_staff_device: Device = None,
    authorization: Authorization = None,
) -> StampOut:
    bearer = _bearer(authorization)
    out = await in_session(
        lambda s: views.migrate_view(
            s, x_staff_device, bearer, card_id=body.card_id, paper_stamps=body.paper_stamps
        )
    )
    background.add_task(kick_wallets)
    return out


@open_router.post(
    "/undo", response_model=CardOut, summary="Undo a stamp or a free drink within 2 minutes."
)
async def undo(
    body: UndoIn,
    background: BackgroundTasks,
    x_staff_device: Device = None,
    authorization: Authorization = None,
) -> CardOut:
    bearer = _bearer(authorization)
    out = await in_session(
        lambda s: views.undo_view(
            s, x_staff_device, bearer, event_id=body.event_id, reward_id=body.reward_id
        )
    )
    background.add_task(kick_wallets)
    return out


@open_router.get(
    "/drinks",
    response_model=DrinksOut,
    summary="Drinks a free drink can be. Phase 3: ?reward_id= / ?option_id= narrow it.",
)
async def drinks(
    reward_id: Annotated[int | None, Query()] = None,
    option_id: Annotated[int | None, Query()] = None,
    x_staff_device: Device = None,
    authorization: Authorization = None,
) -> DrinksOut:
    bearer = _bearer(authorization)
    return await in_session(
        lambda s: views.drinks_view(
            s, x_staff_device, bearer, reward_id=reward_id, option_id=option_id
        )
    )


@open_router.post(
    "/spend",
    response_model=StampOut,
    summary="Phase 3: points for a spend (points cards). 409 manager_pin_required over £50.",
)
async def spend(
    body: SpendIn,
    background: BackgroundTasks,
    x_staff_device: Device = None,
    authorization: Authorization = None,
) -> StampOut:
    bearer = _bearer(authorization)
    out = await in_session(
        lambda s: views.spend_view(
            s,
            x_staff_device,
            bearer,
            card_id=body.card_id,
            spend_pence=body.spend_pence,
            manager_pin=body.manager_pin,
        )
    )
    background.add_task(kick_wallets)
    return out


@open_router.post(
    "/add-card",
    response_model=CardOut,
    summary="Phase 3: give the member a card in another programme.",
)
async def add_card(
    body: AddCardIn,
    background: BackgroundTasks,
    x_staff_device: Device = None,
    authorization: Authorization = None,
) -> CardOut:
    bearer = _bearer(authorization)
    out = await in_session(
        lambda s: views.add_card_view(
            s, x_staff_device, bearer, card_id=body.card_id, program=body.program
        )
    )
    background.add_task(kick_wallets)
    return out


@open_router.get(
    "/pos-customers",
    response_model=PosCustomersOut,
    summary="Phase 3: till customers on recent receipts not yet linked to a member.",
)
async def pos_customers(
    x_staff_device: Device = None, authorization: Authorization = None
) -> PosCustomersOut:
    bearer = _bearer(authorization)
    return await in_session(lambda s: views.pos_customers_view(s, x_staff_device, bearer))


@open_router.post(
    "/link-pos",
    response_model=CardOut,
    summary="Phase 3: link the member to a Lightspeed customer (by id or receipt number).",
)
async def link_pos(
    body: LinkPosIn,
    background: BackgroundTasks,
    x_staff_device: Device = None,
    authorization: Authorization = None,
) -> CardOut:
    bearer = _bearer(authorization)
    out = await in_session(
        lambda s: views.link_pos_view(
            s,
            x_staff_device,
            bearer,
            card_id=body.card_id,
            customer_id=body.customer_id,
            receipt_id=body.receipt_id,
        )
    )
    background.add_task(kick_wallets)
    return out


@open_router.get(
    "/lookup", response_model=LookupOut, summary="Find a member by phone, email, name."
)
async def lookup(
    q: Annotated[str, Query(max_length=254)],
    x_staff_device: Device = None,
    authorization: Authorization = None,
) -> LookupOut:
    bearer = _bearer(authorization)
    return await in_session(lambda s: views.lookup_view(s, x_staff_device, bearer, q))


@open_router.post(
    "/recovery-link",
    response_model=RecoveryLinkOut,
    summary="The web-card link, shown as a QR for the customer. Logged.",
)
async def recovery_link(
    body: RecoveryLinkIn, x_staff_device: Device = None, authorization: Authorization = None
) -> RecoveryLinkOut:
    bearer = _bearer(authorization)
    return await in_session(
        lambda s: views.recovery_link_view(s, x_staff_device, bearer, body.card_id)
    )
