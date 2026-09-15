import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import text

from x_follow_list.application.scan_coordination import ResourceLease, ScanClaim
from x_follow_list.browser.contracts import BrowserSession
from x_follow_list.browser.fake import FakeBrowserProvider
from x_follow_list.browser.registry import BrowserProviderRegistry
from x_follow_list.config import RuntimeEnvironment, Settings
from x_follow_list.persistence.database import Database
from x_follow_list.persistence.migrations import alembic_config
from x_follow_list.worker.browser_session import BrowserWork
from x_follow_list.worker.configured_scan import ConfiguredScanJob


@pytest.mark.asyncio
async def test_configured_scan_loads_account_provider_and_profile_from_database(
    tmp_path: Path,
) -> None:
    settings = Settings(environment=RuntimeEnvironment.TEST, data_dir=tmp_path)
    await asyncio.to_thread(command.upgrade, alembic_config(settings), "head")
    database = Database.from_settings(settings)
    now = datetime.now(UTC).isoformat()
    config = {"api_url": "http://127.0.0.1:50325"}
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
            {"config": json.dumps(config), "n": now},
        )
        await session.execute(
            text(
                "INSERT INTO x_accounts "
                "(id,owner_user_id,provider_config_id,profile_ref,profile_ref_hash,x_user_id,"
                "username,status,created_at,updated_at) VALUES "
                "('a','u','p','fake-profile','hash','self','alice','READY',:n,:n)"
            ),
            {"n": now},
        )
        await session.commit()

    received: tuple[str, str, str] | None = None

    def work_factory(profile_url: str) -> BrowserWork:
        async def work(
            claim: ScanClaim,
            _leases: tuple[ResourceLease, ...],
            browser: BrowserSession,
        ) -> None:
            nonlocal received
            received = (profile_url, claim.run_id, browser.profile_key)

        return work

    provider = FakeBrowserProvider()
    job = ConfiguredScanJob(
        database,
        BrowserProviderRegistry([provider]),
        work_factory,
        profile_url_factory=lambda username: f"http://fixture.test/{username}",
        headless=True,
    )
    claim = ScanClaim("run", "a", "worker", "claim")
    await job(claim, (ResourceLease("account", 1),))

    await database.dispose()
    assert received == ("http://fixture.test/alice", "run", "fake-profile")
    assert provider.stop_count == 1
