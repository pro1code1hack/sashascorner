"""site: media library and image slots

Revision ID: site_0003
Revises: site_0002
Create Date: 2026-09-26

Hand-written. Creates ONLY site_media and site_slot_item; the shared DB's ops
tables are never mentioned here and must never be.

site_media uses AUTOINCREMENT so a deleted photo's id is never handed out again:
its variant files are served as immutable (/media/{id}-{w}.webp), and a reused id
would let a browser keep showing the old photo.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import sashasite.db

revision: str = "site_0003"
down_revision: str | Sequence[str] | None = "site_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "site_media",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("alt", sa.Text(), nullable=False),
        sa.Column("widths", sa.String(length=32), nullable=False),
        sa.Column("blur", sa.Text(), nullable=False),
        sa.Column("created_at", sashasite.db.UTCDateTime(), nullable=False),
        sa.CheckConstraint("width > 0 AND height > 0", name="ck_site_media_dimensions"),
        sa.CheckConstraint("bytes > 0", name="ck_site_media_bytes"),
        sa.CheckConstraint(
            "content_type IN ('image/jpeg', 'image/png', 'image/webp')",
            name="ck_site_media_content_type",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_site_media"),
        sa.UniqueConstraint("sha256", name="uq_site_media_sha256"),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "site_slot_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slot_key", sa.String(length=80), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("alt_override", sa.Text(), nullable=True),
        sa.Column("focal_x", sa.Float(), nullable=False),
        sa.Column("focal_y", sa.Float(), nullable=False),
        sa.Column("updated_at", sashasite.db.UTCDateTime(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_site_slot_item_position"),
        sa.CheckConstraint("focal_x >= 0 AND focal_x <= 1", name="ck_site_slot_item_focal_x"),
        sa.CheckConstraint("focal_y >= 0 AND focal_y <= 1", name="ck_site_slot_item_focal_y"),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["site_media.id"],
            name="fk_site_slot_item_media_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_site_slot_item"),
        sa.UniqueConstraint("slot_key", "position", name="uq_site_slot_item_slot_key_position"),
    )
    op.create_index("ix_site_slot_item_media_id", "site_slot_item", ["media_id"])


def downgrade() -> None:
    op.drop_index("ix_site_slot_item_media_id", table_name="site_slot_item")
    op.drop_table("site_slot_item")
    op.drop_table("site_media")
