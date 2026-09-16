from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from x_follow_list.persistence.database import Database
from x_follow_list.persistence.migrations import HEAD_REVISION

logger = logging.getLogger(__name__)


async def database_is_ready(database: Database) -> bool:
    try:
        async with database.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != HEAD_REVISION:
                return False
            await connection.execute(
                text(
                    "UPDATE alembic_version SET version_num = version_num "
                    "WHERE version_num = :revision"
                ),
                {"revision": HEAD_REVISION},
            )
            await connection.rollback()
    except (OSError, SQLAlchemyError):
        logger.warning("database readiness check failed")
        return False
    return True


def storage_readiness(data_dir: Path, min_free_disk_bytes: int) -> tuple[str, str]:
    try:
        with tempfile.TemporaryFile(dir=data_dir) as probe:
            probe.write(b"readiness")
            probe.flush()
    except OSError:
        logger.warning("storage readiness check failed")
        return "unavailable", "unavailable"

    try:
        free_bytes = shutil.disk_usage(data_dir).free
    except OSError:
        logger.warning("disk readiness check failed")
        return "ready", "unavailable"
    return "ready", "ready" if free_bytes >= min_free_disk_bytes else "low"
