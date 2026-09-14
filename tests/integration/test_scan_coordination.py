import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import text

from x_follow_list.application.scan_coordination import (
    IdempotencyConflictError,
    LeaseConflictError,
    LeaseLostError,
    ScanCoordinator,
)
from x_follow_list.application.snapshots import ObservedMember, SnapshotCommitService
from x_follow_list.config import RuntimeEnvironment, Settings
from x_follow_list.persistence.database import Database
from x_follow_list.persistence.migrations import alembic_config


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
                "INSERT INTO browser_provider_configs "
                "(id,owner_user_id,provider_code,config_version,display_name,config_json,"
                "created_at,updated_at) VALUES "
                "('p','u','DIRECT_CHROME',1,'p','{}',:n,:n)"
            ),
            {"n": now},
        )
        for account_id, profile_hash in (("a", "shared-hash"), ("b", "other-hash")):
            await session.execute(
                text(
                    "INSERT INTO x_accounts "
                    "(id,owner_user_id,provider_config_id,profile_ref,profile_ref_hash,"
                    "x_user_id,status,created_at,updated_at) VALUES "
                    "(:a,'u','p','shared-profile',:profile_hash,:x,'READY',:n,:n)"
                ),
                {
                    "a": account_id,
                    "profile_hash": profile_hash,
                    "x": f"self-{account_id}",
                    "n": now,
                },
            )
        await session.commit()
    return database


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_and_rejects_key_reuse_with_different_request(
    tmp_path: Path,
) -> None:
    database = await prepared_database(tmp_path)
    coordinator = ScanCoordinator(database)

    first = await coordinator.enqueue("u", "a", "request-1", "hash-a")
    replay = await coordinator.enqueue("u", "a", "request-1", "hash-a")
    with pytest.raises(IdempotencyConflictError):
        await coordinator.enqueue("u", "a", "request-1", "hash-b")

    async with database.session() as session:
        count = await session.scalar(text("SELECT count(*) FROM scan_runs"))
        status = await session.scalar(
            text("SELECT status FROM scan_runs WHERE id=:id"), {"id": first}
        )
    await database.dispose()
    assert replay == first
    assert count == 1
    assert status == "QUEUED"


@pytest.mark.asyncio
async def test_two_workers_can_only_claim_a_queued_run_once(tmp_path: Path) -> None:
    database = await prepared_database(tmp_path)
    coordinator = ScanCoordinator(database)
    run_id = await coordinator.enqueue("u", "a", "claim-once", "hash")

    claims = await asyncio.gather(
        coordinator.claim_next("worker-1"), coordinator.claim_next("worker-2")
    )

    claimed = [claim for claim in claims if claim is not None]
    async with database.session() as session:
        row = (
            await session.execute(
                text("SELECT status,worker_id,claim_token FROM scan_runs WHERE id=:id"),
                {"id": run_id},
            )
        ).one()
    await database.dispose()
    assert len(claimed) == 1
    assert claimed[0].run_id == run_id
    assert row[0] == "RUNNING"
    assert row[1] == claimed[0].worker_id
    assert row[2] == claimed[0].claim_token


