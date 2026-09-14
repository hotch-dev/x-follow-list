import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from alembic import command
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import text

from x_follow_list.api.app import create_app
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


async def bootstrap_owner(client: httpx.AsyncClient) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/bootstrap",
        headers={"Origin": "http://test"},
        json={
            "login": "owner@example.test",
            "password": "a sufficiently long password",
            "bootstrap_token": "one-time-bootstrap-token",
        },
    )


@pytest.mark.asyncio
async def test_owner_bootstrap_succeeds_once_and_is_audited(tmp_path: Path) -> None:
    app = await migrated_app(tmp_path)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await bootstrap_owner(client)
        second = await bootstrap_owner(client)

    async with app.state.database.session() as session:
        user_count = await session.scalar(text("SELECT count(*) FROM users"))
        audit_actions = (
            await session.scalars(text("SELECT action FROM audit_logs ORDER BY created_at"))
        ).all()
        password_hash = await session.scalar(text("SELECT password_hash FROM users"))
    await app.state.database.dispose()

    assert first.status_code == 201
    assert first.json()["role"] == "OWNER"
    assert second.status_code == 409
    assert second.json()["code"] == "BOOTSTRAP_ALREADY_COMPLETED"
    assert user_count == 1
    assert audit_actions == ["AUTH_OWNER_BOOTSTRAPPED"]
    assert password_hash != "a sufficiently long password"


@pytest.mark.asyncio
async def test_login_uses_secure_cookie_and_csrf_protected_logout(tmp_path: Path) -> None:
    app = await migrated_app(tmp_path)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await bootstrap_owner(client)).status_code == 201
        login = await client.post(
            "/api/v1/auth/session",
            headers={"Origin": "http://test"},
            json={"login": "owner@example.test", "password": "a sufficiently long password"},
        )
        raw_session_token = client.cookies.get("x_follow_list_session")
        csrf_token = login.json()["csrf_token"]
        current = await client.get("/api/v1/auth/session")
        missing_csrf = await client.delete(
            "/api/v1/auth/session", headers={"Origin": "http://test"}
        )
        logout = await client.delete(
            "/api/v1/auth/session",
            headers={"Origin": "http://test", "X-CSRF-Token": csrf_token},
        )
        after_logout = await client.get("/api/v1/auth/session")

    async with app.state.database.session() as session:
        audit_actions = (
            await session.scalars(text("SELECT action FROM audit_logs ORDER BY created_at"))
        ).all()
        stored_token_hash = await session.scalar(text("SELECT token_hash FROM auth_sessions"))
    await app.state.database.dispose()

    cookie = login.headers["set-cookie"].lower()
    assert login.status_code == 201
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert raw_session_token
    assert raw_session_token != stored_token_hash
    assert current.status_code == 200
    assert current.json()["login"] == "owner@example.test"
    assert missing_csrf.status_code == 403
    assert logout.status_code == 204
    assert after_logout.status_code == 401
    assert audit_actions == [
        "AUTH_OWNER_BOOTSTRAPPED",
        "AUTH_SESSION_CREATED",
        "AUTH_SESSION_REVOKED",
    ]


@pytest.mark.asyncio
async def test_session_expiry_and_bad_login_fail_without_account_enumeration(
    tmp_path: Path,
) -> None:
    app = await migrated_app(tmp_path)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await bootstrap_owner(client)
        missing = await client.post(
            "/api/v1/auth/session",
            headers={"Origin": "http://test"},
            json={"login": "missing@example.test", "password": "a wrong long password"},
        )
        wrong = await client.post(
            "/api/v1/auth/session",
            headers={"Origin": "http://test"},
            json={"login": "owner@example.test", "password": "a wrong long password"},
        )
        login = await client.post(
            "/api/v1/auth/session",
            headers={"Origin": "http://test"},
            json={"login": "owner@example.test", "password": "a sufficiently long password"},
        )
        async with app.state.database.session() as session:
            await session.execute(
                text("UPDATE auth_sessions SET expires_at = :expired"),
                {"expired": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
            )
            await session.commit()
        expired = await client.get("/api/v1/auth/session")

    await app.state.database.dispose()
    assert missing.status_code == wrong.status_code == 401
    assert {
        key: missing.json()[key] for key in ("code", "message", "details")
    } == {key: wrong.json()[key] for key in ("code", "message", "details")}
    assert login.status_code == 201
    assert expired.status_code == 401


@pytest.mark.asyncio
async def test_state_changes_require_an_exact_allowed_origin(tmp_path: Path) -> None:
    app = await migrated_app(tmp_path)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/bootstrap",
            headers={"Origin": "https://evil.example"},
            json={
                "login": "owner@example.test",
                "password": "a sufficiently long password",
                "bootstrap_token": "one-time-bootstrap-token",
            },
        )

    await app.state.database.dispose()
    assert response.status_code == 403
    assert response.json()["code"] == "ORIGIN_REJECTED"


@pytest.mark.asyncio
async def test_validation_errors_do_not_echo_authentication_secrets(tmp_path: Path) -> None:
    app = await migrated_app(tmp_path)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/bootstrap",
            headers={"Origin": "http://test"},
            json={
                "login": "owner@example.test",
                "password": "tinysecret",
                "bootstrap_token": "short-token",
                "unexpected": "also-sensitive",
            },
        )

    await app.state.database.dispose()
    assert response.status_code == 422
    assert "tinysecret" not in response.text
    assert "short-token" not in response.text
    assert "also-sensitive" not in response.text


@pytest.mark.asyncio
async def test_production_session_cookie_is_secure(tmp_path: Path) -> None:
    settings = Settings(
        environment=RuntimeEnvironment.PRODUCTION,
        data_dir=tmp_path,
        master_key=SecretStr("a-production-master-key-with-32-characters"),
        bootstrap_token=SecretStr("one-time-bootstrap-token"),
        app_origin="https://monitor.example",
    )
    await asyncio.to_thread(command.upgrade, alembic_config(settings), "head")
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport, base_url="https://monitor.example"
    ) as client:
        bootstrap = await client.post(
            "/api/v1/auth/bootstrap",
            headers={"Origin": "https://monitor.example"},
            json={
                "login": "owner@example.test",
                "password": "a sufficiently long password",
                "bootstrap_token": "one-time-bootstrap-token",
            },
        )
        login = await client.post(
            "/api/v1/auth/session",
            headers={"Origin": "https://monitor.example"},
            json={"login": "owner@example.test", "password": "a sufficiently long password"},
        )

    await app.state.database.dispose()
    assert bootstrap.status_code == 201
    assert login.status_code == 201
    assert "secure" in login.headers["set-cookie"].lower()
