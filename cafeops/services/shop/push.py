"""Web Push for order status (CONTRACT §3c): VAPID keys and one `send`.

`pywebpush` does the encryption and the POST; this module owns what a failure means:
a 404 or 410 from the push service is a dead subscription (the browser unsubscribed or
the endpoint rotated) and is reported as `expired=True` so the caller marks it; any
other failure is a reason string, logged and put on the order's event.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from cafeops.config import settings

__all__ = ["PushOutcome", "generate_vapid_keys", "push_configured", "send_push"]

log = logging.getLogger("cafeops.shop.push")


@dataclass(frozen=True, slots=True)
class PushOutcome:
    ok: bool
    expired: bool
    detail: str


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
