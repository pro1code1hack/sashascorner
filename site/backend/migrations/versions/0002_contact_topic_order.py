"""site: allow the 'order' contact topic (whole cakes to order ahead)

Revision ID: site_0002
Revises: site_0001
Create Date: 2026-09-26

SQLite cannot alter a CHECK constraint in place, so batch mode rebuilds
site_contact_message. Touches only that site_* table.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "site_0002"
down_revision: str | Sequence[str] | None = "site_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "topic IN ('general', 'events', 'feedback', 'press', 'jobs')"
_NEW = "topic IN ('general', 'order', 'events', 'feedback', 'press', 'jobs')"


def upgrade() -> None:
    with op.batch_alter_table("site_contact_message", recreate="always") as batch:
        batch.drop_constraint("ck_site_contact_message_topic", type_="check")
        batch.create_check_constraint("ck_site_contact_message_topic", _NEW)


def downgrade() -> None:
    op.execute("DELETE FROM site_contact_message WHERE topic = 'order'")
    with op.batch_alter_table("site_contact_message", recreate="always") as batch:
        batch.drop_constraint("ck_site_contact_message_topic", type_="check")
        batch.create_check_constraint("ck_site_contact_message_topic", _OLD)
