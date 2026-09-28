"""loyalty card v2: stickers, welcome stamp, expiring stamps, member notes, cancel campaigns

docs/loyalty/BACKOFFICE-V2.md §4. Adds:

- loyalty_program: `stickers` (JSON, ordered enabled sticker keys; NULL = all eight),
  `welcome_stamp` (default off), `stamps_expire_months` (NULL = never);
- loyalty_member: `notes`;
- loyalty_card: `stickers` (JSON, one key per filled slot; NULL = the default rotation);
- loyalty_stamp_event: `sticker` (what the stamp put on the card, for history);
- loyalty_campaign: `cancelled_at`.

The new enum values (StampReason.WELCOME, CampaignSegment.BIRTHDAY) need nothing here:
enums are stored as plain VARCHAR without CHECK constraints, and both names fit the
existing column widths.

Revision ID: d5e1a7c30b42
Revises: c4d2e8a91f07
Create Date: 2026-09-28 18:30:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd5e1a7c30b42'
down_revision: Union[str, Sequence[str], None] = 'c4d2e8a91f07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('loyalty_program', sa.Column('stickers', sa.JSON(), nullable=True))
    op.add_column(
        'loyalty_program',
        sa.Column('welcome_stamp', sa.Boolean(), nullable=False, server_default='0'),
    )
    op.add_column('loyalty_program', sa.Column('stamps_expire_months', sa.Integer(), nullable=True))
    op.add_column('loyalty_member', sa.Column('notes', sa.String(length=1000), nullable=True))
    op.add_column('loyalty_card', sa.Column('stickers', sa.JSON(), nullable=True))
    op.add_column('loyalty_stamp_event', sa.Column('sticker', sa.String(length=20), nullable=True))
    op.add_column('loyalty_campaign', sa.Column('cancelled_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('loyalty_campaign', schema=None) as batch_op:
        batch_op.drop_column('cancelled_at')
    with op.batch_alter_table('loyalty_stamp_event', schema=None) as batch_op:
        batch_op.drop_column('sticker')
    with op.batch_alter_table('loyalty_card', schema=None) as batch_op:
        batch_op.drop_column('stickers')
    with op.batch_alter_table('loyalty_member', schema=None) as batch_op:
        batch_op.drop_column('notes')
    with op.batch_alter_table('loyalty_program', schema=None) as batch_op:
        batch_op.drop_column('stamps_expire_months')
        batch_op.drop_column('welcome_stamp')
        batch_op.drop_column('stickers')
