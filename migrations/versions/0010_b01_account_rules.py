"""Add persistent account rules and conflict event lifecycle fields."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_b01_account_rules"
down_revision: str | None = "0009_a13_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("x_account_id", sa.String(36), nullable=False),
        sa.Column("subject_x_user_id", sa.String(64), nullable=False),
        sa.Column("rule_type", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["x_account_id"], ["x_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("x_account_id", "subject_x_user_id"),
        sa.CheckConstraint(
            "rule_type IN ('ALLOWLIST','BUSINESS_BLOCKLIST')", name="valid_rule_type"
        ),
        sa.CheckConstraint("version > 0", name="positive_rule_version"),
    )
    op.create_index("ix_account_rules_account_type", "account_rules", ["x_account_id", "rule_type"])
    with op.batch_alter_table("relationship_events") as batch_op:
        batch_op.add_column(sa.Column("rule_reason", sa.String(500), nullable=True))
        batch_op.add_column(sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("relationship_events") as batch_op:
        batch_op.drop_column("resolved_at")
        batch_op.drop_column("last_seen_at")
        batch_op.drop_column("rule_reason")
    op.drop_index("ix_account_rules_account_type", table_name="account_rules")
    op.drop_table("account_rules")
