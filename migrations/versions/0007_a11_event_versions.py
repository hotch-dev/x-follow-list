"""Add optimistic versioning to relationship events."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_a11_event_versions"
down_revision: str | None = "0006_a09_binding_revalidation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("relationship_events") as batch_op:
        batch_op.add_column(
            sa.Column("version", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_relationship_events_positive_version", "version > 0"
        )


def downgrade() -> None:
    with op.batch_alter_table("relationship_events") as batch_op:
        batch_op.drop_constraint(
            "ck_relationship_events_positive_version", type_="check"
        )
        batch_op.drop_column("acknowledged_at")
        batch_op.drop_column("version")
