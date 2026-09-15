from __future__ import annotations

import asyncio
import hashlib
import json
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

ORIGIN = "http://test"


def test_a11_openapi_contract_exposes_required_routes_and_scan_idempotency(
    tmp_path: Path,
) -> None:
    settings = Settings(
        environment=RuntimeEnvironment.TEST,
        data_dir=tmp_path,
        bootstrap_token=SecretStr("one-time-bootstrap-token"),
        app_origin=ORIGIN,
        session_ttl_seconds=3600,
    )
    schema = create_app(settings).openapi()
    required_operations = {
        ("/api/v1/browser-providers", "get"),
        ("/api/v1/browser-provider-configs", "post"),
        ("/api/v1/browser-provider-configs/{config_id}/test", "post"),
        ("/api/v1/browser-provider-configs/{config_id}/profiles", "get"),
        ("/api/v1/x-accounts", "get"),
        ("/api/v1/x-accounts/{account_id}", "delete"),
        ("/api/v1/x-accounts/{account_id}/scan-runs", "post"),
        ("/api/v1/scan-runs", "get"),
        ("/api/v1/scan-runs/{run_id}", "get"),
        ("/api/v1/relationships", "get"),
        ("/api/v1/relationship-events", "get"),
        ("/api/v1/relationship-events/{event_id}/acknowledge", "post"),
    }

    assert all(method in schema["paths"][path] for path, method in required_operations)
    scan_parameters = schema["paths"]["/api/v1/x-accounts/{account_id}/scan-runs"][
        "post"
    ]["parameters"]
    idempotency = next(item for item in scan_parameters if item["name"] == "Idempotency-Key")
    assert idempotency["in"] == "header"
    assert idempotency["required"] is True
    assert "204" in schema["paths"]["/api/v1/x-accounts/{account_id}"]["delete"]["responses"]


async def prepared_app(tmp_path: Path) -> FastAPI:
    settings = Settings(
        environment=RuntimeEnvironment.TEST,
        data_dir=tmp_path,
        bootstrap_token=SecretStr("one-time-bootstrap-token"),
        app_origin=ORIGIN,
        session_ttl_seconds=3600,
    )
    await asyncio.to_thread(command.upgrade, alembic_config(settings), "head")
    return create_app(settings)


async def login_and_seed(client: httpx.AsyncClient, app: FastAPI) -> tuple[str, str]:
    bootstrap = await client.post(
        "/api/v1/auth/bootstrap",
        headers={"Origin": ORIGIN},
        json={
            "login": "owner@example.test",
            "password": "a sufficiently long password",
            "bootstrap_token": "one-time-bootstrap-token",
        },
    )
    login = await client.post(
        "/api/v1/auth/session",
        headers={"Origin": ORIGIN},
        json={"login": "owner@example.test", "password": "a sufficiently long password"},
    )
    owner_id = str(bootstrap.json()["id"])
    csrf = str(login.json()["csrf_token"])
    now = datetime.now(UTC).isoformat()
    profiles_root = (app.state.settings.data_dir / "managed-profiles").resolve()
    async with app.state.database.session() as session:
        await session.execute(
            text(
                "INSERT INTO browser_provider_configs "
                "(id,owner_user_id,provider_code,config_version,display_name,config_json,"
                "created_at,updated_at) VALUES "
                "('provider',:owner,'DIRECT_CHROME',1,'Chrome',:config,:now,:now)"
            ),
            {
                "owner": owner_id,
                "config": json.dumps({"profiles_root": str(profiles_root)}),
                "now": now,
            },
        )
        await session.execute(
            text(
                "INSERT INTO x_accounts "
                "(id,owner_user_id,provider_config_id,profile_ref,profile_ref_hash,x_user_id,"
                "username,display_name,status,created_at,updated_at) VALUES "
                "('account',:owner,'provider','profile','hash','self','alice','Alice',"
                "'READY',:now,:now)"
            ),
            {"owner": owner_id, "now": now},
        )
        await session.execute(
            text(
                "INSERT INTO x_account_memberships "
                "(x_account_id,user_id,role,created_at,updated_at) "
                "VALUES ('account',:owner,'OWNER',:now,:now)"
            ),
            {"owner": owner_id, "now": now},
        )
        await session.commit()
    return owner_id, csrf


