"""Add user timezone and one XLSX artifact per scan."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_a13_artifacts"
down_revision: str | None = "0008_a11_account_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC")
        )
    with op.batch_alter_table("artifacts") as batch_op:
        batch_op.create_unique_constraint(
            "uq_artifacts_scan_run_kind", ["scan_run_id", "kind"]
        )


def downgrade() -> None:
    with op.batch_alter_table("artifacts") as batch_op:
        batch_op.drop_constraint("uq_artifacts_scan_run_kind", type_="unique")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("timezone")
