"""site: admin auth, sessions, audit and settings; booking and message statuses

Revision ID: site_0004
Revises: site_0003
Create Date: 2026-09-26

Hand-written. Touches ONLY site_* tables; the shared DB's ops tables are never
mentioned here and must never be.

- New: site_admin_credential, site_admin_session, site_admin_audit, site_setting.
- site_booking: status CHECK widened to confirmed|cancelled|arrived|no_show;
  new ``source`` (web|admin, default web) and ``updated_at`` (backfilled from
  created_at).
- site_contact_message: new ``status`` (new|handled|archived, default new) and
  ``handled_at``.

SQLite cannot alter a CHECK constraint or a column's nullability in place, so the
two existing tables are rebuilt with batch mode (recreate="always").
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import sashasite.db

revision: str = "site_0004"
down_revision: str | Sequence[str] | None = "site_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BOOKING_STATUS_OLD = "status IN ('confirmed', 'cancelled')"
_BOOKING_STATUS_NEW = "status IN ('confirmed', 'cancelled', 'arrived', 'no_show')"
_BOOKING_SOURCE = "source IN ('web', 'admin')"
_MESSAGE_STATUS = "status IN ('new', 'handled', 'archived')"


def upgrade() -> None:
    op.create_table(
        "site_admin_credential",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("updated_at", sashasite.db.UTCDateTime(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_site_admin_credential_single"),
        sa.PrimaryKeyConstraint("id", name="pk_site_admin_credential"),
    )
    op.create_table(
        "site_admin_session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sashasite.db.UTCDateTime(), nullable=False),
        sa.Column("last_used_at", sashasite.db.UTCDateTime(), nullable=False),
        sa.Column("expires_at", sashasite.db.UTCDateTime(), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=200), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_site_admin_session"),
        sa.UniqueConstraint("token_sha256", name="uq_site_admin_session_token_sha256"),
    )
    op.create_index("ix_site_admin_session_expires_at", "site_admin_session", ["expires_at"])
    op.create_table(
        "site_admin_audit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("at", sashasite.db.UTCDateTime(), nullable=False),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_site_admin_audit"),
    )
    op.create_index("ix_site_admin_audit_at", "site_admin_audit", ["at"])
    op.create_table(
        "site_setting",
        sa.Column("key", sa.String(length=40), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sashasite.db.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key", name="pk_site_setting"),
    )

    # site_booking: add the columns (updated_at nullable for the backfill), then
    # rebuild with the new CHECKs and updated_at NOT NULL.
    with op.batch_alter_table("site_booking") as batch:
        batch.add_column(
            sa.Column("source", sa.String(length=8), server_default="web", nullable=False)
        )
        batch.add_column(sa.Column("updated_at", sashasite.db.UTCDateTime(), nullable=True))
    op.execute("UPDATE site_booking SET updated_at = created_at WHERE updated_at IS NULL")
    with op.batch_alter_table("site_booking", recreate="always") as batch:
        batch.alter_column("updated_at", existing_type=sashasite.db.UTCDateTime(), nullable=False)
        batch.drop_constraint("ck_site_booking_status", type_="check")
        batch.create_check_constraint("ck_site_booking_status", _BOOKING_STATUS_NEW)
        batch.create_check_constraint("ck_site_booking_source", _BOOKING_SOURCE)

    with op.batch_alter_table("site_contact_message", recreate="always") as batch:
        batch.add_column(
            sa.Column("status", sa.String(length=10), server_default="new", nullable=False)
        )
        batch.add_column(sa.Column("handled_at", sashasite.db.UTCDateTime(), nullable=True))
        batch.create_check_constraint("ck_site_contact_message_status", _MESSAGE_STATUS)


def downgrade() -> None:
    with op.batch_alter_table("site_contact_message", recreate="always") as batch:
        batch.drop_constraint("ck_site_contact_message_status", type_="check")
        batch.drop_column("handled_at")
        batch.drop_column("status")

    # The old CHECK knows only confirmed|cancelled: an arrival or a no-show is
    # still a booking that was kept, so it goes back to confirmed.
    op.execute(
        "UPDATE site_booking SET status = 'confirmed' WHERE status IN ('arrived', 'no_show')"
    )
    with op.batch_alter_table("site_booking", recreate="always") as batch:
        batch.drop_constraint("ck_site_booking_source", type_="check")
        batch.drop_constraint("ck_site_booking_status", type_="check")
        batch.create_check_constraint("ck_site_booking_status", _BOOKING_STATUS_OLD)
        batch.drop_column("updated_at")
        batch.drop_column("source")

    op.drop_table("site_setting")
    op.drop_index("ix_site_admin_audit_at", table_name="site_admin_audit")
    op.drop_table("site_admin_audit")
    op.drop_index("ix_site_admin_session_expires_at", table_name="site_admin_session")
    op.drop_table("site_admin_session")
    op.drop_table("site_admin_credential")
