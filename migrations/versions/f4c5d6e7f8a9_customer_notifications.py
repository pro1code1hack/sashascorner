"""online ordering: customer notifications (docs/shop/CONTRACT.md §3c)

`f3b4c5d6e7f8` was already on the live cafeops.db when this scope arrived, so this sits
on top of it.

- `shop_push_subscription`: a browser's Web Push subscription for one order;
- `shop_order.sms_opt_in`: the customer asked for texts (the paid channel);
- `shop_settings.email_notify` / `push_notify` (on) and `sms_notify` ("ready").

Revision ID: f4c5d6e7f8a9
Revises: f3b4c5d6e7f8
Create Date: 2026-09-29 01:20:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

import cafeops.db.types  # custom column types (Qty, UTCDateTime)


revision: str = 'f4c5d6e7f8a9'
down_revision: Union[str, Sequence[str], None] = 'f3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'shop_push_subscription',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('endpoint', sa.String(length=500), nullable=False),
        sa.Column('p256dh', sa.String(length=200), nullable=False),
        sa.Column('auth', sa.String(length=100), nullable=False),
        sa.Column('created_at', cafeops.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column('expired_at', cafeops.db.types.UTCDateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['order_id'], ['shop_order.id'],
            name=op.f('fk_shop_push_subscription_order_id_shop_order'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_shop_push_subscription')),
    )
    with op.batch_alter_table('shop_push_subscription', schema=None) as batch_op:
        batch_op.create_index('ix_shop_push_subscription_order', ['order_id'], unique=False)
    with op.batch_alter_table('shop_order', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('sms_opt_in', sa.Boolean(), nullable=False, server_default='0')
        )
    with op.batch_alter_table('shop_settings', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('email_notify', sa.Boolean(), nullable=False, server_default='1')
        )
        batch_op.add_column(
            sa.Column('push_notify', sa.Boolean(), nullable=False, server_default='1')
        )
        batch_op.add_column(
            sa.Column('sms_notify', sa.String(length=12), nullable=False, server_default='ready')
        )


def downgrade() -> None:
    with op.batch_alter_table('shop_settings', schema=None) as batch_op:
        batch_op.drop_column('sms_notify')
        batch_op.drop_column('push_notify')
        batch_op.drop_column('email_notify')
    with op.batch_alter_table('shop_order', schema=None) as batch_op:
        batch_op.drop_column('sms_opt_in')
    with op.batch_alter_table('shop_push_subscription', schema=None) as batch_op:
        batch_op.drop_index('ix_shop_push_subscription_order')
    op.drop_table('shop_push_subscription')
