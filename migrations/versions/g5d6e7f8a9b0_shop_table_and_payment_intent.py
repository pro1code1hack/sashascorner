"""online ordering: eat-in table and the provider's payment id (docs/shop/CONTRACT.md §10.F)

- `shop_order.table`: the eat-in table the customer sits at (null for takeaway);
- `shop_order.payment_intent`: Stripe's payment_intent, stored at webhook time so a
  refund posts straight to `/v1/refunds`.

Revision ID: g5d6e7f8a9b0
Revises: f4c5d6e7f8a9
Create Date: 2026-09-29 12:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'g5d6e7f8a9b0'
down_revision: str | Sequence[str] | None = 'f4c5d6e7f8a9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('shop_order', schema=None) as batch_op:
        batch_op.add_column(sa.Column('table', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('payment_intent', sa.String(length=120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('shop_order', schema=None) as batch_op:
        batch_op.drop_column('payment_intent')
        batch_op.drop_column('table')