@pytest.mark.asyncio
async def test_same_profile_conflict_does_not_leave_partial_account_lease(tmp_path: Path) -> None:
    database = await prepared_database(tmp_path)
    coordinator = ScanCoordinator(database, lease_ttl=timedelta(seconds=30))
    await coordinator.enqueue("u", "a", "a", "a")
    await coordinator.enqueue("u", "b", "b", "b")
    claim_a = await coordinator.claim_next("worker-a")
    claim_b = await coordinator.claim_next("worker-b")
    assert claim_a is not None and claim_b is not None

    leases_a = await coordinator.acquire_scan_leases(claim_a)
    now = datetime.now(UTC)
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO resource_leases "
                "(resource_key,owner_task_type,owner_task_id,fencing_token,"
                "heartbeat_at,expires_at) "
                "VALUES ('browser-profile:p:other-hash','SCAN','profile-owner',1,:now,:expires)"
            ),
            {"now": now.isoformat(), "expires": (now + timedelta(seconds=30)).isoformat()},
        )
        await session.commit()
    with pytest.raises(LeaseConflictError):
        await coordinator.acquire_scan_leases(claim_b)
    await coordinator.finish_unleased_failed(
        claim_b, "RESOURCE_BUSY", "required browser profile is already in use"
    )

    async with database.session() as session:
        active_b_account = await session.scalar(
            text(
                "SELECT count(*) FROM resource_leases "
                "WHERE resource_key='x-account:b' AND expires_at > :now"
            ),
            {"now": datetime.now(UTC).isoformat()},
        )
        run_b_status = await session.scalar(
            text("SELECT status FROM scan_runs WHERE id=:run"), {"run": claim_b.run_id}
        )
    await database.dispose()
    assert [lease.resource_key for lease in leases_a] == [
        "x-account:a",
        "browser-profile:p:shared-hash",
    ]
    assert active_b_account == 0
    assert run_b_status == "FAILED"


@pytest.mark.asyncio
async def test_heartbeat_renews_both_leases_and_fenced_failure_releases_them(
    tmp_path: Path,
) -> None:
    database = await prepared_database(tmp_path)
    coordinator = ScanCoordinator(database, lease_ttl=timedelta(seconds=30))
    run_id = await coordinator.enqueue("u", "a", "heartbeat", "heartbeat")
    claim = await coordinator.claim_next("worker")
    assert claim is not None
    leases = await coordinator.acquire_scan_leases(claim)

    await coordinator.heartbeat(claim, leases)
    await coordinator.finish_failed(claim, leases, "PARSER_CHANGED", "fixture rejected")

    now = datetime.now(UTC)
    async with database.session() as session:
        run = (
            await session.execute(
                text("SELECT status,error_code,heartbeat_at FROM scan_runs WHERE id=:run"),
                {"run": run_id},
            )
        ).one()
        expiries = (
            await session.scalars(
                text("SELECT expires_at FROM resource_leases WHERE owner_task_id=:run"),
                {"run": run_id},
            )
        ).all()
    await database.dispose()
    assert run[0:2] == ("FAILED", "PARSER_CHANGED")
    assert run[2] is not None
    assert all(ScanCoordinator._as_datetime(value) <= now for value in expiries)


@pytest.mark.asyncio
async def test_heartbeat_loss_stops_worker_and_expired_lease_gets_higher_fence(
    tmp_path: Path,
) -> None:
    database = await prepared_database(tmp_path)
    coordinator = ScanCoordinator(database, lease_ttl=timedelta(seconds=30))
    run_id = await coordinator.enqueue("u", "a", "lost", "lost")
    claim = await coordinator.claim_next("worker-old")
    assert claim is not None
    old_leases = await coordinator.acquire_scan_leases(claim)

    async with database.session() as session:
        await session.execute(
            text("UPDATE resource_leases SET expires_at=:past WHERE owner_task_id=:run"),
            {"past": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(), "run": run_id},
        )
        await session.commit()

    with pytest.raises(LeaseLostError):
        await coordinator.heartbeat(claim, old_leases)
    failed = await coordinator.recover_expired_runs()
    replacement_id = await coordinator.enqueue("u", "a", "replacement", "replacement")
    replacement = await coordinator.claim_next("worker-new")
    assert replacement is not None and replacement.run_id == replacement_id
    new_leases = await coordinator.acquire_scan_leases(replacement)

    await database.dispose()
    assert failed == [run_id]
    assert all(
        new.fencing_token > old.fencing_token
        for old, new in zip(old_leases, new_leases, strict=True)
    )


