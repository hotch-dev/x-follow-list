import asyncio
import shutil
import tempfile
from pathlib import Path

import httpx
import pytest
from alembic import command

from x_follow_list.api.app import create_app
from x_follow_list.config import RuntimeEnvironment, Settings
from x_follow_list.persistence.migrations import alembic_config


@pytest.mark.asyncio
async def test_readiness_reports_migrated_writable_database(tmp_path: Path) -> None:
    settings = Settings(environment=RuntimeEnvironment.TEST, data_dir=tmp_path)
    await asyncio.to_thread(command.upgrade, alembic_config(settings), "head")
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")

    await app.state.database.dispose()
    assert response.status_code == 200
    assert response.json() == {
        "service": "x-follow-list-api",
        "status": "ready",
        "database": "ready",
        "storage": "ready",
        "disk": "ready",
    }


@pytest.mark.asyncio
async def test_readiness_fails_closed_when_database_is_not_migrated(tmp_path: Path) -> None:
    settings = Settings(environment=RuntimeEnvironment.TEST, data_dir=tmp_path)
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")

    await app.state.database.dispose()
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["database"] == "unavailable"
    assert "traceback" not in response.text.lower()


@pytest.mark.asyncio
async def test_readiness_rejects_low_disk_without_exposing_paths(tmp_path: Path) -> None:
    available = shutil.disk_usage(tmp_path).free
    settings = Settings(
        environment=RuntimeEnvironment.TEST,
        data_dir=tmp_path,
        min_free_disk_bytes=available + 1,
    )
    await asyncio.to_thread(command.upgrade, alembic_config(settings), "head")
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")

    await app.state.database.dispose()
    assert response.status_code == 503
    assert response.json() == {
        "service": "x-follow-list-api",
        "status": "not_ready",
        "database": "ready",
        "storage": "ready",
        "disk": "low",
    }
    assert str(tmp_path) not in response.text


@pytest.mark.asyncio
async def test_readiness_rejects_unwritable_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(environment=RuntimeEnvironment.TEST, data_dir=tmp_path)
    await asyncio.to_thread(command.upgrade, alembic_config(settings), "head")
    app = create_app(settings)

    def deny_probe(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("private storage path must not leak")

    monkeypatch.setattr(tempfile, "TemporaryFile", deny_probe)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")

    await app.state.database.dispose()
    assert response.status_code == 503
    assert response.json()["storage"] == "unavailable"
    assert "private storage path" not in response.text
