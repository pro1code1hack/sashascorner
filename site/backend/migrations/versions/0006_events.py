"""site: events and RSVPs

Revision ID: site_0006
Revises: site_0005
Create Date: 2026-09-28

Hand-written. Creates ONLY site_event and site_event_rsvp (sashasite/events.py).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import sashasite.db

revision: str = "site_0006"
down_revision: str | Sequence[str] | None = "site_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "site_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("starts_at", sashasite.db.UTCDateTime(), nullable=False),
        sa.Column("ends_at", sashasite.db.UTCDateTime(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price_pence", sa.Integer(), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=True),
        sa.Column("image_media_id", sa.Integer(), nullable=True),
        sa.Column("published", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sashasite.db.UTCDateTime(), nullable=False),
        sa.Column("updated_at", sashasite.db.UTCDateTime(), nullable=False),
        sa.CheckConstraint("price_pence IS NULL OR price_pence >= 0", name="ck_site_event_price"),
        sa.CheckConstraint("capacity IS NULL OR capacity > 0", name="ck_site_event_capacity"),
        sa.CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at", name="ck_site_event_ends_after"
        ),
        sa.ForeignKeyConstraint(
            ["image_media_id"],
            ["site_media.id"],
            name="fk_site_event_image_media_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_site_event"),
        sa.UniqueConstraint("slug", name="uq_site_event_slug"),
    )
    op.create_index("ix_site_event_starts_at", "site_event", ["starts_at"])
    op.create_table(
        "site_event_rsvp",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("party", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sashasite.db.UTCDateTime(), nullable=False),
        sa.Column("cancelled_at", sashasite.db.UTCDateTime(), nullable=True),
        sa.CheckConstraint("party >= 1 AND party <= 6", name="ck_site_event_rsvp_party"),
        sa.CheckConstraint(
            "email IS NOT NULL OR phone IS NOT NULL", name="ck_site_event_rsvp_contact"
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["site_event.id"],
            name="fk_site_event_rsvp_event_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_site_event_rsvp"),
    )
    op.create_index("ix_site_event_rsvp_event_id", "site_event_rsvp", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_site_event_rsvp_event_id", table_name="site_event_rsvp")
    op.drop_table("site_event_rsvp")
    op.drop_index("ix_site_event_starts_at", table_name="site_event")
    op.drop_table("site_event")
