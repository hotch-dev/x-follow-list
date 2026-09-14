from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from alembic import command
from fastapi import FastAPI
from pydantic import SecretStr

from x_follow_list.api.app import create_app
from x_follow_list.browser.fake import FakeBrowserProvider
from x_follow_list.browser.registry import BrowserProviderRegistry
from x_follow_list.config import RuntimeEnvironment, Settings
from x_follow_list.persistence.migrations import alembic_config


ORIGIN = "http://test"


async def prepared_app(tmp_path: Path) -> FastAPI:
    settings = Settings(
        environment=RuntimeEnvironment.TEST,
        data_dir=tmp_path,
        bootstrap_token=SecretStr("one-time-bootstrap-token"),
        app_origin=ORIGIN,
    )
    await asyncio.to_thread(command.upgrade, alembic_config(settings), "head")
    return create_app(
        settings,
        browser_provider_registry=BrowserProviderRegistry(
            [FakeBrowserProvider(profile_already_running=True)]
        ),
    )


async def login(client: httpx.AsyncClient) -> str:
    await client.post(
        "/api/v1/auth/bootstrap",
        headers={"Origin": ORIGIN},
        json={
            "login": "owner@example.test",
            "password": "a sufficiently long password",
            "bootstrap_token": "one-time-bootstrap-token",
        },
    )
    response = await client.post(
        "/api/v1/auth/session",
        headers={"Origin": ORIGIN},
        json={"login": "owner@example.test", "password": "a sufficiently long password"},
    )
    return str(response.json()["csrf_token"])


@pytest.mark.asyncio
async def test_owner_discovers_configures_tests_and_lists_provider_profiles(
    tmp_path: Path,
) -> None:
    app = await prepared_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        csrf = await login(client)
        providers = await client.get("/api/v1/browser-providers")
        created = await client.post(
            "/api/v1/browser-provider-configs",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
            json={
                "provider_code": "FAKE",
                "display_name": "Local fake",
                "config_version": 1,
                "config": {"api_url": "http://127.0.0.1:50325"},
                "secret_ref": "env://FAKE_PROVIDER_TOKEN",
            },
        )
        config_id = str(created.json()["id"])
        configs = await client.get("/api/v1/browser-provider-configs")
        tested = await client.post(
            f"/api/v1/browser-provider-configs/{config_id}/test",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
        )
        profiles = await client.get(
            f"/api/v1/browser-provider-configs/{config_id}/profiles"
        )

    await app.state.database.dispose()
    assert providers.status_code == 200
    assert providers.json()["items"][0]["code"] == "FAKE"
    assert created.status_code == 201
    assert created.json()["has_secret"] is True
    assert "secret_ref" not in created.text
    assert "api_url" not in created.text
    assert configs.json()["items"] == [created.json()]
    assert tested.status_code == 200
    assert tested.json()["provider_code"] == "FAKE"
    assert profiles.status_code == 200
    assert profiles.json()["items"] == [
        {
            "profile_ref": "fake-profile",
            "display_name": "Fake profile",
            "is_running": True,
        }
    ]


@pytest.mark.asyncio
async def test_provider_config_rejects_inline_secret_and_hides_foreign_ids(
    tmp_path: Path,
) -> None:
    app = await prepared_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        csrf = await login(client)
        inline = await client.post(
            "/api/v1/browser-provider-configs",
            headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
            json={
                "provider_code": "FAKE",
                "display_name": "Unsafe",
                "config_version": 1,
                "config": {
                    "api_url": "http://127.0.0.1:50325",
                    "token": "must-never-be-stored-or-echoed",
                },
                "secret_ref": "env://FAKE_PROVIDER_TOKEN",
            },
        )
        foreign = await client.get(
            "/api/v1/browser-provider-configs/not-owned/profiles"
        )

    await app.state.database.dispose()
    assert inline.status_code == 422
    assert "must-never-be-stored-or-echoed" not in inline.text
    assert foreign.status_code == 404

