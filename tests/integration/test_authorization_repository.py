import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import text

from x_follow_list.application.errors import ResourceNotFoundError
from x_follow_list.config import RuntimeEnvironment, Settings
from x_follow_list.persistence.authorization import OwnedResourceRepository
from x_follow_list.persistence.database import Database
from x_follow_list.persistence.migrations import alembic_config


@pytest.mark.asyncio
async def test_owned_resource_queries_hide_another_users_resources(tmp_path: Path) -> None:
    database = Database.from_settings(
        Settings(environment=RuntimeEnvironment.TEST, data_dir=tmp_path)
    )
    await asyncio.to_thread(
        command.upgrade,
        alembic_config(Settings(environment=RuntimeEnvironment.TEST, data_dir=tmp_path)),
        "head",
    )
    now = datetime.now(UTC).isoformat()
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO users (id,email,password_hash,role,created_at,updated_at) VALUES "
                "('user-a','a@example.test','hash','OWNER',:now,:now),"
                "('user-b','b@example.test','hash','OWNER',:now,:now)"
            ),
            {"now": now},
        )
        await session.execute(
            text(
                "INSERT INTO browser_provider_configs "
                "(id,owner_user_id,provider_code,config_version,display_name,config_json,"
                "created_at,updated_at) VALUES "
                "('provider-a','user-a','DIRECT_CHROME',1,'A','{}',:now,:now),"
                "('provider-b','user-b','DIRECT_CHROME',1,'B','{}',:now,:now)"
            ),
            {"now": now},
        )
        await session.execute(
            text(
                "INSERT INTO x_accounts "
                "(id,owner_user_id,provider_config_id,profile_ref,profile_ref_hash,x_user_id,"
                "status,created_at,updated_at) VALUES "
                "('account-a','user-a','provider-a','profile-a','hash-a','x-a','READY',:now,:now),"
                "('account-b','user-b','provider-b','profile-b','hash-b','x-b','READY',:now,:now)"
            ),
            {"now": now},
        )
        await session.execute(
            text(
                "INSERT INTO x_account_memberships "
                "(x_account_id,user_id,role,created_at,updated_at) VALUES "
                "('account-a','user-a','OWNER',:now,:now),"
                "('account-b','user-b','OWNER',:now,:now)"
            ),
            {"now": now},
        )
        await session.execute(
            text(
                "INSERT INTO scan_runs "
                "(id,x_account_id,requested_by_user_id,status,created_at) "
                "VALUES ('run-b','account-b','user-b','SUCCESS',:now)"
            ),
            {"now": now},
        )
        await session.execute(
            text(
                "INSERT INTO artifacts "
                "(id,scan_run_id,kind,status,storage_path,created_at,expires_at) "
                "VALUES ('artifact-b','run-b','XLSX','READY','private/result.xlsx',:now,:now)"
            ),
            {"now": now},
        )
        await session.commit()

        repository = OwnedResourceRepository(session)
        assert (await repository.require_x_account("user-a", "account-a"))["id"] == "account-a"
        for lookup in (
            repository.require_x_account("user-a", "account-b"),
            repository.require_x_account("user-a", "missing-account"),
            repository.require_scan_run("user-a", "run-b"),
            repository.require_artifact("user-a", "artifact-b"),
        ):
            with pytest.raises(ResourceNotFoundError):
                await lookup

    await database.dispose()
