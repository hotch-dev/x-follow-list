"""Add staging and atomic relationship snapshot storage."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_a05_snapshots"
down_revision: str | None = "0002_a03_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scan_staging_memberships",
        sa.Column("scan_run_id", sa.String(36), primary_key=True),
        sa.Column("relationship_type", sa.String(16), primary_key=True),
        sa.Column("subject_x_user_id", sa.String(64), primary_key=True),
        sa.Column("username", sa.String(64)),
        sa.Column("display_name", sa.String(255)),
        sa.Column("avatar_url", sa.String(2048)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"], ondelete="CASCADE"),
        sa.CheckConstraint("relationship_type IN ('FOLLOWER','FOLLOWING')", name="valid_type"),
    )
    op.create_table(
        "scan_staging_progress",
        sa.Column("scan_run_id", sa.String(36), primary_key=True),
        sa.Column("relationship_type", sa.String(16), primary_key=True),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("terminal", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "relationship_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("x_account_id", sa.String(36), nullable=False),
        sa.Column("scan_run_id", sa.String(36), nullable=False, unique=True),
        sa.Column("follower_count", sa.Integer(), nullable=False),
        sa.Column("following_count", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["x_account_id"], ["x_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_snapshots_account_time", "relationship_snapshots", ["x_account_id", "captured_at", "id"]
    )
    op.create_table(
        "snapshot_memberships",
        sa.Column("snapshot_id", sa.String(36), primary_key=True),
        sa.Column("subject_x_user_id", sa.String(64), primary_key=True),
        sa.Column("follows_me", sa.Boolean(), nullable=False),
        sa.Column("i_follow", sa.Boolean(), nullable=False),
        sa.Column("username", sa.String(64)),
        sa.Column("display_name", sa.String(255)),
        sa.Column("avatar_url", sa.String(2048)),
        sa.ForeignKeyConstraint(["snapshot_id"], ["relationship_snapshots.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "relationship_states",
        sa.Column("x_account_id", sa.String(36), primary_key=True),
        sa.Column("subject_x_user_id", sa.String(64), primary_key=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("non_followback_streak", sa.Integer(), nullable=False),
        sa.Column("last_snapshot_id", sa.String(36), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["x_account_id"], ["x_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["last_snapshot_id"], ["relationship_snapshots.id"]),
    )
    op.create_table(
        "relationship_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("x_account_id", sa.String(36), nullable=False),
        sa.Column("scan_run_id", sa.String(36), nullable=False),
        sa.Column("subject_x_user_id", sa.String(64), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("dedupe_key", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["x_account_id"], ["x_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_events_account_time", "relationship_events", ["x_account_id", "created_at", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_events_account_time", table_name="relationship_events")
    op.drop_table("relationship_events")
    op.drop_table("relationship_states")
    op.drop_table("snapshot_memberships")
    op.drop_index("ix_snapshots_account_time", table_name="relationship_snapshots")
    op.drop_table("relationship_snapshots")
    op.drop_table("scan_staging_progress")
    op.drop_table("scan_staging_memberships")
