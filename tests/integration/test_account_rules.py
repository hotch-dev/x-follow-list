from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from alembic import command
from pydantic import SecretStr
from sqlalchemy import text

from x_follow_list.api.app import create_app
from x_follow_list.application.snapshots import ObservedMember, SnapshotCommitService
from x_follow_list.config import RuntimeEnvironment, Settings
from x_follow_list.persistence.migrations import alembic_config

ORIGIN = "http://test"


async def prepared_app(tmp_path: Path):  # type: ignore[no-untyped-def]
    settings = Settings(
        environment=RuntimeEnvironment.TEST,
        data_dir=tmp_path,
        bootstrap_token=SecretStr("one-time-bootstrap-token"),
        app_origin=ORIGIN,
    )
    await asyncio.to_thread(command.upgrade, alembic_config(settings), "head")
    return create_app(settings)


async def seed_owner(client: httpx.AsyncClient, app: object) -> str:
    bootstrap = await client.post(
        "/api/v1/auth/bootstrap",
        headers={"Origin": ORIGIN},
        json={
            "login": "owner@example.test",
            "password": "a sufficiently long password",
            "bootstrap_token": "one-time-bootstrap-token",
        },
    )
    signed_in = await client.post(
        "/api/v1/auth/session",
        headers={"Origin": ORIGIN},
        json={"login": "owner@example.test", "password": "a sufficiently long password"},
    )
    owner = str(bootstrap.json()["id"])
    now = datetime.now(UTC).isoformat()
    database = app.state.database  # type: ignore[attr-defined]
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO browser_provider_configs "
                "(id,owner_user_id,provider_code,config_version,display_name,config_json,"
                "created_at,updated_at) VALUES "
                "('provider',:owner,'DIRECT_CHROME',1,'Chrome',:config,:now,:now)"
            ),
            {"owner": owner, "config": json.dumps({"profiles_root": str(Path.cwd())}), "now": now},
        )
        await session.execute(
            text(
                "INSERT INTO x_accounts "
                "(id,owner_user_id,provider_config_id,profile_ref,profile_ref_hash,x_user_id,"
                "status,created_at,updated_at) VALUES "
                "('account',:owner,'provider','profile','hash','self','READY',:now,:now)"
            ),
            {"owner": owner, "now": now},
        )
        await session.execute(
            text(
                "INSERT INTO x_account_memberships "
                "(x_account_id,user_id,role,created_at,updated_at) "
                "VALUES ('account',:owner,'OWNER',:now,:now)"
            ),
            {"owner": owner, "now": now},
        )
        await session.commit()
    return str(signed_in.json()["csrf_token"])


def headers(csrf: str, key: str) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": csrf, "Idempotency-Key": key}


async def commit_scan(app: object, run_id: str, following: list[str]) -> None:
    database = app.state.database  # type: ignore[attr-defined]
    async with database.session() as session:
        await session.execute(
            text(
                "INSERT INTO scan_runs "
                "(id,x_account_id,requested_by_user_id,status,created_at) "
                "SELECT :run,'account',owner_user_id,'RUNNING',:now "
                "FROM x_accounts WHERE id='account'"
            ),
            {"run": run_id, "now": datetime.now(UTC).isoformat()},
        )
        await session.commit()
    service = SnapshotCommitService(database)
    await service.stage(run_id, "FOLLOWING", [ObservedMember(x) for x in following])
    await service.complete_side(run_id, "FOLLOWER")
    await service.complete_side(run_id, "FOLLOWING")
    await service.commit(run_id)


