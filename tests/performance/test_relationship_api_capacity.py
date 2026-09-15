import asyncio
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import pytest
from alembic import command
from sqlalchemy import text

from x_follow_list.application.monitoring import MonitoringQueryService
from x_follow_list.config import RuntimeEnvironment, Settings
from x_follow_list.persistence.database import Database
from x_follow_list.persistence.migrations import alembic_config


@pytest.mark.asyncio
async def test_relationship_query_p95_with_fifty_thousand_per_side(tmp_path: Path) -> None:
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
                "created_at,updated_at) VALUES ('p','u','DIRECT_CHROME',1,'p','{}',:n,:n)"
            ),
            {"n": now},
        )
        await session.execute(
            text(
                "INSERT INTO x_accounts "
                "(id,owner_user_id,provider_config_id,profile_ref,profile_ref_hash,x_user_id,"
                "status,created_at,updated_at) VALUES "
                "('a','u','p','profile','hash','self','READY',:n,:n)"
            ),
            {"n": now},
        )
        await session.execute(
            text(
                "INSERT INTO x_account_memberships "
                "(x_account_id,user_id,role,created_at,updated_at) "
                "VALUES ('a','u','OWNER',:n,:n)"
            ),
            {"n": now},
        )
        await session.execute(
            text(
                "INSERT INTO scan_runs "
                "(id,x_account_id,requested_by_user_id,status,created_at,finished_at) "
                "VALUES ('run','a','u','SUCCESS',:n,:n)"
            ),
            {"n": now},
        )
        await session.execute(
            text(
                "INSERT INTO relationship_snapshots "
                "(id,x_account_id,scan_run_id,follower_count,following_count,captured_at) "
                "VALUES ('snapshot','a','run',50000,50000,:n)"
            ),
            {"n": now},
        )
        for start in range(0, 100_000, 5_000):
            rows = [
                {
                    "id": f"{index:06d}",
                    "follows": index < 50_000,
                    "following": index >= 50_000,
                    "state": "FOLLOWS_ME_ONLY" if index < 50_000 else "NOT_FOLLOWING_BACK",
                    "now": now,
                }
                for index in range(start, start + 5_000)
            ]
            await session.execute(
                text(
                    "INSERT INTO snapshot_memberships "
                    "(snapshot_id,subject_x_user_id,follows_me,i_follow,username) "
                    "VALUES ('snapshot',:id,:follows,:following,:id)"
                ),
                rows,
            )
            await session.execute(
                text(
                    "INSERT INTO relationship_states "
                    "(x_account_id,subject_x_user_id,state,non_followback_streak,"
                    "last_snapshot_id,updated_at) "
                    "VALUES ('a',:id,:state,1,'snapshot',:now)"
                ),
                rows,
            )
        await session.commit()

    service = MonitoringQueryService(database)
    timings: list[float] = []
    for _ in range(20):
        started = perf_counter()
        items, _cursor = await service.list_relationships(
            "u",
            "a",
            state="NOT_FOLLOWING_BACK",
            search=None,
            limit=50,
            cursor=None,
        )
        timings.append(perf_counter() - started)
        assert len(items) == 50

    await database.dispose()
    timings.sort()
    p95 = timings[18]
    assert p95 < 0.5
