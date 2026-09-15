import sqlite3
from pathlib import Path

import pytest

from x_follow_list.operations.backup import backup_database, restore_database


def read_value(database_path: Path) -> str:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute("SELECT value FROM release_marker").fetchone()
    assert row is not None
    return str(row[0])


def test_backup_restore_is_atomic_verified_and_preserves_target_on_corruption(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    restored = tmp_path / "restored.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE release_marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO release_marker VALUES ('phase-a')")
    metadata = backup_database(source, tmp_path / "backups" / "phase-a.db")

    restore_database(metadata.path, restored, expected_sha256=metadata.sha256)
    assert read_value(restored) == "phase-a"
    assert metadata.byte_size == metadata.path.stat().st_size
    assert len(metadata.sha256) == 64

    metadata.path.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="integrity"):
        restore_database(metadata.path, restored, expected_sha256=metadata.sha256)

    assert read_value(restored) == "phase-a"
    assert not list(tmp_path.rglob("*.tmp"))