@pytest.mark.asyncio
async def test_late_worker_cannot_write_terminal_state_after_fence_changes(tmp_path: Path) -> None:
    database = await prepared_database(tmp_path)
    coordinator = ScanCoordinator(database, lease_ttl=timedelta(seconds=30))
    run_id = await coordinator.enqueue("u", "a", "late", "late")
    claim = await coordinator.claim_next("worker-old")
    assert claim is not None
    leases = await coordinator.acquire_scan_leases(claim)

    async with database.session() as session:
        await session.execute(
            text(
                "UPDATE resource_leases SET fencing_token=fencing_token+1,"
                "owner_task_id='new-owner' WHERE owner_task_id=:run"
            ),
            {"run": run_id},
        )
        await session.commit()

    with pytest.raises(LeaseLostError):
        await coordinator.finish_failed(claim, leases, "BROWSER_CRASH", "browser stopped")

    async with database.session() as session:
        status = await session.scalar(
            text("SELECT status FROM scan_runs WHERE id=:id"), {"id": run_id}
        )
    await database.dispose()
    assert status == "RUNNING"


@pytest.mark.asyncio
async def test_late_worker_cannot_commit_snapshot_after_fence_changes(tmp_path: Path) -> None:
    database = await prepared_database(tmp_path)
    coordinator = ScanCoordinator(database)
    snapshots = SnapshotCommitService(database)
    run_id = await coordinator.enqueue("u", "a", "snapshot-late", "snapshot-late")
    claim = await coordinator.claim_next("worker-old")
    assert claim is not None
    leases = await coordinator.acquire_scan_leases(claim)
    await snapshots.stage(run_id, "FOLLOWER", [ObservedMember("42")])
    await snapshots.complete_side(run_id, "FOLLOWER")
    await snapshots.complete_side(run_id, "FOLLOWING")
    async with database.session() as session:
        await session.execute(
            text(
                "UPDATE resource_leases SET fencing_token=fencing_token+1,"
                "owner_task_id='new-owner' WHERE owner_task_id=:run"
            ),
            {"run": run_id},
        )
        await session.commit()

    with pytest.raises(LeaseLostError):
        await snapshots.commit(run_id, claim=claim, leases=leases)

    async with database.session() as session:
        snapshots_count = await session.scalar(text("SELECT count(*) FROM relationship_snapshots"))
    await database.dispose()
    assert snapshots_count == 0


@pytest.mark.asyncio
async def test_restart_recovery_marks_abruptly_terminated_run_failed_without_resume(
    tmp_path: Path,
) -> None:
    database = await prepared_database(tmp_path)
    coordinator = ScanCoordinator(database, lease_ttl=timedelta(seconds=30))
    run_id = await coordinator.enqueue("u", "a", "crash", "crash")
    claim = await coordinator.claim_next("worker-killed")
    assert claim is not None
    await coordinator.acquire_scan_leases(claim)
    async with database.session() as session:
        await session.execute(
            text("UPDATE resource_leases SET expires_at=:past WHERE owner_task_id=:run"),
            {"past": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(), "run": run_id},
        )
        await session.commit()

    recovered = await ScanCoordinator(database).recover_expired_runs()

    async with database.session() as session:
        row = (
            await session.execute(
                text("SELECT status,error_code,finished_at FROM scan_runs WHERE id=:id"),
                {"id": run_id},
            )
        ).one()
    await database.dispose()
    assert recovered == [run_id]
    assert row[0:2] == ("FAILED", "BROWSER_CRASH")
    assert row[2] is not None


@pytest.mark.asyncio
async def test_restart_recovers_worker_killed_between_claim_and_first_lease(tmp_path: Path) -> None:
    database = await prepared_database(tmp_path)
    coordinator = ScanCoordinator(database, lease_ttl=timedelta(seconds=30))
    run_id = await coordinator.enqueue("u", "a", "claim-crash", "claim-crash")
    assert await coordinator.claim_next("worker-killed") is not None
    async with database.session() as session:
        await session.execute(
            text("UPDATE scan_runs SET heartbeat_at=:past WHERE id=:run"),
            {"past": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(), "run": run_id},
        )
        await session.commit()

    recovered = await coordinator.recover_expired_runs()

    async with database.session() as session:
        status = await session.scalar(
            text("SELECT status FROM scan_runs WHERE id=:run"), {"run": run_id}
        )
    await database.dispose()
    assert recovered == [run_id]
    assert status == "FAILED"
