import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import text

from x_follow_list.application.binding import (
    BindingConflictError,
    BindingStateError,
    BrowserBindingService,
)
from x_follow_list.application.errors import ResourceNotFoundError
from x_follow_list.config import RuntimeEnvironment, Settings
from x_follow_list.persistence.database import Database
from x_follow_list.persistence.migrations import alembic_config


async def prepared_database(tmp_path: Path) -> Database:
    settings = Settings(environment=RuntimeEnvironment.TEST, data_dir=tmp_path)
    await asyncio.to_thread(command.upgrade, alembic_config(settings), "head")
    database = Database.from_settings(settings)
    now = datetime(2026, 9, 14, 4, 0, tzinfo=UTC).isoformat()
    async with database.session() as session:
        for user_id in ("owner", "other"):
            await session.execute(
                text("INSERT INTO users VALUES (:id,:login,'hash','OWNER',:now,:now)"),
                {"id": user_id, "login": f"{user_id}@test", "now": now},
            )
        await session.execute(
            text(
                "INSERT INTO browser_provider_configs "
                "(id,owner_user_id,provider_code,config_version,display_name,config_json,"
                "created_at,updated_at) VALUES "
                "('provider','owner','DIRECT_CHROME',1,'Chrome','{}',:now,:now)"
            ),
            {"now": now},
        )
        await session.commit()
    return database


@pytest.mark.asyncio
async def test_create_binding_persists_versioned_profile_and_fifteen_minute_timeout(
    tmp_path: Path,
) -> None:
    database = await prepared_database(tmp_path)
    now = datetime(2026, 9, 14, 5, 0, tzinfo=UTC)
    service = BrowserBindingService(database, clock=lambda: now)

    created = await service.create("owner", "provider", "profile-one")
    loaded = await BrowserBindingService(database, clock=lambda: now).get(
        "owner", created.session_id
    )

    await database.dispose()
    assert loaded.status == "QUEUED"
    assert loaded.provider_code == "DIRECT_CHROME"
    assert loaded.provider_config_version == 1
    assert loaded.profile_ref == "profile-one"
    assert loaded.expires_at == now + timedelta(minutes=15)


@pytest.mark.asyncio
async def test_two_workers_can_claim_a_binding_only_once(tmp_path: Path) -> None:
    database = await prepared_database(tmp_path)
    service = BrowserBindingService(database)
    created = await service.create("owner", "provider", "profile-one")

    claims = await asyncio.gather(
        service.claim_next("worker-a"), service.claim_next("worker-b")
    )

    await database.dispose()
    claimed = [claim for claim in claims if claim is not None]
    assert len(claimed) == 1
    assert claimed[0].session_id == created.session_id
    assert claimed[0].claim_token


@pytest.mark.asyncio
async def test_detected_identity_requires_confirmation_before_creating_account(
    tmp_path: Path,
) -> None:
    database = await prepared_database(tmp_path)
    service = BrowserBindingService(database)
    created = await service.create("owner", "provider", "profile-one")
    claim = await service.claim_next("worker")
    assert claim is not None

    await service.record_detected_identity(
        claim,
        x_user_id="x-123",
        username="alice",
        display_name="Alice",
    )
    awaiting = await service.get("owner", created.session_id)
    account_id = await service.confirm("owner", created.session_id)

    async with database.session() as session:
        account = (
            await session.execute(
                text(
                    "SELECT x_user_id,username,status,provider_config_id,profile_ref "
                    "FROM x_accounts WHERE id=:id"
                ),
                {"id": account_id},
            )
        ).one()
        membership = (
            await session.execute(
                text(
                    "SELECT user_id,role FROM x_account_memberships WHERE x_account_id=:id"
                ),
                {"id": account_id},
            )
        ).one()
    await database.dispose()

    assert awaiting.status == "AWAITING_CONFIRMATION"
    assert awaiting.detected_x_user_id == "x-123"
    assert account == ("x-123", "alice", "READY", "provider", "profile-one")
    assert membership == ("owner", "OWNER")


