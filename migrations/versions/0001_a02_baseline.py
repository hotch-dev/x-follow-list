"""Create the A-02 operational baseline schema."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_a02_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('OWNER', 'OPERATOR', 'VIEWER')", name="valid_role"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "browser_provider_configs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.String(36), nullable=False),
        sa.Column("provider_code", sa.String(64), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("secret_ref", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("owner_user_id", "display_name"),
    )
    op.create_index(
        "ix_browser_provider_configs_owner_provider",
        "browser_provider_configs",
        ["owner_user_id", "provider_code"],
    )
    op.create_table(
        "x_accounts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.String(36), nullable=False),
        sa.Column("provider_config_id", sa.String(36), nullable=False),
        sa.Column("profile_ref", sa.String(255), nullable=False),
        sa.Column("profile_ref_hash", sa.String(64), nullable=False),
        sa.Column("x_user_id", sa.String(64), nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("last_successful_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["provider_config_id"], ["browser_provider_configs.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "status IN ('READY', 'REAUTH_REQUIRED', 'DISABLED')", name="valid_status"
        ),
        sa.UniqueConstraint("owner_user_id", "x_user_id"),
        sa.UniqueConstraint("provider_config_id", "profile_ref_hash"),
    )
    op.create_table(
        "scan_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("x_account_id", sa.String(36), nullable=False),
        sa.Column("requested_by_user_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("progress_stage", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_summary", sa.String(500), nullable=True),
        sa.Column("follower_count", sa.Integer(), nullable=True),
        sa.Column("following_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["x_account_id"], ["x_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED')",
            name="valid_status",
        ),
    )
    op.create_index("ix_scan_runs_claim", "scan_runs", ["status", "created_at", "id"])
    op.create_table(
        "resource_leases",
        sa.Column("resource_key", sa.String(512), primary_key=True),
        sa.Column("owner_task_type", sa.String(32), nullable=False),
        sa.Column("owner_task_id", sa.String(36), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fencing_token > 0", name="positive_fencing_token"),
    )
    op.create_index("ix_resource_leases_expiry", "resource_leases", ["expires_at"])
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("scope", sa.String(120), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "scope", "idempotency_key"),
    )
    op.create_index("ix_idempotency_keys_expiry", "idempotency_keys", ["expires_at"])
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_user_id", sa.String(36), nullable=True),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("resource_type", sa.String(120), nullable=False),
        sa.Column("resource_id", sa.String(255), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_audit_logs_resource", "audit_logs", ["resource_type", "resource_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_resource", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_idempotency_keys_expiry", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_resource_leases_expiry", table_name="resource_leases")
    op.drop_table("resource_leases")
    op.drop_index("ix_scan_runs_claim", table_name="scan_runs")
    op.drop_table("scan_runs")
    op.drop_table("x_accounts")
    op.drop_index(
        "ix_browser_provider_configs_owner_provider", table_name="browser_provider_configs"
    )
    op.drop_table("browser_provider_configs")
    op.drop_table("users")

