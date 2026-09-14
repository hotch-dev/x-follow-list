"""Add durable scan claim ownership and heartbeat fields."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_a06_scan_coordination"
down_revision: str | None = "0003_a05_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("scan_runs", sa.Column("worker_id", sa.String(120), nullable=True))
    op.add_column("scan_runs", sa.Column("claim_token", sa.String(36), nullable=True))
    op.add_column("scan_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_scan_runs_worker_claim", "scan_runs", ["worker_id", "claim_token"])


def downgrade() -> None:
    op.drop_index("ix_scan_runs_worker_claim", table_name="scan_runs")
    op.drop_column("scan_runs", "heartbeat_at")
    op.drop_column("scan_runs", "claim_token")
    op.drop_column("scan_runs", "worker_id")
