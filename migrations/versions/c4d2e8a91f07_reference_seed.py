"""reference seed: allergens, photo provenance, ingredient photo, menu item reference

For `cafeops seed-reference` (cafeops/seed/reference/README.md):

- ingredient.allergens (JSON list of the UK 14, `[]` = confirmed none, NULL = unknown)
  and ingredient.allergens_source (the page the list came from).
- ingredient.photo_asset_id -> media_asset (ON DELETE SET NULL), like menu_item's.
- media_asset.licence / author / source_url: provenance of a photo somebody else took.
- menu_item_reference: researched description, tags and a benchmark price per menu
  product (keyed by name, like the site's overlay). The benchmark is comparison only;
  it is never a sell price.

All columns nullable, so existing rows need no default.

Revision ID: c4d2e8a91f07
Revises: b7c1e0a10004
Create Date: 2026-09-28 19:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import cafeops.db.types


revision: str = 'c4d2e8a91f07'
down_revision: Union[str, Sequence[str], None] = 'b7c1e0a10004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('ingredient', schema=None) as batch_op:
        batch_op.add_column(sa.Column('allergens', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('allergens_source', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('photo_asset_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f('fk_ingredient_photo_asset_id_media_asset'),
            'media_asset',
            ['photo_asset_id'],
            ['id'],
            ondelete='SET NULL',
        )

    with op.batch_alter_table('media_asset', schema=None) as batch_op:
        batch_op.add_column(sa.Column('licence', sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column('author', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('source_url', sa.String(length=500), nullable=True))

    op.create_table(
        'menu_item_reference',
        sa.Column('item_name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.String(length=300), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('benchmark_price_pence', sa.Integer(), nullable=True),
        sa.Column('benchmark_source_url', sa.String(length=500), nullable=True),
        sa.Column('fetched_on', sa.Date(), nullable=True),
        sa.Column('updated_at', cafeops.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            'benchmark_price_pence IS NULL OR benchmark_price_pence > 0',
            name=op.f('ck_menu_item_reference_benchmark_positive'),
        ),
        sa.PrimaryKeyConstraint('item_name', name=op.f('pk_menu_item_reference')),
    )


def downgrade() -> None:
    op.drop_table('menu_item_reference')
    with op.batch_alter_table('media_asset', schema=None) as batch_op:
        batch_op.drop_column('source_url')
        batch_op.drop_column('author')
        batch_op.drop_column('licence')
    with op.batch_alter_table('ingredient', schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f('fk_ingredient_photo_asset_id_media_asset'), type_='foreignkey'
        )
        batch_op.drop_column('photo_asset_id')
        batch_op.drop_column('allergens_source')
        batch_op.drop_column('allergens')
