"""Admin authentication: one owner password, cookie sessions, CSRF header, audit.

- The password is scrypt-hashed in ``site_admin_credential`` (one row). While that
  table is empty, ``SITE_ADMIN_PASSWORD`` bootstraps the first sign-in; once a DB
  hash exists the env var is ignored.
- A session is a random token in the ``sc_admin`` cookie (HttpOnly, SameSite=Strict,
  Path=/api). Only its sha256 is stored, in ``site_admin_session``. 14 days, extended
  on use.
- CSRF: every non-GET ``/api/admin/*`` request must carry ``X-Admin: 1``. A form on
  another site cannot set a custom header, and SameSite=Strict already keeps the
  cookie off cross-site requests; the header is the second lock.
- A 401 never carries ``WWW-Authenticate``: the admin page explains it, the browser
  must not pop its own password dialog.

``require_admin_session`` is the FastAPI dependency every admin route uses.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import logging
import secrets
from typing import Any, Literal

from fastapi import HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from sashasite.config import get_settings
from sashasite.db import (
    SiteAdminAudit,
    SiteAdminCredential,
    SiteAdminSession,
    session_scope,
    utcnow,
)
from sashasite.ratelimit import client_ip

log = logging.getLogger("sashasite.auth")

COOKIE_NAME = "sc_admin"
COOKIE_PATH = "/api"
CSRF_HEADER = "X-Admin"
MIN_PASSWORD_LENGTH = 10
#: Re-issue the cookie and push the expiry out at most this often: a write per
#: request would contend with bookings for SQLite's single writer.
EXTEND_EVERY = dt.timedelta(minutes=10)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# scrypt: N=2^15, r=8, p=1 -> 32 MiB and ~0.1 s per check. The params are written
# into the hash string, so raising them later leaves old hashes verifiable.
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024

PasswordSource = Literal["db", "env"]


# --- password hashing -----------------------------------------------------------


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def hash_password(password: str) -> str:
    """``scrypt$n=32768,r=8,p=1$<salt b64>$<key b64>`` with a fresh 16-byte salt."""
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM,
        dklen=32,
    )
    return f"scrypt$n={_SCRYPT_N},r={_SCRYPT_R},p={_SCRYPT_P}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, params, salt_b64, key_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        p = dict(kv.split("=", 1) for kv in params.split(","))
        n, r, par = int(p["n"]), int(p["r"]), int(p["p"])
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(key_b64)
    except (ValueError, KeyError):
        log.error("site_admin_credential holds an unreadable hash; refusing sign-in")
        return False
    got = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=par,
        maxmem=max(_SCRYPT_MAXMEM, 128 * n * r * 2),
        dklen=len(expected),
    )
    return hmac.compare_digest(got, expected)


# --- credential -----------------------------------------------------------------


class AdminDisabled(Exception):
    """Neither a DB password nor SITE_ADMIN_PASSWORD: nobody can sign in (-> 503)."""


def password_source(session: Session) -> PasswordSource | None:
    if session.get(SiteAdminCredential, 1) is not None:
        return "db"
    return "env" if get_settings().admin_password else None


def check_password(password: str) -> PasswordSource:
    """Returns the source that accepted it; raises PermissionError if wrong and
    AdminDisabled if there is nothing to check against."""
    with session_scope() as session:
        cred = session.get(SiteAdminCredential, 1)
        stored = cred.password_hash if cred is not None else None
    if stored is not None:
        if verify_password(password, stored):
            return "db"
        raise PermissionError
    env = get_settings().admin_password
    if not env:
        raise AdminDisabled
    if hmac.compare_digest(password.encode("utf-8"), env.encode("utf-8")):
        return "env"
    raise PermissionError


def set_password(session: Session, new: str) -> int:
    """Store the new hash and revoke every session. Returns sessions revoked.
    The caller writes the audit row (it knows the ip) in the same transaction."""
    if len(new) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"The new password needs at least {MIN_PASSWORD_LENGTH} characters.")
    cred = session.get(SiteAdminCredential, 1)
    if cred is None:
        session.add(
            SiteAdminCredential(id=1, password_hash=hash_password(new), updated_at=utcnow())
        )
    else:
        cred.password_hash = hash_password(new)
        cred.updated_at = utcnow()
    return revoke_all_sessions(session)


def clear_password(session: Session) -> bool:
    """Drop the DB hash, so SITE_ADMIN_PASSWORD bootstraps again. Revokes sessions."""
    cred = session.get(SiteAdminCredential, 1)
    if cred is None:
        return False
    session.delete(cred)
    revoke_all_sessions(session)
    return True


# --- sessions -------------------------------------------------------------------


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", "replace")).hexdigest()


def _lifetime() -> dt.timedelta:
    return dt.timedelta(days=get_settings().admin_session_days)


def create_session(session: Session, request: Request) -> str:
    """A new session row; returns the raw token (for the cookie, never stored)."""
    now = utcnow()
    session.execute(delete(SiteAdminSession).where(SiteAdminSession.expires_at <= now))
    token = secrets.token_urlsafe(32)
    session.add(
        SiteAdminSession(
            token_sha256=_token_hash(token),
            created_at=now,
            last_used_at=now,
            expires_at=now + _lifetime(),
            ip=client_ip(request)[:64],
            user_agent=(request.headers.get("user-agent") or "")[:200] or None,
        )
    )
    return token


def revoke_all_sessions(session: Session) -> int:
    res = session.execute(delete(SiteAdminSession))
    return int(getattr(res, "rowcount", 0) or 0)


def revoke_token(session: Session, token: str) -> bool:
    """Delete the session this cookie token belongs to. True if there was one."""
    res = session.execute(
        delete(SiteAdminSession).where(SiteAdminSession.token_sha256 == _token_hash(token))
    )
    return bool(getattr(res, "rowcount", 0))


def _secure(request: Request) -> bool:
    return request.url.scheme == "https" or get_settings().cookie_secure


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(_lifetime().total_seconds()),
        path=COOKIE_PATH,
        httponly=True,
        samesite="strict",
        secure=_secure(request),
    )


def clear_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        COOKIE_NAME, path=COOKIE_PATH, httponly=True, samesite="strict", secure=_secure(request)
    )


def current_session_id(request: Request) -> int | None:
    """The session row that authenticated this request (set by the dependency)."""
    sid = getattr(request.state, "admin_session_id", None)
    return sid if isinstance(sid, int) else None


# --- dependencies ---------------------------------------------------------------


def require_csrf(request: Request) -> None:
    """403 on a non-GET request without ``X-Admin: 1``."""
    if request.method not in _SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, detail="Missing the X-Admin: 1 header.")


def _unauthorised() -> HTTPException:
    # Deliberately no WWW-Authenticate header.
    return HTTPException(401, detail="Not signed in.")


SERVICE_KEY_HEADER = "X-Site-Service-Key"


def _service_key_ok(request: Request) -> bool:
    """True when the ops back office forwarded this call with the shared secret.

    The back office checked its own sign-in before forwarding, so the key stands in
    for a cookie session. No key configured means this path is closed."""
    expected = get_settings().service_key
    presented = request.headers.get(SERVICE_KEY_HEADER)
    if not expected or not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def require_admin_session(request: Request, response: Response) -> None:
    """FastAPI dependency for every admin route: CSRF header on writes, then a
    live session cookie (or the back office's service key). Extends the session
    (and re-issues the cookie) on use."""
    require_csrf(request)
    if _service_key_ok(request):
        request.state.admin_session_id = None
        return
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise _unauthorised()
    now = utcnow()
    with session_scope() as session:
        row = session.scalar(
            select(SiteAdminSession).where(SiteAdminSession.token_sha256 == _token_hash(token))
        )
        if row is None:
            raise _unauthorised()
        if row.expires_at <= now:
            session.delete(row)
            session.commit()
            raise _unauthorised()
        request.state.admin_session_id = row.id
        if now - row.last_used_at >= EXTEND_EVERY:
            row.last_used_at = now
            row.expires_at = now + _lifetime()
            extend = True
        else:
            extend = False
    if extend:
        set_session_cookie(response, request, token)


# --- audit ----------------------------------------------------------------------


def audit(
    action: str,
    detail: dict[str, Any] | None = None,
    *,
    request: Request | None = None,
    ip: str | None = None,
    session: Session | None = None,
) -> None:
    """Append one ``site_admin_audit`` row. Pass ``session`` to write it in the
    same transaction as the change it records; otherwise it commits on its own."""
    row = SiteAdminAudit(
        at=utcnow(),
        action=action,
        detail_json=json.dumps(detail or {}, default=str, ensure_ascii=False),
        ip=(ip if ip is not None else (client_ip(request) if request is not None else None)),
    )
    if session is not None:
        session.add(row)
        return
    with session_scope() as s:
        s.add(row)
