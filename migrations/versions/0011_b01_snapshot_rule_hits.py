"""Pin account rules to each successful relationship snapshot."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_b01_snapshot_rule_hits"
down_revision: str | None = "0010_b01_account_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "snapshot_rule_hits",
        sa.Column("snapshot_id", sa.String(36), primary_key=True),
        sa.Column("subject_x_user_id", sa.String(64), primary_key=True),
        sa.Column("rule_type", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("i_follow", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["relationship_snapshots.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("snapshot_rule_hits")
