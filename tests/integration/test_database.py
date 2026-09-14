from pathlib import Path

import pytest
from sqlalchemy import text

from x_follow_list.config import RuntimeEnvironment, Settings
from x_follow_list.persistence.database import Database


@pytest.mark.asyncio
async def test_sqlite_pragmas_apply_to_every_connection(tmp_path: Path) -> None:
    settings = Settings(
        environment=RuntimeEnvironment.TEST,
        data_dir=tmp_path,
        sqlite_busy_timeout_ms=4321,
    )
    database = Database.from_settings(settings)

    try:
        for _ in range(2):
            async with database.engine.connect() as connection:
                foreign_keys = await connection.scalar(text("PRAGMA foreign_keys"))
                journal_mode = await connection.scalar(text("PRAGMA journal_mode"))
                busy_timeout = await connection.scalar(text("PRAGMA busy_timeout"))

            assert foreign_keys == 1
            assert str(journal_mode).lower() == "wal"
            assert busy_timeout == 4321
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_async_session_factory_can_commit_and_read(tmp_path: Path) -> None:
    settings = Settings(environment=RuntimeEnvironment.TEST, data_dir=tmp_path)
    database = Database.from_settings(settings)

    try:
        async with database.engine.begin() as connection:
            await connection.execute(text("CREATE TABLE probe (value TEXT NOT NULL)"))

        async with database.session() as session:
            await session.execute(text("INSERT INTO probe (value) VALUES ('ready')"))
            await session.commit()

        async with database.session() as session:
            assert await session.scalar(text("SELECT value FROM probe")) == "ready"
    finally:
        await database.dispose()

