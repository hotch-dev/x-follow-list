import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter

import pytest
from alembic import command
from sqlalchemy import text

from x_follow_list.application.snapshots import (
    IncompleteScanError,
    ObservedMember,
    SnapshotCommitService,
)
from x_follow_list.config import RuntimeEnvironment, Settings
from x_follow_list.persistence.database import Database
from x_follow_list.persistence.migrations import alembic_config
from x_follow_list.release.metrics import process_peak_rss_bytes


async def prepared_database(tmp_path: Path) -> Database:
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
                "INSERT INTO browser_provider_configs (id,owner_user_id,provider_code,config_version,display_name,config_json,created_at,updated_at) VALUES ('p','u','DIRECT',1,'p','{}',:n,:n)"
            ),
            {"n": now},
        )
        await session.execute(
            text(
                "INSERT INTO x_accounts (id,owner_user_id,provider_config_id,profile_ref,profile_ref_hash,x_user_id,status,created_at,updated_at) VALUES ('a','u','p','p','h','self','READY',:n,:n)"
            ),
            {"n": now},
        )
        await session.commit()
    return database


async def add_run(database: Database, run_id: str) -> None:
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO scan_runs (id,x_account_id,requested_by_user_id,status,created_at) VALUES (:r,'a','u','RUNNING',:n)"
            ),
            {"r": run_id, "n": datetime.now(UTC).isoformat()},
        )
        await session.commit()


@pytest.mark.asyncio
async def test_two_complete_snapshots_commit_diff_streak_and_idempotency(tmp_path: Path) -> None:
    database = await prepared_database(tmp_path)
    service = SnapshotCommitService(database)
    await add_run(database, "run-1")
    await service.stage("run-1", "FOLLOWER", [ObservedMember("42", "old")])
    await service.stage(
        "run-1",
        "FOLLOWING",
        [
            ObservedMember("42", "old"),
            ObservedMember("99", "nobody"),
            ObservedMember("99", "nobody"),
        ],
    )
    await service.complete_side("run-1", "FOLLOWER")
    await service.complete_side("run-1", "FOLLOWING")
    first = await service.commit("run-1")

    await add_run(database, "run-2")
    await service.stage(
        "run-2", "FOLLOWING", [ObservedMember("42", "renamed"), ObservedMember("99", "nobody")]
    )
    await service.complete_side("run-2", "FOLLOWER")
    await service.complete_side("run-2", "FOLLOWING")
    second = await service.commit("run-2")
    replay = await service.commit("run-2")

    async with database.session() as session:
        event_types = (
            await session.scalars(
                text("SELECT event_type FROM relationship_events ORDER BY event_type")
            )
        ).all()
        streak_rows = (
            await session.execute(
                text("SELECT subject_x_user_id,non_followback_streak FROM relationship_states")
            )
        ).all()
        streaks: dict[str, int] = {str(row[0]): int(row[1]) for row in streak_rows}
        snapshots = await session.scalar(text("SELECT count(*) FROM relationship_snapshots"))
        staging = await session.scalar(text("SELECT count(*) FROM scan_staging_memberships"))
    await database.dispose()

    assert first.follower_count == 1 and first.following_count == 2
    assert second == replay
    assert event_types == ["LOST_FOLLOWER", "UNFOLLOWED_ME_AFTER_MUTUAL"]
    assert streaks == {"42": 1, "99": 2}
    assert snapshots == 2
    assert staging == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault_checkpoint",
    [
        "after_snapshot",
        "after_memberships",
        "after_states",
        "after_events",
        "after_run_update",
        "after_account_update",
    ],
)
async def test_incomplete_or_interrupted_commit_never_changes_the_baseline(
    tmp_path: Path, fault_checkpoint: str
) -> None:
    database = await prepared_database(tmp_path)
    await add_run(database, "incomplete")
    service = SnapshotCommitService(database)
    await service.stage("incomplete", "FOLLOWER", [ObservedMember("42")])
    await service.complete_side("incomplete", "FOLLOWER")

    with pytest.raises(IncompleteScanError):
        await service.commit("incomplete")

    await add_run(database, "faulted")
    await service.complete_side("faulted", "FOLLOWER")
    await service.complete_side("faulted", "FOLLOWING")

    def inject_failure(checkpoint: str) -> None:
        if checkpoint == fault_checkpoint:
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        await SnapshotCommitService(database, inject_failure).commit("faulted")

    async with database.session() as session:
        assert await session.scalar(text("SELECT count(*) FROM relationship_snapshots")) == 0
        status_rows = (await session.execute(text("SELECT id,status FROM scan_runs"))).all()
        statuses: dict[str, str] = {str(row[0]): str(row[1]) for row in status_rows}
    await database.dispose()
    assert statuses == {"incomplete": "RUNNING", "faulted": "RUNNING"}


@pytest.mark.asyncio
async def test_fifty_thousand_members_per_side_stays_within_baseline(tmp_path: Path) -> None:
    database = await prepared_database(tmp_path)
    await add_run(database, "large")
    service = SnapshotCommitService(database)
    members = [ObservedMember(str(index)) for index in range(50_000)]

    started = perf_counter()
    await service.stage("large", "FOLLOWER", members)
    await service.stage("large", "FOLLOWING", members)
    await service.complete_side("large", "FOLLOWER")
    await service.complete_side("large", "FOLLOWING")
    result = await service.commit("large")
    elapsed = perf_counter() - started
    peak_rss_bytes = process_peak_rss_bytes()
    print(f"A14_DIFF_SECONDS={elapsed:.6f} A14_PEAK_RSS_BYTES={peak_rss_bytes}")

    await database.dispose()
    assert result.follower_count == result.following_count == 50_000
    assert elapsed < 60
    assert peak_rss_bytes < 1536 * 1024 * 1024


@pytest.mark.asyncio
async def test_subject_can_reappear_after_an_absent_snapshot(tmp_path: Path) -> None:
    database = await prepared_database(tmp_path)
    service = SnapshotCommitService(database)
    for run_id, following in (("present", True), ("absent", False), ("returned", True)):
        await add_run(database, run_id)
        if following:
            await service.stage(run_id, "FOLLOWING", [ObservedMember("42")])
        await service.complete_side(run_id, "FOLLOWER")
        await service.complete_side(run_id, "FOLLOWING")
        await service.commit(run_id)

    async with database.session() as session:
        state = (
            await session.execute(
                text(
                    "SELECT state,non_followback_streak FROM relationship_states "
                    "WHERE subject_x_user_id='42'"
                )
            )
        ).one()
    await database.dispose()
    assert tuple(state) == ("NOT_FOLLOWING_BACK", 1)


@pytest.mark.asyncio
async def test_stale_staging_cleanup_keeps_recent_runs(tmp_path: Path) -> None:
    database = await prepared_database(tmp_path)
    service = SnapshotCommitService(database)
    await add_run(database, "stale")
    await add_run(database, "recent")
    await service.stage("stale", "FOLLOWER", [ObservedMember("old")])
    await service.stage("recent", "FOLLOWER", [ObservedMember("new")])
    async with database.session() as session:
        await session.execute(
            text("UPDATE scan_runs SET created_at=:old WHERE id='stale'"),
            {"old": (datetime.now(UTC) - timedelta(days=2)).isoformat()},
        )
        await session.commit()

    deleted = await service.cleanup_staging_before(datetime.now(UTC) - timedelta(days=1))

    async with database.session() as session:
        remaining = (
            await session.scalars(
                text("SELECT subject_x_user_id FROM scan_staging_memberships")
            )
        ).all()
    await database.dispose()
    assert deleted == 1
    assert remaining == ["new"]
