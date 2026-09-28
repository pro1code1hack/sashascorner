"""Staff on the scanner: registered devices, PIN logins, 12-hour sessions, manager PINs.

SPEC: "Scanner only on registered devices, staff PIN, 12-hour session." CONTRACT §2/§5.

**Pairing.** The back office creates a device and shows a 6-digit code for 15 minutes; the
scanner trades it (plus a name) for a long random device token, once. The code is stored
as sha256 only and dies on use. Guessing is limited per client IP: a million codes and
five tries a minute is not a race anybody wins inside 15 minutes.

**Login.** The PIN identifies the person -- PINs are unique among active users, checked on
every create/change -- so the till asks one question. Failures are limited per DEVICE:
five wrong PINs lock that device for five minutes. Per device rather than per IP because
the café's tablets share one public address, and one person's typos must not lock the
other till.

**PIN hashing** is `services/auth.py`'s scrypt format. A 4-digit PIN has 10,000 values
and no KDF makes that strong; what protects it is that it only works on a registered
device, behind the lockout. The hash exists so a copied database file does not hand
over the PINs outright.

The limiters are in-process for the reason `services/auth.py` gives: one process on one
box, and a table would put every wrong guess on the single SQLite writer.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from cafeops.db.models import StaffDevice, StaffRole, StaffSession, StaffUser
from cafeops.services.auth import RateLimiter, hash_password, verify_hash
from cafeops.services.loyalty.common import now_utc, sha256_hex
from cafeops.services.loyalty.errors import LoyaltyError

__all__ = [
    "PAIRING_TTL",
    "SESSION_TTL",
    "IssuedStaffSession",
    "LockoutLimiter",
    "NewDevice",
    "StaffActor",
    "create_device",
    "create_staff_user",
    "list_devices",
    "list_staff",
    "login",
    "logout",
    "pair_device",
    "pin_lockout",
    "resolve_device",
    "resolve_staff",
    "revoke_device",
    "update_staff_user",
    "verify_manager_pin",
]

SESSION_TTL = timedelta(hours=12)
PAIRING_TTL = timedelta(minutes=15)
#: `last_seen_at` is refreshed at most this often, for the same single-writer reason as
#: `auth_session`.
_TOUCH_EVERY = timedelta(minutes=5)
_PIN_RE = re.compile(r"^\d{4,6}$")


class LockoutLimiter:
    """N failures lock a key for a fixed time. A success clears the key's failures."""

    def __init__(self, *, max_failures: int = 5, lock_seconds: int = 300) -> None:
        self.max_failures = max_failures
        self.lock_seconds = lock_seconds
        self._fails: dict[str, int] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> int | None:
        now = time.monotonic()
        with self._lock:
            until = self._locked_until.get(key)
            if until is None:
                return None
            if until <= now:
                del self._locked_until[key]
                self._fails.pop(key, None)
                return None
            return max(1, int(until - now) + 1)

    def record_failure(self, key: str) -> None:
        with self._lock:
            count = self._fails.get(key, 0) + 1
            self._fails[key] = count
            if count >= self.max_failures:
                self._locked_until[key] = time.monotonic() + self.lock_seconds

    def record_success(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


#: PIN logins and manager-PIN approvals, keyed `device:<id>` (or `ip:<addr>` from the
#: back office, which has no device).
pin_lockout = LockoutLimiter(max_failures=5, lock_seconds=300)
#: Pairing-code guesses, keyed by client IP.
_pairing_limiter = RateLimiter(per_minute=5, per_hour=30)


# --------------------------------------------------------------------------
# staff users
# --------------------------------------------------------------------------


def _check_pin_format(pin: str) -> None:
    if not _PIN_RE.match(pin):
        raise LoyaltyError(422, "bad_pin_format", "A PIN is 4 to 6 digits.")


def _pin_owner(session: Session, pin: str, *, exclude_id: int | None = None) -> StaffUser | None:
    """The active user whose PIN this is. Scrypt per user; there are a handful of them."""
    for user in session.scalars(select(StaffUser).where(StaffUser.active.is_(True))):
        if user.id == exclude_id:
            continue
        if verify_hash(pin, user.pin_hash):
            return user
    return None


def _check_pin_free(session: Session, pin: str, *, exclude_id: int | None = None) -> None:
    if _pin_owner(session, pin, exclude_id=exclude_id) is not None:
        # Deliberately does not say whose: that would tell anybody with admin access
        # a colleague's PIN.
        raise LoyaltyError(
            409, "pin_taken", "Someone else already uses that PIN. Choose a different one."
        )


def list_staff(session: Session) -> list[StaffUser]:
    return list(
        session.scalars(select(StaffUser).order_by(StaffUser.active.desc(), StaffUser.name))
    )


def create_staff_user(
    session: Session, *, name: str, role: StaffRole, pin: str, telegram_id: int | None = None
) -> StaffUser:
    name = name.strip()
    if not name:
        raise LoyaltyError(422, "name_required", "A staff member needs a name.")
    _check_pin_format(pin)
    _check_pin_free(session, pin)
    user = StaffUser(
        name=name[:80],
        role=role,
        pin_hash=hash_password(pin),
        telegram_id=telegram_id,
        active=True,
        created_at=now_utc(),
    )
    session.add(user)
    session.flush()
    return user


def update_staff_user(
    session: Session,
    user_id: int,
    *,
    name: str | None = None,
    role: StaffRole | None = None,
    pin: str | None = None,
    active: bool | None = None,
    telegram_id: int | None = None,
    clear_telegram: bool = False,
) -> StaffUser:
    user = session.get(StaffUser, user_id)
    if user is None:
        raise LoyaltyError(404, "unknown_staff", "There is no staff member with that id.")
    if name is not None:
        if not name.strip():
            raise LoyaltyError(422, "name_required", "A staff member needs a name.")
        user.name = name.strip()[:80]
    if role is not None:
        user.role = role
    if pin is not None:
        _check_pin_format(pin)
        _check_pin_free(session, pin, exclude_id=user.id)
        user.pin_hash = hash_password(pin)
    if active is not None:
        if active and not user.active and pin is None:
            # Two salted hashes cannot be compared, so a returning colleague's old PIN
            # might now belong to someone hired since. A new PIN is checked for clashes.
            raise LoyaltyError(
                409, "pin_required", "Set a new PIN when reactivating a staff member."
            )
        user.active = active
        if not active:
            _end_sessions(session, staff_user_id=user.id)
    if clear_telegram:
        user.telegram_id = None
    elif telegram_id is not None:
        user.telegram_id = telegram_id
    session.flush()
    return user


def _end_sessions(
    session: Session, *, staff_user_id: int | None = None, device_id: int | None = None
) -> None:
    now = now_utc()
    stmt = update(StaffSession).where(StaffSession.revoked_at.is_(None))
    if staff_user_id is not None:
        stmt = stmt.where(StaffSession.staff_user_id == staff_user_id)
    if device_id is not None:
        stmt = stmt.where(StaffSession.device_id == device_id)
    session.execute(stmt.values(revoked_at=now))


def verify_manager_pin(session: Session, pin: str | None, *, limiter_key: str) -> StaffUser:
    """The active manager or owner whose PIN this is. Rate-limited like a login.

    Raises 403 `bad_manager_pin` for a wrong PIN or a PIN that belongs to plain staff: an
    approval only means something when the approver could not have approved themselves.
    """
    wait = pin_lockout.retry_after(limiter_key)
    if wait is not None:
        raise LoyaltyError(
            429, "locked", f"Too many wrong PINs on this device. Try again in {wait} seconds."
        )
    if not pin or not _PIN_RE.match(pin):
        pin_lockout.record_failure(limiter_key)
        raise LoyaltyError(403, "bad_manager_pin", "That is not a manager PIN.")
    user = _pin_owner(session, pin)
    if user is None or not user.is_manager:
        pin_lockout.record_failure(limiter_key)
        raise LoyaltyError(403, "bad_manager_pin", "That is not a manager PIN.")
    pin_lockout.record_success(limiter_key)
    return user


# --------------------------------------------------------------------------
# devices
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NewDevice:
    device_id: int
    pairing_code: str
    expires_at: datetime


def create_device(session: Session, *, name: str) -> NewDevice:
    """A device slot with a 6-digit pairing code, valid for 15 minutes."""
    name = name.strip()
    if not name:
        raise LoyaltyError(422, "name_required", "Name the device, e.g. 'Till tablet'.")
    now = now_utc()
    # Codes are unique among live pairings so a code names exactly one slot.
    for _ in range(20):
        code = f"{secrets.randbelow(1_000_000):06d}"
        clash = session.scalar(
            select(StaffDevice.id).where(
                StaffDevice.pairing_code_hash == sha256_hex(code),
                StaffDevice.pairing_expires_at > now,
            )
        )
        if clash is None:
            break
    else:  # pragma: no cover - a million codes, a handful live
        raise LoyaltyError(503, "no_code", "Could not allocate a pairing code. Try again.")
    device = StaffDevice(
        name=name[:80],
        pairing_code_hash=sha256_hex(code),
        pairing_expires_at=now + PAIRING_TTL,
        created_at=now,
    )
    session.add(device)
    session.flush()
    return NewDevice(device_id=device.id, pairing_code=code, expires_at=now + PAIRING_TTL)


def list_devices(session: Session) -> list[StaffDevice]:
    return list(session.scalars(select(StaffDevice).order_by(StaffDevice.created_at.desc())))


def revoke_device(session: Session, device_id: int) -> None:
    device = session.get(StaffDevice, device_id)
    if device is None:
        raise LoyaltyError(404, "unknown_device", "There is no device with that id.")
    now = now_utc()
    if device.revoked_at is None:
        device.revoked_at = now
    device.pairing_code_hash = None
    device.pairing_expires_at = None
    _end_sessions(session, device_id=device.id)


def pair_device(session: Session, *, pairing_code: str, device_name: str, client_ip: str) -> str:
    """Trade a pairing code for a device token. The token is returned once, never stored."""
    wait = _pairing_limiter.retry_after(client_ip)
    if wait is not None:
        raise LoyaltyError(429, "rate_limited", f"Too many tries. Wait {wait} seconds.")
    now = now_utc()
    code = pairing_code.strip()
    device = None
    if re.fullmatch(r"\d{6}", code):
        device = session.scalar(
            select(StaffDevice).where(
                StaffDevice.pairing_code_hash == sha256_hex(code),
                StaffDevice.pairing_expires_at > now,
                StaffDevice.revoked_at.is_(None),
            )
        )
    if device is None:
        _pairing_limiter.record_failure(client_ip)
        raise LoyaltyError(
            400,
            "bad_pairing_code",
            "That code is wrong or has expired. Ask a manager for a new one in the back office.",
        )
    token = secrets.token_urlsafe(32)
    device.token_hash = sha256_hex(token)
    device.pairing_code_hash = None
    device.pairing_expires_at = None
    device.registered_at = now
    device.last_seen_at = now
    if device_name.strip():
        device.name = device_name.strip()[:80]
    return token


def resolve_device(session: Session, device_token: str | None) -> StaffDevice:
    if not device_token:
        raise LoyaltyError(
            401, "device_not_registered", "This device is not registered for the scanner."
        )
    device = session.scalar(
        select(StaffDevice).where(StaffDevice.token_hash == sha256_hex(device_token))
    )
    if device is None or device.revoked_at is not None or device.registered_at is None:
        raise LoyaltyError(
            401, "device_not_registered", "This device is not registered for the scanner."
        )
    now = now_utc()
    if device.last_seen_at is None or now - device.last_seen_at > _TOUCH_EVERY:
        device.last_seen_at = now
    return device


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StaffActor:
    """Who is holding the scanner, on what. Every stamp records both."""

    user_id: int
    name: str
    role: StaffRole
    device_id: int
    device_name: str
    session_id: int
    expires_at: datetime

    @property
    def is_manager(self) -> bool:
        return self.role in (StaffRole.MANAGER, StaffRole.OWNER)

    @property
    def limiter_key(self) -> str:
        return f"device:{self.device_id}"


@dataclass(frozen=True, slots=True)
class IssuedStaffSession:
    token: str
    expires_at: datetime
    actor: StaffActor


def login(session: Session, *, device_token: str | None, pin: str) -> IssuedStaffSession:
    device = resolve_device(session, device_token)
    key = f"device:{device.id}"
    wait = pin_lockout.retry_after(key)
    if wait is not None:
        raise LoyaltyError(
            429, "locked", f"Too many wrong PINs on this device. Try again in {wait} seconds."
        )
    user = _pin_owner(session, pin) if _PIN_RE.match(pin.strip()) else None
    if user is None:
        pin_lockout.record_failure(key)
        raise LoyaltyError(401, "bad_pin", "That PIN is not right.")
    pin_lockout.record_success(key)
    now = now_utc()
    token = secrets.token_urlsafe(32)
    row = StaffSession(
        token_hash=sha256_hex(token),
        staff_user_id=user.id,
        device_id=device.id,
        created_at=now,
        expires_at=now + SESSION_TTL,
    )
    session.add(row)
    session.flush()
    return IssuedStaffSession(
        token=token,
        expires_at=row.expires_at,
        actor=StaffActor(
            user_id=user.id,
            name=user.name,
            role=user.role,
            device_id=device.id,
            device_name=device.name,
            session_id=row.id,
            expires_at=row.expires_at,
        ),
    )


def resolve_staff(
    session: Session, *, device_token: str | None, session_token: str | None
) -> StaffActor:
    """The live session on this registered device, or 401.

    The session must belong to THIS device: a session token lifted from one tablet is
    worthless on a phone that was never registered.
    """
    device = resolve_device(session, device_token)
    if not session_token:
        raise LoyaltyError(401, "login_required", "Log in with your PIN.")
    row = session.scalar(
        select(StaffSession).where(StaffSession.token_hash == sha256_hex(session_token))
    )
    now = now_utc()
    if (
        row is None
        or row.device_id != device.id
        or row.revoked_at is not None
        or row.expires_at <= now
        or not row.user.active
    ):
        raise LoyaltyError(401, "login_required", "Your session has ended. Log in with your PIN.")
    return StaffActor(
        user_id=row.user.id,
        name=row.user.name,
        role=row.user.role,
        device_id=device.id,
        device_name=device.name,
        session_id=row.id,
        expires_at=row.expires_at,
    )


def logout(session: Session, actor: StaffActor) -> None:
    row = session.get(StaffSession, actor.session_id)
    if row is not None and row.revoked_at is None:
        row.revoked_at = now_utc()
