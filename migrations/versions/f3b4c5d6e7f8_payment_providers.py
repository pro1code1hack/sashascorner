"""online ordering: payment providers and the POS sink (docs/shop/CONTRACT.md §3b)

`f2a3b4c5d6e7` had already been applied to the live cafeops.db when the owner extended
the scope, so this sits on top of it rather than being folded in.

- `shop_settings.payment_provider` (default "stripe") and `pos_sink` (default "none");
- `shop_order.pos_ref`: the POS's reference for a pushed order;
- `shop_payment_customer`: the member's account at a provider -- a reference and a
  label, never a card number.

Revision ID: f3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-09-29 00:40:00

"""
from collections.abc import Sequence

import cafeops.db.types  # custom column types (Qty, UTCDateTime)
import sqlalchemy as sa
from alembic import op

revision: str = 'f3b4c5d6e7f8'
down_revision: str | Sequence[str] | None = 'f2a3b4c5d6e7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'shop_payment_customer',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('member_id', sa.Integer(), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('provider_customer_id', sa.String(length=120), nullable=False),
        sa.Column('default_method_label', sa.String(length=60), nullable=True),
        sa.Column('created_at', cafeops.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.Column('updated_at', cafeops.db.types.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['member_id'], ['loyalty_member.id'],
            name=op.f('fk_shop_payment_customer_member_id_loyalty_member'),
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_shop_payment_customer')),
        sa.UniqueConstraint('member_id', 'provider', name='uq_shop_payment_customer_member'),
    )
    with op.batch_alter_table('shop_settings', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('payment_provider', sa.String(length=20), nullable=False, server_default='stripe')
        )
        batch_op.add_column(
            sa.Column('pos_sink', sa.String(length=20), nullable=False, server_default='none')
        )
    with op.batch_alter_table('shop_order', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pos_ref', sa.String(length=80), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('shop_order', schema=None) as batch_op:
        batch_op.drop_column('pos_ref')
    with op.batch_alter_table('shop_settings', schema=None) as batch_op:
        batch_op.drop_column('pos_sink')
        batch_op.drop_column('payment_provider')
    op.drop_table('shop_payment_customer')
