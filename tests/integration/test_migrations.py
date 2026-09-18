import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config

from x_follow_list.persistence.migrations import HEAD_REVISION

EXPECTED_TABLES = {
    "users",
    "browser_provider_configs",
    "x_accounts",
    "scan_runs",
    "resource_leases",
    "idempotency_keys",
    "audit_logs",
    "auth_sessions",
    "x_account_memberships",
    "artifacts",
    "scan_staging_memberships",
    "scan_staging_progress",
    "relationship_snapshots",
    "snapshot_memberships",
    "relationship_states",
    "relationship_events",
    "browser_bind_sessions",
    "account_rules",
    "snapshot_rule_hits",
}


def migration_config(database_path: Path) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path.as_posix()}")
    return config


def table_names(database_path: Path) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    return {str(row[0]) for row in rows}


def test_baseline_migration_up_and_down(tmp_path: Path) -> None:
    database_path = tmp_path / "new-data-directory" / "migration.sqlite3"
    config = migration_config(database_path)

    command.upgrade(config, "head")

    assert EXPECTED_TABLES <= table_names(database_path)
    with sqlite3.connect(database_path) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        assert revision == (HEAD_REVISION,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    command.downgrade(config, "base")

    assert EXPECTED_TABLES.isdisjoint(table_names(database_path))


def test_auth_migration_backfills_owner_memberships(tmp_path: Path) -> None:
    database_path = tmp_path / "backfill.sqlite3"
    config = migration_config(database_path)
    command.upgrade(config, "0001_a02_baseline")
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO users (id,email,password_hash,role,created_at,updated_at) "
            "VALUES ('owner','owner@example.test','hash','OWNER',?,?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO browser_provider_configs "
            "(id,owner_user_id,provider_code,config_version,display_name,config_json,"
            "created_at,updated_at) VALUES "
            "('provider','owner','DIRECT_CHROME',1,'Chrome','{}',?,?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO x_accounts "
            "(id,owner_user_id,provider_config_id,profile_ref,profile_ref_hash,x_user_id,"
            "status,created_at,updated_at) VALUES "
            "('account','owner','provider','profile','hash','x-user','READY',?,?)",
            (now, now),
        )
        connection.commit()

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        membership = connection.execute(
            "SELECT x_account_id,user_id,role FROM x_account_memberships"
        ).fetchone()
    assert membership == ("account", "owner", "OWNER")
