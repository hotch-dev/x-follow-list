import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import text

from x_follow_list.application.binding import BrowserBindingService
from x_follow_list.browser.contracts import BrowserSession
from x_follow_list.browser.fake import FakeBrowserProvider
from x_follow_list.browser.registry import BrowserProviderRegistry
from x_follow_list.config import RuntimeEnvironment, Settings
from x_follow_list.persistence.database import Database
from x_follow_list.persistence.migrations import alembic_config
from x_follow_list.worker.browser_binding import DetectedIdentity
from x_follow_list.worker.configured_binding import ConfiguredBindingJob


@pytest.mark.asyncio
async def test_configured_binding_loads_claimed_provider_and_records_identity(
    tmp_path: Path,
) -> None:
    settings = Settings(environment=RuntimeEnvironment.TEST, data_dir=tmp_path)
    await asyncio.to_thread(command.upgrade, alembic_config(settings), "head")
    database = Database.from_settings(settings)
    now = datetime.now(UTC).isoformat()
    async with database.session() as session:
        await session.execute(
            text("INSERT INTO users VALUES ('u','u@test','h','OWNER',:n,:n)"), {"n": now}
        )
        await session.execute(
            text(
                "INSERT INTO browser_provider_configs "
                "(id,owner_user_id,provider_code,config_version,display_name,config_json,"
                "created_at,updated_at) VALUES "
                "('p','u','FAKE',1,'fake',:config,:n,:n)"
            ),
            {
                "config": json.dumps({"api_url": "http://127.0.0.1:50325"}),
                "n": now,
            },
        )
        await session.commit()

    service = BrowserBindingService(database)
    binding = await service.create("u", "p", "fake-profile")
    claim = await service.claim_next("worker")
    assert claim is not None

    observed_profile: str | None = None

    async def read_identity(browser: BrowserSession) -> DetectedIdentity:
        nonlocal observed_profile
        observed_profile = browser.profile_key
        return DetectedIdentity("x-123", "alice", "Alice")

    provider = FakeBrowserProvider()
    job = ConfiguredBindingJob(
        database,
        service,
        BrowserProviderRegistry([provider]),
        read_identity,
    )
    await job(claim)

    result = await service.get("u", binding.session_id)
    await database.dispose()
    assert result.status == "AWAITING_CONFIRMATION"
    assert result.detected_x_user_id == "x-123"
    assert observed_profile == "fake-profile"
    assert provider.stop_count == 1
