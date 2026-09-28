"""wallet

Two new tables for Sasha's Corner Rewards wallet passes (docs/loyalty/CONTRACT.md §2 B):
wallet_apple_registration (devices that added the Apple pass, from the PassKit web
service) and wallet_google_object (cards whose Google object exists and last synced).
No data; downgrade drops both, which loses only the registrations -- Apple devices
re-register on their next pass update, and Google objects are re-found by id.

Revision ID: b7c1e0a10002
Revises: b7c1e0a10001
Create Date: 2026-09-28 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import cafeops.db.types  # custom column types (Qty, UTCDateTime)


# revision identifiers, used by Alembic.
revision: str = 'b7c1e0a10002'
down_revision: Union[str, Sequence[str], None] = 'b7c1e0a10001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('wallet_apple_registration',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('device_library_id', sa.String(length=128), nullable=False),
    sa.Column('push_token', sa.String(length=200), nullable=False),
    sa.Column('card_id', sa.String(length=36), nullable=False),
    sa.Column('created_at', cafeops.db.types.UTCDateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['card_id'], ['loyalty_card.id'], name=op.f('fk_wallet_apple_registration_card_id_loyalty_card')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_wallet_apple_registration')),
    sa.UniqueConstraint('device_library_id', 'card_id', name='uq_wallet_apple_registration')
    )
    with op.batch_alter_table('wallet_apple_registration', schema=None) as batch_op:
        batch_op.create_index('ix_wallet_apple_registration_card', ['card_id'], unique=False)

    op.create_table('wallet_google_object',
    sa.Column('card_id', sa.String(length=36), nullable=False),
    sa.Column('object_id', sa.String(length=120), nullable=False),
    sa.Column('class_id', sa.String(length=120), nullable=False),
    sa.Column('last_synced_at', cafeops.db.types.UTCDateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.String(length=400), nullable=True),
    sa.ForeignKeyConstraint(['card_id'], ['loyalty_card.id'], name=op.f('fk_wallet_google_object_card_id_loyalty_card')),
    sa.PrimaryKeyConstraint('card_id', name=op.f('pk_wallet_google_object')),
    sa.UniqueConstraint('object_id', name=op.f('uq_wallet_google_object_object_id'))
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('wallet_google_object')
    with op.batch_alter_table('wallet_apple_registration', schema=None) as batch_op:
        batch_op.drop_index('ix_wallet_apple_registration_card')
    op.drop_table('wallet_apple_registration')
