"""browser ordering agents: supplier_session, browser_job, browser_job_step

docs/agents/BROWSER-ORDERING.md. Three new tables and nothing else:

- supplier_session: the signed-in state of one persistent browser profile per supplier
  (no password column, by design; the encrypted blob is a Playwright storage state);
- browser_job: the queue the API and scheduler INSERT into and only the worker runs;
- browser_job_step: the per-action audit trail under each job.

Enums are stored as VARCHAR with a CHECK (enum_col), like every other enum here.

Revision ID: e6f2b9c41a55
Revises: d5e1a7c30b42
Create Date: 2026-09-28 21:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e6f2b9c41a55'
down_revision: Union[str, Sequence[str], None] = 'd5e1a7c30b42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'supplier_session',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('supplier_id', sa.Integer(), nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'NOT_CONNECTED', 'CONNECTED', 'EXPIRED', 'CHECK_FAILED',
                name='suppliersessionstatus', native_enum=False, length=13,
            ),
            nullable=False,
        ),
        sa.Column('account_label', sa.String(length=200), nullable=True),
        sa.Column('profile_dir', sa.String(length=200), nullable=True),
        sa.Column('storage_state_enc', sa.LargeBinary(), nullable=True),
        sa.Column('connected_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('connected_by', sa.String(length=120), nullable=True),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_ok_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status <> 'CONNECTED' OR (connected_at IS NOT NULL AND connected_by IS NOT NULL)",
            name='ck_supplier_session_connected_by_human',
        ),
        sa.ForeignKeyConstraint(['supplier_id'], ['supplier.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('supplier_id'),
    )
    op.create_table(
        'browser_job',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            'kind',
            sa.Enum('STAGE_BASKET', 'CHECK_SESSION', name='browserjobkind', native_enum=False, length=13),
            nullable=False,
        ),
        sa.Column(
            'status',
            sa.Enum(
                'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'NEEDS_HUMAN',
                name='browserjobstatus', native_enum=False, length=11,
            ),
            nullable=False,
        ),
        sa.Column('supplier_id', sa.Integer(), nullable=False),
        sa.Column('purchase_order_id', sa.Integer(), nullable=True),
        sa.Column('requested_by', sa.String(length=120), nullable=False),
        sa.Column('requested_via', sa.String(length=20), nullable=False),
        sa.Column('params', sa.JSON(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('worker_id', sa.String(length=80), nullable=True),
        sa.Column('cancel_requested_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancel_requested_by', sa.String(length=120), nullable=True),
        sa.Column('run_id', sa.String(length=64), nullable=True),
        sa.Column('proposal_id', sa.Integer(), nullable=True),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('needs_human_reason', sa.Text(), nullable=True),
        sa.Column('model', sa.String(length=80), nullable=True),
        sa.Column('steps_total', sa.Integer(), server_default='0', nullable=False),
        sa.Column('model_calls', sa.Integer(), server_default='0', nullable=False),
        sa.Column('input_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('output_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING') OR finished_at IS NOT NULL",
            name='ck_browser_job_terminal_has_finished_at',
        ),
        sa.CheckConstraint(
            "kind <> 'STAGE_BASKET' OR purchase_order_id IS NOT NULL",
            name='ck_browser_job_basket_has_po',
        ),
        sa.ForeignKeyConstraint(['proposal_id'], ['agent_proposal.id']),
        sa.ForeignKeyConstraint(['purchase_order_id'], ['purchase_order.id']),
        sa.ForeignKeyConstraint(['supplier_id'], ['supplier.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_browser_job_status', 'browser_job', ['status', 'created_at'])
    op.create_index('ix_browser_job_po', 'browser_job', ['purchase_order_id'])
    op.create_index('ix_browser_job_supplier', 'browser_job', ['supplier_id', 'created_at'])
    op.create_table(
        'browser_job_step',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            'source',
            sa.Enum('SCRIPT', 'MODEL', 'POLICY', name='browserstepsource', native_enum=False, length=6),
            nullable=False,
        ),
        sa.Column('member', sa.String(length=60), nullable=False),
        sa.Column('input', sa.JSON(), nullable=False),
        sa.Column(
            'outcome',
            sa.Enum('OK', 'ERROR', 'REFUSED', name='browserstepoutcome', native_enum=False, length=7),
            nullable=False,
        ),
        sa.Column('output', sa.Text(), nullable=True),
        sa.Column('refusal_reason', sa.String(length=400), nullable=True),
        sa.Column('url', sa.String(length=2000), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('screenshot_asset_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['job_id'], ['browser_job.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['screenshot_asset_id'], ['media_asset.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('job_id', 'seq', name='uq_browser_job_step_seq'),
    )


def downgrade() -> None:
    op.drop_table('browser_job_step')
    op.drop_index('ix_browser_job_supplier', table_name='browser_job')
    op.drop_index('ix_browser_job_po', table_name='browser_job')
    op.drop_index('ix_browser_job_status', table_name='browser_job')
    op.drop_table('browser_job')
    op.drop_table('supplier_session')
