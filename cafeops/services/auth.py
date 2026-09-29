"""The shared back-office password, its sessions and its audit. DECISIONS.md 3.

Still ONE shared password (CLAUDE.md §1: no user management). What this module adds
over the pre-v2 "compare the header to an env var" is the four things a password that
can be *changed at runtime* needs:

1. **Storage as a hash.** `auth_credential` holds `scrypt$n$r$p$salt$hash`. The
   plaintext is never stored, logged or echoed back.
2. **One explicit precedence.** If an `auth_credential` row exists it is THE password.
   Otherwise `CAFEOPS_API_PASSWORD` is the bootstrap. `credential_source()` says which
   is in force, so nobody has to guess why the old password stopped working.
   Break-glass is deleting the row (`reset_password`), after which the env var rules
   again -- shell access to the box is already full trust.
3. **Sessions, so a change can revoke.** Sign-in trades the password for a random
   bearer token; only `sha256(token)` is stored. A password change revokes every
   session (rows are kept, `revoked_at` set) and issues a fresh one to the caller.
4. **Guess limiting.** An in-process sliding window per client IP: 5 failures a
   minute, 30 an hour. One process on one box (CLAUDE.md §3) makes process memory
   the right home for it; a table would hand every guess the single SQLite writer.

The KDF runs at sign-in and on the raw-password (`X-API-Key`) path only. A successful
verification is remembered for 60 seconds under an HMAC of the presented value plus
the credential's fingerprint, so a CLI tool does not pay scrypt per request and a
password change invalidates the cache by construction.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from cafeops.config import REPO_ROOT
from cafeops.db.models import AuthAudit, AuthCredential, AuthEvent, AuthSession

__all__ = [
    "MIN_PASSWORD_LENGTH",
    "SESSION_ABSOLUTE",
    "SESSION_IDLE",
    "AuthSettings",
    "IssuedSession",
    "PasswordChangeRefused",
    "PasswordChanged",
    "RateLimiter",
    "SignInResult",
    "active_session_count",
    "auth_is_configured",
    "auth_settings",
    "change_password",
    "credential_source",
    "hash_password",
    "issue_session",
    "login_limiter",
    "record_audit",
    "reset_password",
    "resolve_session",
    "revoke_session",
    "sign_in",
    "verify_hash",
    "verify_password",
]

MIN_PASSWORD_LENGTH = 10
SESSION_ABSOLUTE = timedelta(days=30)
SESSION_IDLE = timedelta(days=7)
#: `last_seen_at` is refreshed at most this often: a write per request would put every
#: page load on the single SQLite writer for no information worth having.
_TOUCH_EVERY = timedelta(minutes=10)

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32

#: Refused as a new password whatever its length. Compared case- and space-insensitively.
_DENYLIST = frozenset(
    {
        "password",
        "password1",
        "corner",
        "sashas",
        "sasha",
        "sashascorner",
        "sasha'scorner",
        "sashas corner",
        "commercialstreet",
        "23commercialstreet",
        "cafeops",
    }
)


class AuthSettings(BaseSettings):
    """The auth-only knobs, read from the same `.env` as `config.Settings`.

    Kept here rather than on the shared `Settings` so this module can be changed
    without touching a file every area edits. Read fresh per call (a file stat), so a
    changed `.env` is noticed without a restart -- the same rule `security.py` had.
    """

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="CAFEOPS_",
    )

    api_password: str | None = None
    #: Peers whose `X-Forwarded-For` is believed: comma-separated IPs or CIDRs. Loopback
    #: covers the systemd deployment (Caddy on the same box). Under docker compose set
    #: it to the compose network (e.g. `172.16.0.0/12`), or every client shares Caddy's
    #: address and one person's typos rate-limit everyone.
    trusted_proxies: str = "127.0.0.1,::1"
    login_failures_per_minute: int = 5
    login_failures_per_hour: int = 30


def auth_settings() -> AuthSettings:
    return AuthSettings()


# --------------------------------------------------------------------------
# hashing
# --------------------------------------------------------------------------


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_hash(password: str, stored: str) -> bool:
    """Constant-time check of `password` against a stored `scrypt$...` string."""
    try:
        algo, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


# --------------------------------------------------------------------------
# which password is in force
# --------------------------------------------------------------------------

CredentialSource = Literal["database", "environment"]


def _stored(session: Session) -> AuthCredential | None:
    return session.get(AuthCredential, 1)


def credential_source(session: Session) -> CredentialSource | None:
    """`database` if a row exists (it wins), else `environment` if the env var is set."""
    if _stored(session) is not None:
        return "database"
    if auth_settings().api_password:
        return "environment"
    return None


def auth_is_configured(session: Session) -> bool:
    return credential_source(session) is not None


#: Per-process secret for the verification cache's keys. Never leaves memory.
_CACHE_KEY = secrets.token_bytes(32)
_CACHE_TTL_SECONDS = 60.0
_cache: dict[str, float] = {}
_cache_lock = threading.Lock()


def _cache_key(presented: str, fingerprint: str) -> str:
    msg = f"{fingerprint}\x00{presented}".encode()
    return hmac.new(_CACHE_KEY, msg, hashlib.sha256).hexdigest()


def verify_password(session: Session, presented: str) -> bool:
    """Is `presented` the password in force? Precedence: DB row, then env var."""
    row = _stored(session)
    if row is not None:
        fingerprint = f"db:{row.password_hash}"
    else:
        env = auth_settings().api_password
        if not env:
            return False
        fingerprint = "env:" + hashlib.sha256(env.encode("utf-8")).hexdigest()

    key = _cache_key(presented, fingerprint)
    now = time.monotonic()
    with _cache_lock:
        expires = _cache.get(key)
        if expires is not None and expires > now:
            return True

    if row is not None:
        ok = verify_hash(presented, row.password_hash)
    else:
        env = auth_settings().api_password or ""
        ok = secrets.compare_digest(presented.encode("utf-8"), env.encode("utf-8"))

    if ok:
        with _cache_lock:
            # Sweep while here; the dict never holds more than a handful of entries.
            for k in [k for k, v in _cache.items() if v <= now]:
                del _cache[k]
            _cache[key] = now + _CACHE_TTL_SECONDS
    return ok


def _forget_cached_verifications() -> None:
    with _cache_lock:
        _cache.clear()


# --------------------------------------------------------------------------
# rate limiting
# --------------------------------------------------------------------------


class RateLimiter:
    """Sliding window of failures per key. In-process by design (see module doc).

    Bounded: a key whose failures have all aged out of the hour is dropped, and so is
    an announcement older than a minute. Keyed by client IP, the maps would otherwise
    grow by one entry per address that ever mistyped a password, for the life of the
    process. A full sweep runs at most once a minute (`_sweep`).
    """

    _SWEEP_EVERY = 60.0

    def __init__(self, *, per_minute: int, per_hour: int) -> None:
        self.per_minute = per_minute
        self.per_hour = per_hour
        self._fails: dict[str, deque[float]] = {}
        #: Keys whose "rate limited" audit row has been written for the current lockout,
        #: so a flood of blocked attempts writes one row rather than one per attempt.
        self._announced: dict[str, float] = {}
        self._lock = threading.Lock()
        self._swept_at = time.monotonic()

    def _prune(self, key: str, now: float) -> deque[float]:
        """The key's failures inside the hour. An emptied key is removed from the map
        (the returned deque is then a detached empty one, safe to append to via
        `record_failure`, which re-inserts it)."""
        self._sweep(now)
        q = self._fails.get(key)
        if q is None:
            return deque()
        while q and q[0] <= now - 3600:
            q.popleft()
        if not q:
            del self._fails[key]
        return q

    def _sweep(self, now: float) -> None:
        if now - self._swept_at < self._SWEEP_EVERY:
            return
        self._swept_at = now
        for key in [k for k, q in self._fails.items() if not q or q[-1] <= now - 3600]:
            del self._fails[key]
        for key in [k for k, at in self._announced.items() if at <= now - 60]:
            del self._announced[key]

    def retry_after(self, key: str) -> int | None:
        """Seconds until another attempt is allowed, or None when one is allowed now."""
        now = time.monotonic()
        with self._lock:
            q = self._prune(key, now)
            recent = [t for t in q if t > now - 60]
            if len(recent) >= self.per_minute:
                return max(1, int(recent[-self.per_minute] + 60 - now) + 1)
            if len(q) >= self.per_hour:
                return max(1, int(q[-self.per_hour] + 3600 - now) + 1)
            return None

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._prune(key, now)
            q.append(now)
            self._fails[key] = q

    def first_block(self, key: str) -> bool:
        """True the first time a lockout is observed for `key` (for a single audit row)."""
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            last = self._announced.get(key)
            if last is not None and last > now - 60:
                return False
            self._announced[key] = now
            return True


def _limiter_from_settings() -> RateLimiter:
    s = auth_settings()
    return RateLimiter(per_minute=s.login_failures_per_minute, per_hour=s.login_failures_per_hour)


#: The process-wide limiter: sign-in, raw-password header failures and password
#: change all draw on it, keyed by client IP.
login_limiter = _limiter_from_settings()


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------


def record_audit(
    session: Session,
    event: AuthEvent,
    *,
    via: str,
    session_id: int | None = None,
    client_ip: str | None = None,
    actor: str | None = None,
    detail: str | None = None,
) -> None:
    session.add(
        AuthAudit(
            at=datetime.now(UTC),
            event=event,
            via=via,
            session_id=session_id,
            client_ip=client_ip[:64] if client_ip else None,
            actor=actor[:120] if actor else None,
            detail=detail[:400] if detail else None,
        )
    )


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class IssuedSession:
    token: str
    expires_at: datetime
    session_id: int


def issue_session(
    session: Session,
    *,
    user_agent: str | None,
    client_ip: str | None,
    now: datetime | None = None,
) -> IssuedSession:
    now = now or datetime.now(UTC)
    token = secrets.token_urlsafe(32)
    row = AuthSession(
        token_hash=_token_hash(token),
        created_at=now,
        last_seen_at=now,
        expires_at=now + SESSION_ABSOLUTE,
        user_agent=user_agent[:300] if user_agent else None,
        client_ip=client_ip[:64] if client_ip else None,
    )
    session.add(row)
    session.flush()
    return IssuedSession(token=token, expires_at=row.expires_at, session_id=row.id)


def _is_live(row: AuthSession, now: datetime) -> bool:
    return row.revoked_at is None and now < row.expires_at and now < row.last_seen_at + SESSION_IDLE


def resolve_session(
    session: Session, token: str, *, now: datetime | None = None
) -> AuthSession | None:
    """The live session this bearer token names, or None. Refreshes `last_seen_at`."""
    now = now or datetime.now(UTC)
    row = session.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    if row is None or not _is_live(row, now):
        return None
    if now - row.last_seen_at > _TOUCH_EVERY:
        row.last_seen_at = now
    return row


def revoke_session(
    session: Session, token: str, *, client_ip: str | None = None, actor: str | None = None
) -> bool:
    """Sign out. True when a live session was ended; idempotent otherwise."""
    now = datetime.now(UTC)
    row = session.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = now
    row.revoked_reason = "sign_out"
    record_audit(
        session,
        AuthEvent.SIGN_OUT,
        via="web",
        session_id=row.id,
        client_ip=client_ip,
        actor=actor,
    )
    return True


def _revoke_all(session: Session, *, reason: str, now: datetime) -> int:
    result = session.execute(
        update(AuthSession)
        .where(AuthSession.revoked_at.is_(None), AuthSession.expires_at > now)
        .values(revoked_at=now, revoked_reason=reason)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def active_session_count(session: Session, *, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    return int(
        session.scalar(
            select(func.count(AuthSession.id)).where(
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
                AuthSession.last_seen_at > now - SESSION_IDLE,
            )
        )
        or 0
    )


# --------------------------------------------------------------------------
# sign-in
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SignInResult:
    """`issued` on success; otherwise `status` is the HTTP answer and `message` why."""

    issued: IssuedSession | None
    status: int
    message: str
    retry_after: int | None = None


_NOT_CONFIGURED = (
    "The back office has no password set yet. Set CAFEOPS_API_PASSWORD on the server and restart."
)


def sign_in(
    session: Session,
    password: str,
    *,
    client_ip: str,
    user_agent: str | None,
    actor: str | None = None,
) -> SignInResult:
    """Trade the shared password for a session token. Rate-limited per client IP."""
    wait = login_limiter.retry_after(client_ip)
    if wait is not None:
        if login_limiter.first_block(client_ip):
            record_audit(
                session,
                AuthEvent.LOGIN_RATE_LIMITED,
                via="web",
                client_ip=client_ip,
                actor=actor,
                detail=f"blocked for {wait}s",
            )
        return SignInResult(
            issued=None,
            status=429,
            message="Too many tries. Wait a minute and try again.",
            retry_after=wait,
        )
    if not auth_is_configured(session):
        return SignInResult(issued=None, status=503, message=_NOT_CONFIGURED)
    if not verify_password(session, password):
        login_limiter.record_failure(client_ip)
        record_audit(session, AuthEvent.LOGIN_FAILED, via="web", client_ip=client_ip, actor=actor)
        return SignInResult(issued=None, status=401, message="wrong password.")
    issued = issue_session(session, user_agent=user_agent, client_ip=client_ip)
    record_audit(
        session,
        AuthEvent.LOGIN_OK,
        via="web",
        session_id=issued.session_id,
        client_ip=client_ip,
        actor=actor,
        detail=f"credential from {credential_source(session)}",
    )
    return SignInResult(issued=issued, status=200, message="signed in")


# --------------------------------------------------------------------------
# changing the password
# --------------------------------------------------------------------------


class PasswordChangeRefused(Exception):
    """A change was refused. `status` is the HTTP answer; the message is for the person."""

    def __init__(self, message: str, *, status: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True, slots=True)
class PasswordChanged:
    issued: IssuedSession
    revoked_sessions: int
    previous_source: CredentialSource | None
    changed_at: datetime


def _normalised(pw: str) -> str:
    return "".join(pw.lower().split())


def _refuse(
    session: Session,
    message: str,
    *,
    status: int,
    client_ip: str | None,
    session_id: int | None,
    actor: str | None,
    detail: str,
) -> PasswordChangeRefused:
    record_audit(
        session,
        AuthEvent.PASSWORD_CHANGE_REFUSED,
        via="web",
        session_id=session_id,
        client_ip=client_ip,
        actor=actor,
        detail=detail,
    )
    return PasswordChangeRefused(message, status=status)


def change_password(
    session: Session,
    *,
    current_password: str,
    new_password: str,
    client_ip: str,
    user_agent: str | None,
    session_id: int | None,
    actor: str | None,
) -> PasswordChanged:
    """Replace the shared password. Revokes every session and issues the caller a new one.

    Refusals raise `PasswordChangeRefused` AFTER adding a PASSWORD_CHANGE_REFUSED audit
    row to `session`; the caller commits so the refusal is on record.
    """
    wait = login_limiter.retry_after(client_ip)
    if wait is not None:
        raise PasswordChangeRefused("Too many tries. Wait a minute and try again.", status=429)

    if not verify_password(session, current_password):
        login_limiter.record_failure(client_ip)
        raise _refuse(
            session,
            "The current password is not right, so nothing was changed.",
            status=403,
            client_ip=client_ip,
            session_id=session_id,
            actor=actor,
            detail="wrong current password",
        )
    if len(new_password) < MIN_PASSWORD_LENGTH:
        raise _refuse(
            session,
            f"The new password needs at least {MIN_PASSWORD_LENGTH} characters.",
            status=422,
            client_ip=client_ip,
            session_id=session_id,
            actor=actor,
            detail="too short",
        )
    if new_password == current_password:
        raise _refuse(
            session,
            "The new password is the same as the current one.",
            status=422,
            client_ip=client_ip,
            session_id=session_id,
            actor=actor,
            detail="unchanged",
        )
    if _normalised(new_password) in _DENYLIST:
        raise _refuse(
            session,
            "That password is too easy to guess for a back office on the internet. "
            "Pick something that isn't the café's name.",
            status=422,
            client_ip=client_ip,
            session_id=session_id,
            actor=actor,
            detail="on the denylist",
        )

    now = datetime.now(UTC)
    previous = credential_source(session)
    row = _stored(session)
    hashed = hash_password(new_password)
    if row is None:
        session.add(
            AuthCredential(
                id=1,
                password_hash=hashed,
                algo="scrypt",
                set_at=now,
                set_via="web",
                set_by=actor,
            )
        )
    else:
        row.password_hash = hashed
        row.algo = "scrypt"
        row.set_at = now
        row.set_via = "web"
        row.set_by = actor
    session.flush()
    revoked = _revoke_all(session, reason="password_changed", now=now)
    _forget_cached_verifications()
    issued = issue_session(session, user_agent=user_agent, client_ip=client_ip, now=now)
    record_audit(
        session,
        AuthEvent.PASSWORD_CHANGED,
        via="web",
        session_id=issued.session_id,
        client_ip=client_ip,
        actor=actor,
        detail=f"was from {previous or 'nowhere'}; revoked {revoked} session(s)",
    )
    return PasswordChanged(
        issued=issued, revoked_sessions=revoked, previous_source=previous, changed_at=now
    )


def reset_password(session: Session, *, actor: str | None = None) -> int:
    """Break-glass: delete the stored credential so `CAFEOPS_API_PASSWORD` rules again.

    Revokes every session too, since they were issued against the password that is
    going away. Returns how many were revoked. Shell access is already full trust, so
    this is for the CLI, never an HTTP route.
    """
    now = datetime.now(UTC)
    row = _stored(session)
    if row is not None:
        session.delete(row)
    revoked = _revoke_all(session, reason="password_reset", now=now)
    _forget_cached_verifications()
    record_audit(
        session,
        AuthEvent.PASSWORD_RESET,
        via="cli",
        actor=actor,
        detail=f"stored credential {'deleted' if row else 'absent'}; revoked {revoked}",
    )
    return revoked
