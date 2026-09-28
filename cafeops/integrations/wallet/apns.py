"""APNs pushes for Wallet passes: "this pass changed, come and fetch it".

A pass push carries no content -- the payload is `{}` and the topic is the Pass Type ID.
The device then calls our web service for the serials that changed and downloads each
pass; any lock-screen text comes from the pass's own `changeMessage` (see `apple.py`).

Authenticated with the Pass Type ID certificate itself (TLS client auth), which is the
only APNs credential a pass needs -- no .p8 key, no app. HTTP/2 is mandatory for APNs,
hence httpx's `h2` extra. No `apns-push-type` header: pass updates predate it and Apple's
Wallet guide does not set one; iOS treats its absence as the default.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass

import httpx

from cafeops.integrations.wallet.config import (
    WalletNotConfigured,
    WalletSettings,
    wallet_settings,
)

PRODUCTION = "https://api.push.apple.com"
SANDBOX = "https://api.sandbox.push.apple.com"


@dataclass(frozen=True)
class PushResult:
    token: str
    status: int
    reason: str | None

    @property
    def ok(self) -> bool:
        return self.status == 200

    @property
    def token_dead(self) -> bool:
        """The device no longer holds the pass (410) or the token is garbage (400
        BadDeviceToken) -- either way the registration should go."""
        return self.status == 410 or (self.status == 400 and self.reason == "BadDeviceToken")


def _ssl_context(cfg: WalletSettings) -> ssl.SSLContext:
    assert cfg.apple_cert_path and cfg.apple_key_path
    ctx = ssl.create_default_context()
    ctx.load_cert_chain(
        certfile=str(cfg.apple_cert_path),
        keyfile=str(cfg.apple_key_path),
        password=cfg.apple_key_password,
    )
    return ctx


def push(tokens: list[str], *, cfg: WalletSettings | None = None) -> list[PushResult]:
    """Send the empty update push to each token. Raises `WalletNotConfigured`;
    transport failures raise `httpx.HTTPError` for the caller's backoff."""
    cfg = cfg or wallet_settings
    if not cfg.apple_configured:
        raise WalletNotConfigured("apple not configured")
    if not tokens:
        return []
    base = SANDBOX if cfg.apple_apns_use_sandbox else PRODUCTION
    results: list[PushResult] = []
    with httpx.Client(http2=True, verify=_ssl_context(cfg), base_url=base, timeout=10.0) as client:
        for token in tokens:
            resp = client.post(
                f"/3/device/{token}",
                headers={"apns-topic": str(cfg.apple_pass_type_id)},
                content=b"{}",
            )
            reason = None
            if resp.status_code != 200:
                try:
                    reason = resp.json().get("reason")
                except ValueError:
                    reason = resp.text[:100] or None
            results.append(PushResult(token=token, status=resp.status_code, reason=reason))
    return results


__all__ = ["PushResult", "push"]
