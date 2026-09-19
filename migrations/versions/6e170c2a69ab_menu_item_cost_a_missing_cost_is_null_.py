"""menu_item_cost: a missing cost is NULL, never zero

Invariant 6 says a missing cost is `None`, never zero. `menu_item_cost` was created
with `cost_pence` and `cost_source` NOT NULL, which left the rollup two bad options
for an item with an unpriced ingredient: invent a partial sum, or write no row at
all. The first launders a guess into a cost; the second hides the item from the
margin screen that is supposed to flag it.

Making both columns nullable is the third option: the row exists, the item is shown
and flagged (`has_missing_cost`), and the cost reads NULL so nothing downstream can
mistake it for money.

Revision ID: 6e170c2a69ab
Revises: b8194b3caaa6
Create Date: 2026-09-19

"""

from collections.abc import Sequence

import cafeops.db.types  # custom column types (Qty, UTCDateTime)
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6e170c2a69ab"
down_revision: str | Sequence[str] | None = "b8194b3caaa6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow NULL cost and NULL source on the materialised cost cache."""
    with op.batch_alter_table("menu_item_cost") as batch:
        batch.alter_column(
            "cost_pence",
            existing_type=cafeops.db.types.Qty(precision=18, scale=6),
            nullable=True,
        )
        batch.alter_column(
            "cost_source",
            existing_type=sa.Enum(
                "INVOICE", "ESTIMATE", "SUPPLIER_FEED", name="pricesource", native_enum=False
            ),
            nullable=True,
        )


def downgrade() -> None:
    """Restore NOT NULL. Rows with an unknown cost cannot survive this.

    They are deleted rather than zero-filled: a zero cost is a lie, and the rollup
    job rebuilds every row on its next run.
    """
    op.execute(
        "DELETE FROM menu_item_cost WHERE cost_pence IS NULL OR cost_source IS NULL"
    )
    with op.batch_alter_table("menu_item_cost") as batch:
        batch.alter_column(
            "cost_pence",
            existing_type=cafeops.db.types.Qty(precision=18, scale=6),
            nullable=False,
        )
        batch.alter_column(
            "cost_source",
            existing_type=sa.Enum(
                "INVOICE", "ESTIMATE", "SUPPLIER_FEED", name="pricesource", native_enum=False
            ),
            nullable=False,
        )
