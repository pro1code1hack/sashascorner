"""loyalty back-office settings: configurable cooldown and 90-day targets

Adds to loyalty_program: cooldown_max_stamps (default 3) and cooldown_minutes (default
10) -- the SPEC's ">3 stamps on one card within 10 minutes needs a manager PIN", now
editable per programme from Rewards > Programme -- and `targets`, a JSON map of the
owner's 90-day targets (SPEC: "Exact targets are an open question").

Existing rows keep today's behaviour through the server defaults.

Revision ID: b7c1e0a10004
Revises: b7c1e0a10003
Create Date: 2026-09-28 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7c1e0a10004'
down_revision: Union[str, Sequence[str], None] = 'b7c1e0a10003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('loyalty_program', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('cooldown_max_stamps', sa.Integer(), nullable=False, server_default='3')
        )
        batch_op.add_column(
            sa.Column('cooldown_minutes', sa.Integer(), nullable=False, server_default='10')
        )
        batch_op.add_column(sa.Column('targets', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('loyalty_program', schema=None) as batch_op:
        batch_op.drop_column('targets')
        batch_op.drop_column('cooldown_minutes')
        batch_op.drop_column('cooldown_max_stamps')
