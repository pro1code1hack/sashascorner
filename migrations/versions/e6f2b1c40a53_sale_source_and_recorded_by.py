"""sale: source, recorded_by, note -- hand-typed transactions (DECISIONS 27)

Every `sale` row now says where it came from. Existing rows are the Lightspeed sync
(or the demo seed imitating it) and become `POS_API`; the loyalty redemption rows,
recognisable by their fixed receipt id, become `LOYALTY`.

The new enum value `SaleChannel.CASH` needs nothing here: enums are stored as plain
VARCHAR without CHECK constraints and the name fits the column width.

Revision ID: e6f2b1c40a53
Revises: e6f2b9c41a55
Create Date: 2026-09-28 20:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e6f2b1c40a53'
down_revision: Union[str, Sequence[str], None] = 'e6f2b9c41a55'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'sale',
        sa.Column(
            'source',
            sa.Enum('POS_API', 'MANUAL', 'CSV_UPLOAD', 'LOYALTY', name='salesource', native_enum=False),
            nullable=False,
            server_default='POS_API',
        ),
    )
    op.add_column('sale', sa.Column('recorded_by', sa.String(length=120), nullable=True))
    op.add_column('sale', sa.Column('note', sa.String(length=400), nullable=True))
    op.create_index('ix_sale_source', 'sale', ['source'])
    op.execute("UPDATE sale SET source = 'LOYALTY' WHERE lightspeed_receipt_id = 'loyalty'")


def downgrade() -> None:
    with op.batch_alter_table('sale', schema=None) as batch_op:
        batch_op.drop_index('ix_sale_source')
        batch_op.drop_column('note')
        batch_op.drop_column('recorded_by')
        batch_op.drop_column('source')
