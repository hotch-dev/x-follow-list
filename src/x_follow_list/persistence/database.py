from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from x_follow_list.config import Settings


def _configure_sqlite_connection(dbapi_connection: Any, busy_timeout_ms: int) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms:d}")
    finally:
        cursor.close()


@dataclass(frozen=True, slots=True)
class Database:
    engine: AsyncEngine
    session: async_sessionmaker[AsyncSession]

    @classmethod
    def from_settings(cls, settings: Settings) -> Database:
        _ensure_data_directory(settings.data_dir)
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)

        @event.listens_for(engine.sync_engine, "connect")
        def on_connect(dbapi_connection: Any, _connection_record: Any) -> None:
            _configure_sqlite_connection(dbapi_connection, settings.sqlite_busy_timeout_ms)

        return cls(
            engine=engine,
            session=async_sessionmaker(engine, expire_on_commit=False),
        )

    async def dispose(self) -> None:
        await self.engine.dispose()


def _ensure_data_directory(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)

