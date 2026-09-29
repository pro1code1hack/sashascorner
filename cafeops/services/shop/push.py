"""Web Push for order status (CONTRACT §3c): VAPID keys and one `send`.

`pywebpush` does the encryption and the POST; this module owns what a failure means:
a 404 or 410 from the push service is a dead subscription (the browser unsubscribed or
the endpoint rotated) and is reported as `expired=True` so the caller marks it; any
other failure is a reason string, logged and put on the order's event.

**Only real push services are ever POSTed to.** The endpoint comes from a browser on a
public route, so without a host check this module is a server-side request forger: any
`https://` URL a caller submits -- an internal admin page, a cloud metadata address --
would be fetched by the server whenever that order's status changes. `endpoint_refusal`
is the allow-list; the schema checks it on the way in, `subscribe` checks it again, and
`send_push` checks it a third time before the POST (a row written before the check
existed must not slip through).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from cafeops.clock import utcnow
from cafeops.config import settings
from cafeops.db.models import ShopOrder, ShopPushSubscription
from cafeops.services.shop.errors import ShopError

__all__ = [
    "PushOutcome",
    "endpoint_refusal",
    "generate_vapid_keys",
    "live_subscription_count",
    "push_configured",
    "send_push",
    "subscribe",
    "unsubscribe",
]

log = logging.getLogger("cafeops.shop.push")


@dataclass(frozen=True, slots=True)
class PushOutcome:
    ok: bool
    expired: bool
    detail: str


#: The push services browsers actually hand out endpoints for: Chrome/Edge-on-Android
#: (FCM), Safari (Apple), Firefox (Mozilla autopush) and Edge on Windows (WNS). A new
#: browser vendor means a new line here, deliberately.
_EXACT_HOSTS = frozenset({"fcm.googleapis.com", "updates.push.services.mozilla.com"})
_HOST_PATTERNS = (
    re.compile(r"^(?:[a-z0-9-]+\.)*push\.apple\.com$"),
    re.compile(r"^(?:[a-z0-9-]+\.)*notify\.windows\.com$"),  # includes wns2-*.notify...
)


def endpoint_refusal(endpoint: str) -> str | None:
    """Why this endpoint may not be stored or POSTed to, or None if it may.

    https only, no userinfo, no explicit port other than 443, and a host on the
    allow-list above. The host is matched after `urlsplit`, so
    `https://fcm.googleapis.com@evil.example/` is `evil.example` and is refused.
    """
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        return "The push endpoint is not a valid URL."
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https" or not host:
        return "The push endpoint must be an https URL."
    if parts.username is not None or parts.password is not None:
        return "The push endpoint must not carry credentials."
    if port not in (None, 443):
        return "The push endpoint must use the standard https port."
    if host in _EXACT_HOSTS or any(p.match(host) for p in _HOST_PATTERNS):
        return None
    return "The push endpoint is not a recognised browser push service."


def push_configured() -> bool:
    return settings.push_configured


def generate_vapid_keys() -> tuple[str, str]:
    """A fresh (public, private) pair, base64url, as `.env` and the browser want them."""
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid, b64urlencode

    vapid = Vapid()
    vapid.generate_keys()
    public = b64urlencode(
        vapid.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )
    private = b64urlencode(vapid.private_key.private_numbers().private_value.to_bytes(32, "big"))
    return str(public), str(private)


def send_push(*, endpoint: str, p256dh: str, auth: str, payload: dict[str, Any]) -> PushOutcome:
    if not settings.push_configured:
        return PushOutcome(False, False, "VAPID keys not configured")
    refusal = endpoint_refusal(endpoint)
    if refusal is not None:
        log.warning("push not sent to disallowed endpoint host %r", urlsplit(endpoint).hostname)
        return PushOutcome(False, False, f"not sent: {refusal}")
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
            data=json.dumps(payload),
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_subject},
            ttl=3600,
            timeout=10,
        )
    except WebPushException as exc:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if status in (404, 410):
            return PushOutcome(False, True, f"subscription gone (HTTP {status})")
        return PushOutcome(False, False, f"push service refused: {str(exc)[:160]}")
    except Exception as exc:  # network, bad key material: a reason, never a crash
        return PushOutcome(False, False, f"{type(exc).__name__}: {str(exc)[:160]}")
    return PushOutcome(True, False, "sent")


# --------------------------------------------------------------------------
# subscriptions: the only writes to shop_push_subscription outside the notifier
# --------------------------------------------------------------------------


def live_subscription_count(session: Session, order: ShopOrder) -> int:
    ids = session.scalars(
        select(ShopPushSubscription.id).where(
            ShopPushSubscription.order_id == order.id, ShopPushSubscription.expired_at.is_(None)
        )
    )
    return len(list(ids))


def subscribe(
    session: Session, order: ShopOrder, *, endpoint: str, p256dh: str, auth: str
) -> ShopPushSubscription:
    """Subscribe `endpoint` to `order`, or refresh the keys of (and revive) an existing row.

    Refuses a non-push-service endpoint with a 422 `ShopError` (see `endpoint_refusal`).
    """
    refusal = endpoint_refusal(endpoint)
    if refusal is not None:
        raise ShopError(422, "invalid_request", refusal)
    existing = session.scalar(
        select(ShopPushSubscription).where(
            ShopPushSubscription.order_id == order.id,
            ShopPushSubscription.endpoint == endpoint,
        )
    )
    if existing is None:
        existing = ShopPushSubscription(
            order_id=order.id, endpoint=endpoint, p256dh=p256dh, auth=auth, created_at=utcnow()
        )
        session.add(existing)
    else:
        existing.p256dh, existing.auth, existing.expired_at = p256dh, auth, None
    session.flush()
    return existing


def unsubscribe(session: Session, order: ShopOrder, *, endpoint: str | None = None) -> int:
    """Expire one endpoint's subscription to `order`, or all of them when `endpoint` is
    None. Returns how many were expired."""
    now = utcnow()
    n = 0
    for sub in session.scalars(
        select(ShopPushSubscription).where(
            ShopPushSubscription.order_id == order.id, ShopPushSubscription.expired_at.is_(None)
        )
    ):
        if endpoint is None or sub.endpoint == endpoint:
            sub.expired_at = now
            n += 1
    session.flush()
    return n
