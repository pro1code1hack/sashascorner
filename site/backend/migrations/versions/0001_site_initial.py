"""site: bookings and contact messages

Revision ID: site_0001
Revises:
Create Date: 2026-09-25

Hand-written. Creates ONLY site_* tables; the shared DB's ops tables are never
mentioned here and must never be.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import sashasite.db

revision: str = "site_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "site_booking",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reference", sa.String(length=16), nullable=False),
        sa.Column("manage_token", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("party", sa.Integer(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("local_time", sa.String(length=5), nullable=False),
        sa.Column("starts_at", sashasite.db.UTCDateTime(), nullable=False),
        sa.Column("ends_at", sashasite.db.UTCDateTime(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sashasite.db.UTCDateTime(), nullable=False),
        sa.Column("cancelled_at", sashasite.db.UTCDateTime(), nullable=True),
        sa.CheckConstraint("party > 0", name="ck_site_booking_party_positive"),
        sa.CheckConstraint("status IN ('confirmed', 'cancelled')", name="ck_site_booking_status"),
        sa.PrimaryKeyConstraint("id", name="pk_site_booking"),
        sa.UniqueConstraint("reference", name="uq_site_booking_reference"),
        sa.UniqueConstraint("manage_token", name="uq_site_booking_manage_token"),
    )
    op.create_index("ix_site_booking_date_status", "site_booking", ["local_date", "status"])
    op.create_table(
        "site_contact_message",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("topic", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sashasite.db.UTCDateTime(), nullable=False),
        sa.CheckConstraint(
            "topic IN ('general', 'events', 'feedback', 'press', 'jobs')",
            name="ck_site_contact_message_topic",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_site_contact_message"),
    )


def downgrade() -> None:
    op.drop_table("site_contact_message")
    op.drop_index("ix_site_booking_date_status", table_name="site_booking")
    op.drop_table("site_booking")