def mutation_headers(csrf: str, *, key: str | None = None) -> dict[str, str]:
    headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


@pytest.mark.asyncio
async def test_owner_lists_account_and_enqueues_one_idempotent_active_scan(
    tmp_path: Path,
) -> None:
    app = await prepared_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        _owner, csrf = await login_and_seed(client, app)

        accounts = await client.get("/api/v1/x-accounts")
        created = await client.post(
            "/api/v1/x-accounts/account/scan-runs",
            headers=mutation_headers(csrf, key="scan-key-one"),
        )
        replay = await client.post(
            "/api/v1/x-accounts/account/scan-runs",
            headers=mutation_headers(csrf, key="scan-key-one"),
        )
        duplicate = await client.post(
            "/api/v1/x-accounts/account/scan-runs",
            headers=mutation_headers(csrf, key="scan-key-two"),
        )
        foreign = await client.post(
            "/api/v1/x-accounts/not-owned/scan-runs",
            headers=mutation_headers(csrf, key="foreign-key"),
        )

    await app.state.database.dispose()
    assert accounts.status_code == 200
    assert accounts.json()["items"] == [
        {
            "id": "account",
            "x_user_id": "self",
            "username": "alice",
            "display_name": "Alice",
            "session_status": "READY",
            "provider_code": "DIRECT_CHROME",
            "profile_ref": "profile",
            "version": 1,
            "last_successful_scan_at": None,
        }
    ]
    assert created.status_code == replay.status_code == 202
    assert replay.json()["id"] == created.json()["id"]
    assert created.json()["status"] == "QUEUED"
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "SCAN_ALREADY_ACTIVE"
    assert foreign.status_code == 404


@pytest.mark.asyncio
async def test_scan_lists_and_details_keep_failure_separate_from_last_success(
    tmp_path: Path,
) -> None:
    app = await prepared_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        _owner, csrf = await login_and_seed(client, app)
        created = await client.post(
            "/api/v1/x-accounts/account/scan-runs",
            headers=mutation_headers(csrf, key="failed-scan"),
        )
        run_id = str(created.json()["id"])
        last_success = datetime.now(UTC) - timedelta(hours=1)
        async with app.state.database.session() as session:
            await session.execute(
                text(
                    "UPDATE x_accounts SET last_successful_scan_at=:last WHERE id='account'"
                ),
                {"last": last_success.isoformat()},
            )
            await session.execute(
                text(
                    "UPDATE scan_runs SET status='FAILED',progress_stage='VALIDATING',"
                    "error_code='INCOMPLETE_SUSPECTED',error_summary='Retry the scan',"
                    "finished_at=:now WHERE id=:run"
                ),
                {"now": datetime.now(UTC).isoformat(), "run": run_id},
            )
            await session.commit()

        listing = await client.get("/api/v1/scan-runs?limit=1")
        detail = await client.get(f"/api/v1/scan-runs/{run_id}")
        invalid_limit = await client.get("/api/v1/scan-runs?limit=201")

    await app.state.database.dispose()
    assert listing.status_code == 200
    assert listing.json()["items"][0]["id"] == run_id
    assert detail.status_code == 200
    assert detail.json()["status"] == "FAILED"
    assert detail.json()["error"] == {
        "code": "INCOMPLETE_SUSPECTED",
        "summary": "Retry the scan",
    }
    assert detail.json()["last_successful_scan_at"] == last_success.isoformat().replace(
        "+00:00", "Z"
    )
    assert invalid_limit.status_code == 422


