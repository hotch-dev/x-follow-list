"""Add local authentication, memberships, and protected artifact ownership."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_a03_auth"
down_revision: str | None = "0001_a02_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_auth_sessions_expiry", "auth_sessions", ["expires_at"])
    op.create_table(
        "x_account_memberships",
        sa.Column("x_account_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), primary_key=True),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["x_account_id"], ["x_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint("role IN ('OWNER', 'OPERATOR', 'VIEWER')", name="valid_role"),
    )
    op.create_index(
        "ix_x_account_memberships_user", "x_account_memberships", ["user_id", "x_account_id"]
    )
    op.execute(
        "INSERT INTO x_account_memberships "
        "(x_account_id,user_id,role,created_at,updated_at) "
        "SELECT id,owner_user_id,'OWNER',created_at,updated_at FROM x_accounts"
    )
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scan_run_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("storage_path", sa.String(1024), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'READY', 'FAILED', 'DELETED')", name="valid_status"
        ),
    )
    op.create_index("ix_artifacts_scan_run", "artifacts", ["scan_run_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_artifacts_scan_run", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_x_account_memberships_user", table_name="x_account_memberships")
    op.drop_table("x_account_memberships")
    op.drop_index("ix_auth_sessions_expiry", table_name="auth_sessions")
    op.drop_table("auth_sessions")
