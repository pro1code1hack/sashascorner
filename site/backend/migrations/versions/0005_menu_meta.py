"""site: website menu presentation overlay

Revision ID: site_0005
Revises: site_0004
Create Date: 2026-09-26

Hand-written. Creates ONLY site_menu_category_meta and site_menu_item_meta: the
website-only presentation (blurbs, descriptions, signature flags, hidden flags,
order) layered over the ops menu, which the site reads and never writes. Both are
keyed by name, because the ops menu has no stable id per item-across-sizes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import sashasite.db

revision: str = "site_0005"
down_revision: str | Sequence[str] | None = "site_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "site_menu_category_meta",
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=True),
        sa.Column("blurb", sa.Text(), nullable=True),
        sa.Column("hidden", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("updated_at", sashasite.db.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("name", name="pk_site_menu_category_meta"),
        sa.UniqueConstraint("slug", name="uq_site_menu_category_meta_slug"),
    )
    op.create_table(
        "site_menu_item_meta",
        sa.Column("item_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("signature", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("hidden", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("use_ops_note", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("updated_at", sashasite.db.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("item_name", name="pk_site_menu_item_meta"),
    )


def downgrade() -> None:
    op.drop_table("site_menu_item_meta")
    op.drop_table("site_menu_category_meta")