async def seed_relationships(app: FastAPI) -> None:
    now = datetime.now(UTC).isoformat()
    async with app.state.database.session() as session:
        await session.execute(
            text(
                "INSERT INTO scan_runs "
                "(id,x_account_id,requested_by_user_id,status,created_at,finished_at) "
                "SELECT 'successful','account',owner_user_id,'SUCCESS',:now,:now "
                "FROM x_accounts WHERE id='account'"
            ),
            {"now": now},
        )
        await session.execute(
            text(
                "INSERT INTO relationship_snapshots "
                "(id,x_account_id,scan_run_id,follower_count,following_count,captured_at) "
                "VALUES ('snapshot','account','successful',1,2,:now)"
            ),
            {"now": now},
        )
        await session.execute(
            text(
                "INSERT INTO snapshot_memberships "
                "(snapshot_id,subject_x_user_id,follows_me,i_follow,username,display_name) VALUES "
                "('snapshot','101',1,1,'mutual','Mutual User'),"
                "('snapshot','102',0,1,'waiting','Waiting User')"
            )
        )
        await session.execute(
            text(
                "INSERT INTO relationship_states "
                "(x_account_id,subject_x_user_id,state,non_followback_streak,last_snapshot_id,"
                "updated_at) VALUES "
                "('account','101','MUTUAL',0,'snapshot',:now),"
                "('account','102','NOT_FOLLOWING_BACK',3,'snapshot',:now)"
            ),
            {"now": now},
        )
        await session.execute(
            text(
                "INSERT INTO relationship_events "
                "(id,x_account_id,scan_run_id,subject_x_user_id,category,event_type,dedupe_key,"
                "status,created_at) VALUES "
                "('event','account','successful','102','ACTION_ITEM',"
                "'UNFOLLOWED_ME_AFTER_MUTUAL','dedupe','NEW',:now)"
            ),
            {"now": now},
        )
        await session.commit()


@pytest.mark.asyncio
async def test_relationship_and_event_queries_filter_and_acknowledge_with_version(
    tmp_path: Path,
) -> None:
    app = await prepared_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        _owner, csrf = await login_and_seed(client, app)
        await seed_relationships(app)

        relationships = await client.get(
            "/api/v1/relationships?x_account_id=account&state=NOT_FOLLOWING_BACK&search=wait"
        )
        events = await client.get(
            "/api/v1/relationship-events?x_account_id=account&status=NEW&limit=1"
        )
        acknowledged = await client.post(
            "/api/v1/relationship-events/event/acknowledge",
            headers=mutation_headers(csrf),
            json={"version": 1},
        )
        stale = await client.post(
            "/api/v1/relationship-events/event/acknowledge",
            headers=mutation_headers(csrf),
            json={"version": 1},
        )
        foreign = await client.get(
            "/api/v1/relationships?x_account_id=not-owned"
        )

    await app.state.database.dispose()
    assert relationships.status_code == 200
    assert relationships.json()["items"] == [
        {
            "x_user_id": "102",
            "username": "waiting",
            "display_name": "Waiting User",
            "state": "NOT_FOLLOWING_BACK",
            "non_followback_streak": 3,
            "snapshot_id": "snapshot",
        }
    ]
    assert events.status_code == 200
    assert events.json()["items"][0]["event_type"] == "UNFOLLOWED_ME_AFTER_MUTUAL"
    assert events.json()["items"][0]["version"] == 1
    assert acknowledged.status_code == 200
    assert acknowledged.json()["status"] == "ACKNOWLEDGED"
    assert acknowledged.json()["version"] == 2
    assert stale.status_code == 409
    assert stale.json()["code"] == "RESOURCE_VERSION_CONFLICT"
    assert foreign.status_code == 404


