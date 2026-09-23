"""po_line / purchase_order: the figures and codes behind an explanation

`purchase_order.note_codes` and four `po_line` columns. All additive and all nullable,
so an order written before this migration renders exactly as it did -- the bot treats a
NULL as "this order was built before the codes existed" and falls back to what it
already had, never to printing English.

Why each column exists rather than being derived at render time:

* `note_codes` -- `purchase_order.notes` is English prose, so the Russian bot dropped it
  entirely and the owner read quantities with no account of the par ceiling that cut them
  or the perishables invariant 5 kept out of the top-up. Spec 5.4 forbids a silent
  adjustment, and a sentence she cannot read is silent.
* `cover_days` -- the effective window a line was sized on. "Capped at 4 days" is the
  whole of the invariant 4 message and the number used to be recovered by regex out of
  `cap_reason`.
* `forecast_history_days` / `forecast_needed_days` -- the two figures invariant 9's
  replacement sentence is made of. Stored rather than read back from config, because the
  requirement is what it was when the order was sized.
* `checklist_requested_by` -- tier C carries no forecast (spec 4.7), so a quantity on a
  checklist line is somebody's decision and the line has to say whose.
* `par_level.auto_order_revoke_cause` -- the gate is STATELESS. `evaluate_gate` re-derives
  its verdict from current history, so the moment `auto_order_enabled` is already False it
  reports HOLD and the fact that a revocation happened has vanished. Without this column
  the morning digest -- the channel the owner actually reads -- cannot tell her that
  auto-ordering stopped yesterday, which is the gap `ARCHITECTURE.md` 8A.3 recorded.

Revision ID: 3c41d7a9e2b0
Revises: 2910136e57f5
Create Date: 2026-09-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import cafeops.db.types  # noqa: F401  -- registers Qty / UTCDateTime

# revision identifiers, used by Alembic.
revision: str = "3c41d7a9e2b0"
down_revision: str | Sequence[str] | None = "2910136e57f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("purchase_order", schema=None) as batch_op:
        batch_op.add_column(sa.Column("note_codes", sa.String(length=600), nullable=True))

    with op.batch_alter_table("par_level", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "auto_order_revoke_cause",
                sa.Enum(
                    "DRIFT_ABOVE_TOLERANCE",
                    "DRIFT_IN_TUNING_BAND",
                    "STREAK_BROKEN",
                    "TIER_NOT_A",
                    "PAR_LEVEL_REMOVED",
                    "NO_OBSERVATION",
                    name="revokecause",
                    native_enum=False,
                ),
                nullable=True,
            )
        )

    with op.batch_alter_table("po_line", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cover_days", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("forecast_history_days", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("forecast_needed_days", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("checklist_requested_by", sa.String(length=120), nullable=True)
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("po_line", schema=None) as batch_op:
        batch_op.drop_column("checklist_requested_by")
        batch_op.drop_column("forecast_needed_days")
        batch_op.drop_column("forecast_history_days")
        batch_op.drop_column("cover_days")

    with op.batch_alter_table("par_level", schema=None) as batch_op:
        batch_op.drop_column("auto_order_revoke_cause")

    with op.batch_alter_table("purchase_order", schema=None) as batch_op:
        batch_op.drop_column("note_codes")
