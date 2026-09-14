import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from alembic import command
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import text

from x_follow_list.api.app import create_app
from x_follow_list.application.binding import BrowserBindingService
from x_follow_list.config import RuntimeEnvironment, Settings
from x_follow_list.persistence.migrations import alembic_config


async def migrated_app(tmp_path: Path) -> FastAPI:
    settings = Settings(
        environment=RuntimeEnvironment.TEST,
        data_dir=tmp_path,
        bootstrap_token=SecretStr("one-time-bootstrap-token"),
        app_origin="http://test",
        session_ttl_seconds=3600,
    )
    await asyncio.to_thread(command.upgrade, alembic_config(settings), "head")
    return create_app(settings)


async def login_owner(client: httpx.AsyncClient, app: FastAPI) -> tuple[str, str]:
    bootstrap = await client.post(
        "/api/v1/auth/bootstrap",
        headers={"Origin": "http://test"},
        json={
            "login": "owner@example.test",
            "password": "a sufficiently long password",
            "bootstrap_token": "one-time-bootstrap-token",
        },
    )
    login = await client.post(
        "/api/v1/auth/session",
        headers={"Origin": "http://test"},
        json={
            "login": "owner@example.test",
            "password": "a sufficiently long password",
        },
    )
    owner_id = str(bootstrap.json()["id"])
    now = datetime.now(UTC).isoformat()
    async with app.state.database.session() as session:
        await session.execute(
            text(
                "INSERT INTO browser_provider_configs "
                "(id,owner_user_id,provider_code,config_version,display_name,config_json,"
                "created_at,updated_at) VALUES "
                "('provider',:owner,'DIRECT_CHROME',1,'Chrome','{}',:now,:now)"
            ),
            {"owner": owner_id, "now": now},
        )
        await session.commit()
    return owner_id, str(login.json()["csrf_token"])


@pytest.mark.asyncio
async def test_binding_api_creates_queries_and_confirms_detected_identity(
    tmp_path: Path,
) -> None:
    app = await migrated_app(tmp_path)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        owner_id, csrf_token = await login_owner(client, app)
        created = await client.post(
            "/api/v1/x-account-bind-sessions",
            headers={"Origin": "http://test", "X-CSRF-Token": csrf_token},
            json={"provider_config_id": "provider", "profile_ref": "profile-one"},
        )
        binding_id = str(created.json()["id"])
        service = BrowserBindingService(app.state.database)
        claim = await service.claim_next("binding-worker")
        assert claim is not None
        await service.record_detected_identity(
            claim,
            x_user_id="x-123",
            username="alice",
            display_name="Alice",
        )
        queried = await client.get(f"/api/v1/x-account-bind-sessions/{binding_id}")
        confirmed = await client.post(
            f"/api/v1/x-account-bind-sessions/{binding_id}/confirm",
            headers={"Origin": "http://test", "X-CSRF-Token": csrf_token},
        )

    await app.state.database.dispose()
    assert created.status_code == 202
    assert created.json()["status"] == "QUEUED"
    assert queried.status_code == 200
    assert queried.json()["status"] == "AWAITING_CONFIRMATION"
    assert queried.json()["detected_identity"] == {
        "x_user_id": "x-123",
        "username": "alice",
        "display_name": "Alice",
    }
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "CONFIRMED"
    assert confirmed.json()["account_id"]
    assert confirmed.json()["owner_user_id"] == owner_id


@pytest.mark.asyncio
async def test_binding_mutations_require_authentication_and_csrf(tmp_path: Path) -> None:
    app = await migrated_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    payload = {"provider_config_id": "provider", "profile_ref": "profile-one"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        unauthenticated = await client.post(
            "/api/v1/x-account-bind-sessions",
            headers={"Origin": "http://test", "X-CSRF-Token": "invalid"},
            json=payload,
        )
        _owner_id, csrf_token = await login_owner(client, app)
        missing_csrf = await client.post(
            "/api/v1/x-account-bind-sessions",
            headers={"Origin": "http://test"},
            json=payload,
        )
        created = await client.post(
            "/api/v1/x-account-bind-sessions",
            headers={"Origin": "http://test", "X-CSRF-Token": csrf_token},
            json=payload,
        )
        binding_id = str(created.json()["id"])
        cancelled = await client.post(
            f"/api/v1/x-account-bind-sessions/{binding_id}/cancel",
            headers={"Origin": "http://test", "X-CSRF-Token": csrf_token},
        )

    await app.state.database.dispose()
    assert unauthenticated.status_code == 401
    assert missing_csrf.status_code == 403
    assert cancelled.status_code == 204


@pytest.mark.asyncio
async def test_binding_request_rejects_secret_fields_without_echoing_them(
    tmp_path: Path,
) -> None:
    app = await migrated_app(tmp_path)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _owner_id, csrf_token = await login_owner(client, app)
        response = await client.post(
            "/api/v1/x-account-bind-sessions",
            headers={"Origin": "http://test", "X-CSRF-Token": csrf_token},
            json={
                "provider_config_id": "provider",
                "profile_ref": "profile-one",
                "password": "must-never-be-accepted-or-returned",
            },
        )

    await app.state.database.dispose()
    assert response.status_code == 422
    assert "must-never-be-accepted-or-returned" not in response.text
