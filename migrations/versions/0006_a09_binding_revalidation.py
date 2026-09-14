"""Link browser binding sessions to accounts being revalidated."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_a09_binding_revalidation"
down_revision: str | None = "0005_a09_browser_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "uq_browser_bind_sessions_active_profile", table_name="browser_bind_sessions"
    )
    with op.batch_alter_table("browser_bind_sessions") as batch_op:
        batch_op.add_column(sa.Column("target_account_id", sa.String(36), nullable=True))
        batch_op.create_foreign_key(
            "fk_bind_session_target_account",
            "x_accounts",
            ["target_account_id"],
            ["id"],
            ondelete="CASCADE",
        )
    op.create_index(
        "uq_browser_bind_sessions_active_profile",
        "browser_bind_sessions",
        ["provider_config_id", "profile_ref_hash"],
        unique=True,
        sqlite_where=sa.text("status IN ('QUEUED','RUNNING','AWAITING_CONFIRMATION')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_browser_bind_sessions_active_profile", table_name="browser_bind_sessions"
    )
    with op.batch_alter_table("browser_bind_sessions") as batch_op:
        batch_op.drop_constraint("fk_bind_session_target_account", type_="foreignkey")
        batch_op.drop_column("target_account_id")
    op.create_index(
        "uq_browser_bind_sessions_active_profile",
        "browser_bind_sessions",
        ["provider_config_id", "profile_ref_hash"],
        unique=True,
        sqlite_where=sa.text(
            "status IN ('QUEUED','RUNNING','AWAITING_CONFIRMATION','CONFIRMED')"
        ),
    )
