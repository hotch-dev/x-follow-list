"""Add durable interactive browser binding sessions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_a09_browser_binding"
down_revision: str | None = "0004_a06_scan_coordination"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "browser_bind_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.String(36), nullable=False),
        sa.Column("provider_config_id", sa.String(36), nullable=False),
        sa.Column("provider_code", sa.String(64), nullable=False),
        sa.Column("provider_config_version", sa.Integer(), nullable=False),
        sa.Column("profile_ref", sa.String(255), nullable=False),
        sa.Column("profile_ref_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("worker_id", sa.String(120), nullable=True),
        sa.Column("claim_token", sa.String(36), nullable=True),
        sa.Column("detected_x_user_id", sa.String(64), nullable=True),
        sa.Column("detected_username", sa.String(64), nullable=True),
        sa.Column("detected_display_name", sa.String(255), nullable=True),
        sa.Column("confirmed_account_id", sa.String(36), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["provider_config_id"], ["browser_provider_configs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_account_id"], ["x_accounts.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint("provider_config_version > 0", name="positive_config_version"),
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','AWAITING_CONFIRMATION','CONFIRMED',"
            "'CANCELLED','FAILED','EXPIRED')",
            name="valid_status",
        ),
    )
    op.create_index(
        "ix_browser_bind_sessions_claim",
        "browser_bind_sessions",
        ["status", "created_at", "id"],
    )
    op.create_index(
        "ix_browser_bind_sessions_owner",
        "browser_bind_sessions",
        ["owner_user_id", "created_at", "id"],
    )
    op.create_index(
        "uq_browser_bind_sessions_active_profile",
        "browser_bind_sessions",
        ["provider_config_id", "profile_ref_hash"],
        unique=True,
        sqlite_where=sa.text(
            "status IN ('QUEUED','RUNNING','AWAITING_CONFIRMATION','CONFIRMED')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_browser_bind_sessions_active_profile", table_name="browser_bind_sessions"
    )
    op.drop_index("ix_browser_bind_sessions_owner", table_name="browser_bind_sessions")
    op.drop_index("ix_browser_bind_sessions_claim", table_name="browser_bind_sessions")
    op.drop_table("browser_bind_sessions")