@pytest.mark.asyncio
async def test_scan_cursor_is_stable_and_domain_open_status_is_exposed_as_new(
    tmp_path: Path,
) -> None:
    app = await prepared_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        _owner, _csrf = await login_and_seed(client, app)
        await seed_relationships(app)
        now = datetime.now(UTC)
        async with app.state.database.session() as session:
            for index in range(3):
                await session.execute(
                    text(
                        "INSERT INTO scan_runs "
                        "(id,x_account_id,requested_by_user_id,status,created_at) "
                        "SELECT :id,'account',owner_user_id,'FAILED',:created "
                        "FROM x_accounts WHERE id='account'"
                    ),
                    {
                        "id": f"page-run-{index}",
                        "created": (now + timedelta(seconds=index)).isoformat(),
                    },
                )
            await session.execute(
                text("UPDATE relationship_events SET status='OPEN' WHERE id='event'")
            )
            await session.commit()

        first = await client.get("/api/v1/scan-runs?limit=2")
        cursor = first.json()["next_cursor"]
        second = await client.get(f"/api/v1/scan-runs?limit=2&cursor={cursor}")
        new_events = await client.get(
            "/api/v1/relationship-events?x_account_id=account&status=NEW"
        )
        malformed = await client.get("/api/v1/scan-runs?cursor=not-a-cursor")

    await app.state.database.dispose()
    first_ids = [item["id"] for item in first.json()["items"]]
    second_ids = [item["id"] for item in second.json()["items"]]
    assert first.status_code == second.status_code == 200
    assert cursor
    assert set(first_ids).isdisjoint(second_ids)
    assert new_events.json()["items"][0]["status"] == "NEW"
    assert malformed.status_code == 422
    assert malformed.json()["code"] == "REQUEST_VALIDATION_FAILED"


async def add_viewer_session(app: FastAPI) -> tuple[str, str]:
    now = datetime.now(UTC)
    raw_token = "viewer-session-token"
    csrf = "viewer-csrf-token"
    async with app.state.database.session() as session:
        await session.execute(
            text(
                "INSERT INTO users (id,email,password_hash,role,created_at,updated_at) "
                "VALUES ('viewer','viewer@example.test','unused','VIEWER',:now,:now)"
            ),
            {"now": now.isoformat()},
        )
        await session.execute(
            text(
                "INSERT INTO auth_sessions "
                "(id,user_id,token_hash,csrf_token_hash,created_at,expires_at,last_seen_at) "
                "VALUES ('viewer-session','viewer',:token,:csrf,:now,:expires,:now)"
            ),
            {
                "token": hashlib.sha256(raw_token.encode()).hexdigest(),
                "csrf": hashlib.sha256(csrf.encode()).hexdigest(),
                "now": now.isoformat(),
                "expires": (now + timedelta(hours=1)).isoformat(),
            },
        )
        await session.execute(
            text(
                "INSERT INTO x_account_memberships "
                "(x_account_id,user_id,role,created_at,updated_at) "
                "VALUES ('account','viewer','VIEWER',:now,:now)"
            ),
            {"now": now.isoformat()},
        )
        await session.commit()
    return raw_token, csrf