@pytest.mark.asyncio
async def test_rule_api_is_owner_scoped_mutually_exclusive_and_audited(tmp_path: Path) -> None:
    app = await prepared_app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        csrf = await seed_owner(client, app)
        created = await client.post(
            "/api/v1/account-rules",
            headers=headers(csrf, "rule-one"),
            json={
                "x_account_id": "account",
                "subject_x_user_id": "42",
                "rule_type": "BUSINESS_BLOCKLIST",
                "reason": "Do not follow this supplier",
            },
        )
        repeated = await client.post(
            "/api/v1/account-rules",
            headers=headers(csrf, "rule-one"),
            json={
                "x_account_id": "account",
                "subject_x_user_id": "42",
                "rule_type": "BUSINESS_BLOCKLIST",
                "reason": "Do not follow this supplier",
            },
        )
        conflicting = await client.post(
            "/api/v1/account-rules",
            headers=headers(csrf, "rule-two"),
            json={
                "x_account_id": "account",
                "subject_x_user_id": "42",
                "rule_type": "ALLOWLIST",
                "reason": "Conflicting rule",
            },
        )
        listed = await client.get("/api/v1/account-rules?x_account_id=account")
        foreign = await client.get("/api/v1/account-rules?x_account_id=not-owned")
        bad_csrf = await client.post(
            "/api/v1/account-rules",
            headers={"Origin": ORIGIN, "Idempotency-Key": "bad-csrf"},
            json={"x_account_id": "account", "subject_x_user_id": "77", "rule_type": "ALLOWLIST", "reason": "ok"},
        )
        assert created.status_code == 201
        deleted = await client.request(
            "DELETE",
            f"/api/v1/account-rules/{created.json()['id']}",
            headers=headers(csrf, "delete-one"),
            json={"version": 1},
        )
        stale = await client.request(
            "DELETE",
            f"/api/v1/account-rules/{created.json()['id']}",
            headers=headers(csrf, "delete-two"),
            json={"version": 1},
        )
    async with app.state.database.session() as session:
        actions = (await session.scalars(text("SELECT action FROM audit_logs WHERE resource_type='account_rule' ORDER BY created_at,id"))).all()
    await app.state.database.dispose()

    assert created.status_code == repeated.status_code == 201
    assert created.json() == repeated.json()
    assert created.json()["scan_required"] is True
    assert conflicting.status_code == 409
    assert listed.status_code == 200
    assert [item["subject_x_user_id"] for item in listed.json()["items"]] == ["42"]
    assert foreign.status_code == 404
    assert bad_csrf.status_code == 403
    assert deleted.status_code == 204
    assert stale.status_code == 404
    assert actions == ["ACCOUNT_RULE_CREATED", "ACCOUNT_RULE_DELETED"]


@pytest.mark.asyncio
async def test_blocklist_conflict_uses_latest_snapshot_and_opens_new_episode_after_resolution(
    tmp_path: Path,
) -> None:
    app = await prepared_app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        csrf = await seed_owner(client, app)
        await commit_scan(app, "baseline", ["42"])
        created = await client.post(
            "/api/v1/account-rules",
            headers=headers(csrf, "after-baseline"),
            json={"x_account_id": "account", "subject_x_user_id": "42", "rule_type": "BUSINESS_BLOCKLIST", "reason": "Risk review"},
        )
        first = await client.get("/api/v1/relationship-events?x_account_id=account&event_type=FOLLOWING_BLOCKLISTED_ACCOUNT&status=NEW")
        await commit_scan(app, "still-following", ["42"])
        repeated = await client.get("/api/v1/relationship-events?x_account_id=account&event_type=FOLLOWING_BLOCKLISTED_ACCOUNT&status=NEW")
        await commit_scan(app, "unfollowed", [])
        resolved = await client.get("/api/v1/relationship-events?x_account_id=account&event_type=FOLLOWING_BLOCKLISTED_ACCOUNT&status=RESOLVED")
        await commit_scan(app, "followed-again", ["42"])
        reopened = await client.get("/api/v1/relationship-events?x_account_id=account&event_type=FOLLOWING_BLOCKLISTED_ACCOUNT&status=NEW")
    await app.state.database.dispose()

    assert created.status_code == 201
    assert created.json()["scan_required"] is False
    assert len(first.json()["items"]) == len(repeated.json()["items"]) == 1
    assert first.json()["items"][0]["id"] == repeated.json()["items"][0]["id"]
    assert first.json()["items"][0]["rule_reason"] == "Risk review"
    assert len(resolved.json()["items"]) == 1
    assert len(reopened.json()["items"]) == 1
    assert reopened.json()["items"][0]["id"] != first.json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_baseline_scan_detects_existing_blocklist_without_prior_relationship_event(tmp_path: Path) -> None:
    app = await prepared_app(tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        csrf = await seed_owner(client, app)
        created = await client.post(
            "/api/v1/account-rules",
            headers=headers(csrf, "before-baseline"),
            json={"x_account_id": "account", "subject_x_user_id": "42", "rule_type": "BUSINESS_BLOCKLIST", "reason": "Risk review"},
        )
        before = await client.get("/api/v1/relationship-events?x_account_id=account&event_type=FOLLOWING_BLOCKLISTED_ACCOUNT")
        await commit_scan(app, "baseline", ["42"])
        after = await client.get("/api/v1/relationship-events?x_account_id=account&event_type=FOLLOWING_BLOCKLISTED_ACCOUNT")
    await app.state.database.dispose()

    assert created.json()["scan_required"] is True
    assert before.json()["items"] == []
    assert len(after.json()["items"]) == 1
    assert after.json()["items"][0]["scan_run_id"] == "baseline"
