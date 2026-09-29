"""online ordering: sign-in required to place an order (docs/shop/CONTRACT.md §3.5, §10.J)

`shop_settings.guest_orders`, default off: a customer must be signed in with their
Rewards card to place an order. Browsing, quoting and the basket stay open.

Revision ID: h6e7f8a9b0c1
Revises: g5d6e7f8a9b0
Create Date: 2026-09-29 16:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'h6e7f8a9b0c1'
down_revision: str | Sequence[str] | None = 'g5d6e7f8a9b0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('shop_settings', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'guest_orders', sa.Boolean(), nullable=False, server_default=sa.text('0')
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('shop_settings', schema=None) as batch_op:
        batch_op.drop_column('guest_orders')
