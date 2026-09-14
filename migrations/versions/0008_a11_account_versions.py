"""Add optimistic versioning to X accounts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_a11_account_versions"
down_revision: str | None = "0007_a11_event_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("x_accounts") as batch_op:
        batch_op.add_column(
            sa.Column("version", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.create_check_constraint("ck_x_accounts_positive_version", "version > 0")


def downgrade() -> None:
    with op.batch_alter_table("x_accounts") as batch_op:
        batch_op.drop_constraint("ck_x_accounts_positive_version", type_="check")
        batch_op.drop_column("version")
