"""One shared password, exchanged for revocable sessions. DECISIONS.md 3.

Still ONE shared password -- spec 1: no user management. What changed in the
2026-09 redesign is where the password lives and what travels on each request:

* **Where it lives.** `auth_credential` (a scrypt hash, set from Settings) wins; with
  no row, `CAFEOPS_API_PASSWORD` is the bootstrap. `services/auth.py` owns the rule.
* **What the browser sends.** `POST /api/auth/session {password}` returns a random
  token; the browser sends `Authorization: Bearer <token>`. Only the token's sha256
  is stored, and a password change revokes every session.
* **What tools send.** `X-API-Key: <password>` is still accepted: `cafeops
  api-fixtures` (api/examples.py) and anyone with curl present the password that
  way, and requiring them to juggle a session would buy nothing -- they hold the
  password already. Failures on that header count against the same per-IP limiter
  as sign-in, so it is not a way around guess limiting.
* **What is no longer accepted:** the raw password as `Authorization: Bearer`. A
  bearer value is a session token or nothing. Nothing in the repo sent the password
  that way (the pre-v2 frontend used `X-API-Key`), and keeping one header meaning one
  thing is what lets a revoked browser session actually be revoked.

**It fails closed.** With neither a stored credential nor the env var, every route
but `/api/health` and sign-in answers 503 and says what to set.

**Client IP** for the limiter comes from `X-Forwarded-For` only when the direct peer
is a trusted proxy (`CAFEOPS_TRUSTED_PROXIES`, loopback by default -- Caddy on the same
box). Caddy replaces any client-supplied `X-Forwarded-For` unless *it* is configured
to trust an upstream, so the rightmost entry is the address Caddy saw.
"""

from __future__ import annotations

import ipaddress
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from cafeops.db.base import session_scope
from cafeops.db.models import AuthEvent
from cafeops.services import auth as auth_service

__all__ = [
    "ApiAuth",
    "OptionalApiAuth",
    "api_password",
    "client_ip",
    "presented_password_is_valid",
    "require_password",
]

_UNSET_MESSAGE = (
    "No back-office password is configured, so this API is refusing to serve rather "
    "than serving the whole business unauthenticated. Set CAFEOPS_API_PASSWORD in .env "
    "or the environment and restart. Spec 1: a single shared password."
)


def api_password() -> str | None:
    """The ENV password (the bootstrap), read fresh each call.

    Kept for the CLI (`cafeops serve`'s warning, `cafeops api-fixtures`' default). It
    is not necessarily the password in force: a password set from Settings is stored
    in `auth_credential` and wins. Use `services.auth.credential_source` for that.
    """
    return auth_service.auth_settings().api_password or None


def _trusted(peer: str) -> bool:
    raw = auth_service.auth_settings().trusted_proxies
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        try:
            if addr in ipaddress.ip_network(token, strict=False):
                return True
        except ValueError:
            continue
    return False


def client_ip(request: Request) -> str:
    """The address to rate-limit: the peer, or Caddy's X-Forwarded-For if the peer is Caddy."""
    peer = request.client.host if request.client else "unknown"
    if _trusted(peer):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            last = forwarded.split(",")[-1].strip()
            if last:
                return last
    return peer


def _bearer(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        return token or None
    return None


def require_password(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Accept a live session token (Bearer) or the shared password (X-API-Key).

    Sync on purpose: FastAPI runs it on a worker thread, which is where DB work goes.
    On success `request.state.auth_session_id` holds the session id (None for the
    password header), for routes that audit who did what.
    """
    token = _bearer(authorization)
    ip = client_ip(request)
    with session_scope() as session:
        if not auth_service.auth_is_configured(session):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_UNSET_MESSAGE
            )

        if token is not None:
            row = auth_service.resolve_session(session, token)
            if row is not None:
                request.state.auth_session_id = row.id
                return
            if x_api_key is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="This session has ended (signed out, expired, or the password "
                    "was changed). Sign in again.",
                    headers={"WWW-Authenticate": "Bearer"},
                )

        if x_api_key is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "no credential presented. Sign in with POST /api/auth/session and send "
                    "'Authorization: Bearer <token>', or send the shared password as "
                    "'X-API-Key: <password>'."
                ),
                headers={"WWW-Authenticate": "Bearer"},
            )

        wait = auth_service.login_limiter.retry_after(ip)
        if wait is not None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many wrong passwords from this address. Wait a minute.",
                headers={"Retry-After": str(wait)},
            )
        if not auth_service.verify_password(session, x_api_key):
            auth_service.login_limiter.record_failure(ip)
            auth_service.record_audit(
                session, AuthEvent.LOGIN_FAILED, via="api_key", client_ip=ip, detail="X-API-Key"
            )
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="wrong password.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        request.state.auth_session_id = None


#: Attach to a router to protect every route on it.
ApiAuth = Depends(require_password)


def presented_password_is_valid(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> bool:
    """Did this caller present a valid session or password? Never rejects.

    For `/api/health`, which must answer before anyone has the password but should
    tell a stranger less. Does not feed the rate limiter (it never refuses), and a
    wrong X-API-Key here costs a scrypt at most once per request -- health is not a
    guessing oracle worth more than sign-in, which IS limited.
    """
    token = _bearer(authorization)
    with session_scope() as session:
        if not auth_service.auth_is_configured(session):
            return False
        if token is not None and auth_service.resolve_session(session, token) is not None:
            return True
        if x_api_key:
            ip = client_ip(request)
            if auth_service.login_limiter.retry_after(ip) is not None:
                return False
            ok = auth_service.verify_password(session, x_api_key)
            if not ok:
                auth_service.login_limiter.record_failure(ip)
            return ok
    return False


#: For an open route that discloses more to a caller who belongs here.
OptionalApiAuth = Depends(presented_password_is_valid)
