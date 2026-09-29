"""The shared web password, sessions and their audit. shell-agents spec 4.2, DECISIONS.md 3.

Still ONE shared password (CLAUDE.md §1 non-goal: no user management). What changes is
where it lives and what the browser sends:

- `auth_credential` holds a scrypt hash. **Precedence:** if the row exists it wins;
  otherwise `CAFEOPS_API_PASSWORD` is the bootstrap. `cafeops password reset` deletes
  the row (env takes over again). The plaintext is never stored or logged.
- `auth_session` holds sha256(token) for bearer tokens issued at login, so a password
  change can revoke every other device.
- `auth_audit` records auth events with no secret material.

Login rate limiting (5 failures/min, 30/hour per client IP) is an in-process token
bucket, NOT a table: one process on one box (CLAUDE.md §3) makes that correct, and a
DB write per failed guess would hand an attacker the single SQLite writer.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from cafeops.db.base import Base
from cafeops.db.models._common import UTCDateTime, enum_col, utcnow
from cafeops.domain.enums import AuthEvent


class AuthCredential(Base):
    """Single row (id = 1): the shared password's hash. Wins over the env var."""

    __tablename__ = "auth_credential"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    #: `scrypt$n$r$p$salt_b64$hash_b64` (hashlib.scrypt, n=2**14, r=8, p=1, 16-byte salt).
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    algo: Mapped[str] = mapped_column(String(20), nullable=False, default="scrypt")
    set_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    #: "web" | "cli"
    set_via: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Operator name typed on the device that changed it (DECISIONS.md 6), if any.
    set_by: Mapped[str | None] = mapped_column(String(120))

    __table_args__ = (
        CheckConstraint("id = 1", name="single_row"),
        CheckConstraint("algo = 'scrypt'", name="algo_scrypt"),
    )


class AuthSession(Base):
    """A bearer token issued by `POST /api/auth/session`. Only its sha256 is stored.

    Valid iff `revoked_at IS NULL AND now < expires_at AND now < last_seen_at + idle`
    (30-day absolute, 7-day idle; the idle rule is the service's). A password change
    REVOKES every row (sets `revoked_at`) rather than deleting, so the audit can still
    say which sessions it ended.
    """

    __tablename__ = "auth_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Lowercase hex sha256 of the token (64 chars).
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    #: "sign_out" | "password_changed" | "password_reset" | "expired_sweep"
    revoked_reason: Mapped[str | None] = mapped_column(String(40))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    client_ip: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(
            "(revoked_at IS NULL) = (revoked_reason IS NULL)", name="revoked_has_reason"
        ),
        Index("ix_auth_session_active", "revoked_at", "expires_at"),
    )


class AuthAudit(Base):
    """Append-only record of auth events. Never holds a password or a token."""

    __tablename__ = "auth_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    event: Mapped[AuthEvent] = mapped_column(enum_col(AuthEvent), nullable=False)
    #: "web" | "cli" | "api_key"
    via: Mapped[str] = mapped_column(String(20), nullable=False)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("auth_session.id"))
    client_ip: Mapped[str | None] = mapped_column(String(64))
    #: Operator name from the device, when one was sent (DECISIONS.md 6).
    actor: Mapped[str | None] = mapped_column(String(120))
    #: e.g. "revoked 3 sessions", "too short". No secret material.
    detail: Mapped[str | None] = mapped_column(String(400))

    __table_args__ = (Index("ix_auth_audit_at", "at"), Index("ix_auth_audit_event", "event", "at"))


__all__ = ["AuthAudit", "AuthCredential", "AuthSession"]