@pytest.mark.asyncio
async def test_mutations_require_idempotency_permission_version_and_write_audit(
    tmp_path: Path,
) -> None:
    app = await prepared_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        _owner, csrf = await login_and_seed(client, app)
        await seed_relationships(app)

        missing_key = await client.post(
            "/api/v1/x-accounts/account/scan-runs",
            headers=mutation_headers(csrf),
        )
        viewer_token, viewer_csrf = await add_viewer_session(app)
        client.cookies.set("x_follow_list_session", viewer_token)
        viewer_scan = await client.post(
            "/api/v1/x-accounts/account/scan-runs",
            headers=mutation_headers(viewer_csrf, key="viewer-key"),
        )
        viewer_ack = await client.post(
            "/api/v1/relationship-events/event/acknowledge",
            headers=mutation_headers(viewer_csrf),
            json={"version": 1},
        )
        client.cookies.clear()
        login = await client.post(
            "/api/v1/auth/session",
            headers={"Origin": ORIGIN},
            json={
                "login": "owner@example.test",
                "password": "a sufficiently long password",
            },
        )
        owner_csrf = str(login.json()["csrf_token"])
        created = await client.post(
            "/api/v1/x-accounts/account/scan-runs",
            headers=mutation_headers(owner_csrf, key="audited-key"),
        )
        unbound = await client.request(
            "DELETE",
            "/api/v1/x-accounts/account",
            headers=mutation_headers(owner_csrf),
            json={"version": 1, "delete_history": False},
        )
        async with app.state.database.session() as session:
            account = (
                await session.execute(
                    text("SELECT status,profile_ref,version FROM x_accounts WHERE id='account'")
                )
            ).one()
            history_count = await session.scalar(
                text("SELECT count(*) FROM relationship_snapshots WHERE x_account_id='account'")
            )
            actions = set(
                await session.scalars(
                    text(
                        "SELECT action FROM audit_logs "
                        "WHERE action IN ('SCAN_RUN_CREATED','X_ACCOUNT_UNBOUND')"
                    )
                )
            )

    await app.state.database.dispose()
    assert missing_key.status_code == 422
    assert viewer_scan.status_code == viewer_ack.status_code == 403
    assert created.status_code == 202
    assert unbound.status_code == 204
    assert account[0] == "DISABLED"
    assert str(account[1]).startswith("unbound:")
    assert account[2] == 2
    assert history_count == 1
    assert actions == {"SCAN_RUN_CREATED", "X_ACCOUNT_UNBOUND"}


@pytest.mark.asyncio
async def test_relationship_and_event_keyset_cursors_do_not_repeat_rows(
    tmp_path: Path,
) -> None:
    app = await prepared_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        _owner, _csrf = await login_and_seed(client, app)
        await seed_relationships(app)
        async with app.state.database.session() as session:
            await session.execute(
                text(
                    "INSERT INTO relationship_events "
                    "(id,x_account_id,scan_run_id,subject_x_user_id,category,event_type,"
                    "dedupe_key,status,created_at) "
                    "SELECT 'event-2',x_account_id,scan_run_id,'101','RELATIONSHIP_CHANGE',"
                    "'NEW_FOLLOWER','dedupe-2','INFORMATIONAL',created_at "
                    "FROM relationship_events WHERE id='event'"
                )
            )
            await session.commit()

        relationship_first = await client.get(
            "/api/v1/relationships?x_account_id=account&limit=1"
        )
        relationship_cursor = relationship_first.json()["next_cursor"]
        relationship_second = await client.get(
            "/api/v1/relationships",
            params={
                "x_account_id": "account",
                "limit": 1,
                "cursor": relationship_cursor,
            },
        )
        event_first = await client.get(
            "/api/v1/relationship-events?x_account_id=account&limit=1"
        )
        event_cursor = event_first.json()["next_cursor"]
        event_second = await client.get(
            "/api/v1/relationship-events",
            params={"x_account_id": "account", "limit": 1, "cursor": event_cursor},
        )

    await app.state.database.dispose()
    first_relationship_id = relationship_first.json()["items"][0]["x_user_id"]
    second_relationship_id = relationship_second.json()["items"][0]["x_user_id"]
    assert relationship_cursor
    assert first_relationship_id != second_relationship_id
    assert event_cursor
    assert event_second.status_code == 200
    assert event_second.json()["items"][0]["id"] != event_first.json()["items"][0]["id"]


@pytest.mark.asyncio
async def test_unbind_executes_direct_chrome_managed_profile_deletion_policy(
    tmp_path: Path,
) -> None:
    app = await prepared_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
        _owner, csrf = await login_and_seed(client, app)
        profile_path = tmp_path / "managed-profiles" / "profile"
        profile_path.mkdir(parents=True)
        (profile_path / ".x-follow-list-profile").write_text("managed", encoding="utf-8")

        response = await client.request(
            "DELETE",
            "/api/v1/x-accounts/account",
            headers=mutation_headers(csrf),
            json={"version": 1, "delete_history": False},
        )

    await app.state.database.dispose()
    assert response.status_code == 204
    assert not profile_path.exists()
