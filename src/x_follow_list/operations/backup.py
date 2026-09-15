from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class BackupMetadata:
    path: Path
    sha256: str
    byte_size: int
    created_at: datetime


def backup_database(source: Path, destination: Path) -> BackupMetadata:
    if not source.is_file():
        raise FileNotFoundError(source)
    target = destination.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        source_connection = sqlite3.connect(source)
        target_connection = sqlite3.connect(temporary)
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()
        _require_integrity(temporary)
        digest, size = _file_metadata(temporary)
        os.replace(temporary, target)
        return BackupMetadata(target, digest, size, datetime.now(UTC))
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def restore_database(
    backup: Path, target: Path, *, expected_sha256: str | None = None
) -> None:
    source = backup.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    digest, _size = _file_metadata(source)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("backup integrity check failed: SHA-256 mismatch")
    try:
        _require_integrity(source)
    except sqlite3.DatabaseError as error:
        raise ValueError("backup integrity check failed") from error

    destination = target.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        source_connection = sqlite3.connect(source)
        target_connection = sqlite3.connect(temporary)
        try:
            source_connection.backup(target_connection)
        finally:
            target_connection.close()
            source_connection.close()
        _require_integrity(temporary)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _require_integrity(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()
    if result is None or result[0] != "ok":
        raise ValueError("database integrity check failed")


def _file_metadata(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size