@pytest.mark.asyncio
async def test_timeout_cancel_and_restart_keep_queryable_terminal_records(
    tmp_path: Path,
) -> None:
    database = await prepared_database(tmp_path)
    now = datetime(2026, 9, 14, 5, 0, tzinfo=UTC)
    service = BrowserBindingService(database, clock=lambda: now)
    timed = await service.create("owner", "provider", "timed-profile")
    cancelled = await service.create("owner", "provider", "cancelled-profile")
    await service.cancel("owner", cancelled.session_id)

    later = BrowserBindingService(database, clock=lambda: now + timedelta(minutes=16))
    expired_ids = await later.expire_due()
    timed_after_restart = await later.get("owner", timed.session_id)
    cancelled_after_restart = await later.get("owner", cancelled.session_id)

    await database.dispose()
    assert expired_ids == [timed.session_id]
    assert timed_after_restart.status == "EXPIRED"
    assert timed_after_restart.error_code == "BIND_TIMEOUT"
    assert cancelled_after_restart.status == "CANCELLED"


@pytest.mark.asyncio
async def test_binding_owner_boundary_and_profile_conflicts_fail_closed(
    tmp_path: Path,
) -> None:
    database = await prepared_database(tmp_path)
    service = BrowserBindingService(database)
    created = await service.create("owner", "provider", "shared-profile")

    with pytest.raises(ResourceNotFoundError):
        await service.get("other", created.session_id)
    with pytest.raises(ResourceNotFoundError):
        await service.create("other", "provider", "other-profile")
    with pytest.raises(BindingConflictError):
        await service.create("owner", "provider", "shared-profile")
    with pytest.raises(BindingStateError):
        await service.confirm("owner", created.session_id)

    await database.dispose()


@pytest.mark.asyncio
async def test_revalidation_restores_ready_only_for_the_same_detected_x_identity(
    tmp_path: Path,
) -> None:
    database = await prepared_database(tmp_path)
    service = BrowserBindingService(database)
    initial = await service.create("owner", "provider", "profile-one")
    initial_claim = await service.claim_next("initial-worker")
    assert initial_claim is not None
    await service.record_detected_identity(
        initial_claim,
        x_user_id="x-123",
        username="alice",
        display_name="Alice",
    )
    account_id = await service.confirm("owner", initial.session_id)

    await service.mark_reauth_required("owner", account_id)
    revalidation = await service.create_revalidation("owner", account_id)
    revalidation_claim = await service.claim_next("revalidation-worker")
    assert revalidation_claim is not None
    await service.record_detected_identity(
        revalidation_claim,
        x_user_id="x-123",
        username="alice-renamed",
        display_name="Alice Updated",
    )
    confirmed_account_id = await service.confirm("owner", revalidation.session_id)

    await service.mark_reauth_required("owner", account_id)
    mismatched = await service.create_revalidation("owner", account_id)
    mismatched_claim = await service.claim_next("mismatched-worker")
    assert mismatched_claim is not None
    await service.record_detected_identity(
        mismatched_claim,
        x_user_id="x-999",
        username="mallory",
        display_name="Mallory",
    )
    with pytest.raises(BindingConflictError):
        await service.confirm("owner", mismatched.session_id)

    async with database.session() as session:
        account = (
            await session.execute(
                text(
                    "SELECT status,x_user_id,username,display_name FROM x_accounts "
                    "WHERE id=:id"
                ),
                {"id": account_id},
            )
        ).one()
        account_count = await session.scalar(text("SELECT count(*) FROM x_accounts"))
    await database.dispose()

    assert revalidation.target_account_id == account_id
    assert confirmed_account_id == account_id
    assert account == ("REAUTH_REQUIRED", "x-123", "alice-renamed", "Alice Updated")
    assert account_count == 1
