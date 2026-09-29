"""The public ordering API: `/api/shop/*` (docs/shop/CONTRACT.md §4).

Open routes -- no back-office password -- served on the public domain through Caddy.
What stands in for auth:

- a signed-in customer is a Rewards card: `X-Card-Token` (the card's `auth_token`, the
  same header the loyalty API uses); a bad token is simply "not signed in", except on
  `/me`, which needs one;
- an order's status page needs the order's own `access_token` (`?t=` or
  `X-Order-Token`); wrong code and wrong token are the same 404;
- placing an order is rate-limited per client IP (20 a minute, 60 an hour, counting
  only orders actually placed and honeypot hits: the café's Wi-Fi is one IP, and a
  409 `price_changed` retry must not lock the whole room out) and carries a honeypot;
  catalogue reads are limited too (60 a minute), which is plenty;
- placing an order needs a signed-in Rewards card unless the owner allows guest orders
  (`shop_settings.guest_orders`); the refusal is 401 `sign_in_required` (§10.J);
- the payment webhook is verified by the provider's own signature scheme.

Thin like every area router: parse, hand the work to a view on a worker thread, return,
and kick the notifications / POS push AFTER the response has gone. Errors are
`{"error": code, "detail": sentence}` (`ShopError` is a `LoyaltyError`; the app's
handler adds any `extra` payload, e.g. the fresh quote on 409 `price_changed`).
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Header, Query, Request, Response, status

from cafeops.api.areas import shop_views as views
from cafeops.api.areas.shop_schemas import (
    CatalogueOut,
    ConfigOut,
    MemberOut,
    MeOrdersOut,
    MeOut,
    MePatchIn,
    OrderOut,
    PlacedOut,
    PlaceOrderIn,
    PushSubscribedOut,
    PushSubscribeIn,
    QuoteIn,
    QuoteOut,
    SlotsOut,
    WebhookOut,
)
from cafeops.api.runtime import in_session
from cafeops.api.security import client_ip
from cafeops.services.auth import RateLimiter
from cafeops.services.shop.errors import ShopError
from cafeops.services.shop.notify import notify_new_order
from cafeops.services.shop.pos import push_order_to_pos

open_router = APIRouter(prefix="/api/shop", tags=["shop"])

CardToken = Annotated[str | None, Header(alias="X-Card-Token")]
OrderTokenHeader = Annotated[str | None, Header(alias="X-Order-Token")]
TokenQuery = Annotated[str | None, Query(alias="t", max_length=64)]

CACHE_30S = {"Cache-Control": "public, max-age=30"}

_LIMITERS: dict[str, RateLimiter] = {
    #: Counted on 201 and on honeypot hits only (`_check` + `_count`), not per attempt.
    "orders": RateLimiter(per_minute=20, per_hour=60),
    "read": RateLimiter(per_minute=60, per_hour=1500),
    "me": RateLimiter(per_minute=30, per_hour=600),
}


def _check(kind: str, ip: str) -> None:
    wait = _LIMITERS[kind].retry_after(ip)
    if wait is not None:
        raise ShopError(429, "rate_limited", f"Too many tries. Wait {wait} seconds.")


def _count(kind: str, ip: str) -> None:
    _LIMITERS[kind].record_failure(ip)


def _limit(kind: str, ip: str) -> None:
    """Check, then count this attempt (reads and `/me`)."""
    _check(kind, ip)
    _count(kind, ip)


def _honeypot(value: str, ip: str) -> None:
    if value.strip():
        _count("orders", ip)
        raise ShopError(400, "rejected", "Something went wrong. Please try again.")


async def _after_new_order(order_id: int) -> None:
    """Owner notification and the POS push, off the request thread."""
    await asyncio.to_thread(notify_new_order, order_id)
    await asyncio.to_thread(push_order_to_pos, order_id)


@open_router.get("/config", response_model=ConfigOut, summary="Is the shop open, and its copy.")
async def config(request: Request, response: Response) -> ConfigOut:
    _limit("read", client_ip(request))
    response.headers.update(CACHE_30S)
    return await in_session(views.config_view)


@open_router.get(
    "/catalogue",
    response_model=CatalogueOut,
    summary="Everything the ordering app renders, with a version to cache on.",
)
async def catalogue(request: Request, response: Response) -> CatalogueOut:
    _limit("read", client_ip(request))
    response.headers.update(CACHE_30S)
    return await in_session(views.catalogue_view)


@open_router.get("/slots", response_model=SlotsOut, summary="Collection slots for a day.")
async def slots(
    request: Request,
    response: Response,
    day: Annotated[date | None, Query(alias="date")] = None,
) -> SlotsOut:
    _limit("read", client_ip(request))
    response.headers.update(CACHE_30S)
    return await in_session(lambda s: views.slots_view(s, day))


@open_router.post("/quote", response_model=QuoteOut, summary="Price a basket. Never writes.")
async def quote(body: QuoteIn, request: Request, x_card_token: CardToken = None) -> QuoteOut:
    _limit("read", client_ip(request))
    return await in_session(lambda s: views.quote_view(s, body, x_card_token))


@open_router.post(
    "/orders",
    response_model=PlacedOut,
    status_code=status.HTTP_201_CREATED,
    summary=(
        "Place an order. 401 sign_in_required without a Rewards card (unless guests may "
        "order); 409 price_changed carries the fresh quote."
    ),
)
async def place_order(
    body: PlaceOrderIn,
    request: Request,
    background: BackgroundTasks,
    x_card_token: CardToken = None,
    user_agent: Annotated[str | None, Header()] = None,
) -> PlacedOut:
    ip = client_ip(request)
    _check("orders", ip)
    _honeypot(body.website, ip)
    placed, order_id = await in_session(
        lambda s: views.place_order_view(s, body, x_card_token, client_ip=ip, user_agent=user_agent)
    )
    _count("orders", ip)
    if placed.status == "NEW":
        background.add_task(_after_new_order, order_id)
    return placed


@open_router.get("/orders/{code}", response_model=OrderOut, summary="The customer's order.")
async def order(
    code: str,
    request: Request,
    x_order_token: OrderTokenHeader = None,
    t: TokenQuery = None,
) -> OrderOut:
    _limit("me", client_ip(request))
    return await in_session(lambda s: views.order_view(s, code, x_order_token or t))


@open_router.post(
    "/orders/{code}/cancel", response_model=OrderOut, summary="Cancel while NEW (customer)."
)
async def cancel(
    code: str,
    request: Request,
    x_order_token: OrderTokenHeader = None,
    t: TokenQuery = None,
) -> OrderOut:
    _limit("me", client_ip(request))
    return await in_session(lambda s: views.cancel_view(s, code, x_order_token or t))


@open_router.post(
    "/orders/{code}/push",
    response_model=PushSubscribedOut,
    status_code=status.HTTP_201_CREATED,
    summary="Subscribe this browser to the order's status by Web Push (§3c).",
)
async def push_subscribe(
    code: str,
    body: PushSubscribeIn,
    request: Request,
    x_order_token: OrderTokenHeader = None,
    t: TokenQuery = None,
) -> PushSubscribedOut:
    _limit("me", client_ip(request))
    return await in_session(lambda s: views.push_subscribe_view(s, code, x_order_token or t, body))


@open_router.delete(
    "/orders/{code}/push",
    response_model=PushSubscribedOut,
    summary="Unsubscribe (one endpoint with ?endpoint=, or all of this order's).",
)
async def push_unsubscribe(
    code: str,
    request: Request,
    x_order_token: OrderTokenHeader = None,
    t: TokenQuery = None,
    endpoint: Annotated[str | None, Query(max_length=500)] = None,
) -> PushSubscribedOut:
    _limit("me", client_ip(request))
    return await in_session(
        lambda s: views.push_unsubscribe_view(s, code, x_order_token or t, endpoint)
    )


@open_router.get("/me", response_model=MeOut, summary="The signed-in member, for checkout.")
async def me(
    request: Request,
    x_card_token: CardToken = None,
    card_id: Annotated[str | None, Query(max_length=36)] = None,
) -> MeOut:
    _limit("me", client_ip(request))
    return await in_session(lambda s: views.me_view(s, x_card_token, card_id))


@open_router.get(
    "/me/orders",
    response_model=MeOrdersOut,
    summary="The signed-in member's orders, newest first, paged (§10.J).",
)
async def me_orders(
    request: Request,
    x_card_token: CardToken = None,
    card_id: Annotated[str | None, Query(max_length=36)] = None,
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 10,
) -> MeOrdersOut:
    _limit("me", client_ip(request))
    return await in_session(
        lambda s: views.me_orders_view(s, x_card_token, card_id, page=page, page_size=page_size)
    )


@open_router.patch(
    "/me",
    response_model=MemberOut,
    summary=(
        "Edit my profile: name, email, phone, birthday, news and offers. Any subset; "
        "loyalty's own rules apply (contact uniqueness, one contact kept)."
    ),
)
async def me_patch(
    body: MePatchIn,
    request: Request,
    x_card_token: CardToken = None,
    card_id: Annotated[str | None, Query(max_length=36)] = None,
) -> MemberOut:
    _limit("me", client_ip(request))
    return await in_session(lambda s: views.me_patch_view(s, x_card_token, card_id, body))


@open_router.post(
    "/payments/{provider_key}/webhook",
    response_model=WebhookOut,
    summary="The payment provider's webhook (raw body + its signature header).",
)
async def payment_webhook(
    provider_key: str, request: Request, background: BackgroundTasks
) -> WebhookOut:
    body = await request.body()
    headers = dict(request.headers.items())
    out, order_id, became_new = await in_session(
        lambda s: views.webhook_view(s, provider_key, headers, body)
    )
    if became_new and order_id is not None:
        background.add_task(_after_new_order, order_id)
    return out


@open_router.post("/stripe/webhook", response_model=WebhookOut, include_in_schema=False)
async def stripe_webhook_alias(request: Request, background: BackgroundTasks) -> WebhookOut:
    """The contract's original path (§4); same handler as `/payments/stripe/webhook`."""
    return await payment_webhook("stripe", request, background)
