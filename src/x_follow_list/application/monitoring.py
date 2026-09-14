from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from x_follow_list.application.errors import ApplicationError, ResourceNotFoundError
from x_follow_list.application.scan_coordination import (
    IdempotencyConflictError,
    ScanAlreadyActiveError,
    ScanCoordinator,
)
from x_follow_list.persistence.database import Database


class MonitoringQueryService:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._coordinator = ScanCoordinator(database)

    async def list_accounts(self, user_id: str) -> list[dict[str, Any]]:
        async with self._database.session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT a.id,a.x_user_id,a.username,a.display_name,a.status,"
                        "p.provider_code,a.profile_ref,a.last_successful_scan_at "
                        "FROM x_account_memberships m "
                        "JOIN x_accounts a ON a.id=m.x_account_id "
                        "JOIN browser_provider_configs p ON p.id=a.provider_config_id "
                        "WHERE m.user_id=:user ORDER BY a.created_at,a.id"
                    ),
                    {"user": user_id},
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    async def enqueue_scan(
        self,
        user_id: str,
        account_id: str,
        idempotency_key: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        await self._require_account_role(user_id, account_id, mutation=True)
        try:
            run_id = await self._coordinator.enqueue(
                user_id,
                account_id,
                idempotency_key,
                f"manual-scan:v1:{account_id}",
                request_id,
            )
        except LookupError:
            raise ResourceNotFoundError from None
        except IdempotencyConflictError:
            raise ApplicationError(
                "IDEMPOTENCY_CONFLICT", "Idempotency key conflicts with this request", 409
            ) from None
        except ScanAlreadyActiveError:
            raise ApplicationError(
                "SCAN_ALREADY_ACTIVE", "Account already has an active scan", 409
            ) from None
        return await self.get_scan(user_id, run_id)

    async def list_scans(
        self, user_id: str, limit: int, cursor: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        cursor_value = _decode_cursor(cursor)
        cursor_clause = ""
        parameters: dict[str, object] = {"user": user_id, "limit": limit + 1}
        if cursor_value is not None:
            cursor_clause = (
                "AND (r.created_at < :cursor_time OR "
                "(r.created_at = :cursor_time AND r.id < :cursor_id)) "
            )
            parameters["cursor_time"], parameters["cursor_id"] = cursor_value
        async with self._database.session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT r.*,a.last_successful_scan_at FROM x_account_memberships m "
                        "JOIN scan_runs r ON r.x_account_id=m.x_account_id "
                        "JOIN x_accounts a ON a.id=r.x_account_id "
                        "WHERE m.user_id=:user "
                        + cursor_clause
                        + "ORDER BY r.created_at DESC,r.id DESC LIMIT :limit"
                    ),
                    parameters,
                )
            ).mappings().all()
        return _page(rows, limit, timestamp_key="created_at", resource_key="id")

    async def get_scan(self, user_id: str, run_id: str) -> dict[str, Any]:
        async with self._database.session() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT r.*,a.last_successful_scan_at FROM x_account_memberships m "
                        "JOIN scan_runs r ON r.x_account_id=m.x_account_id "
                        "JOIN x_accounts a ON a.id=r.x_account_id "
                        "WHERE m.user_id=:user AND r.id=:run"
                    ),
                    {"user": user_id, "run": run_id},
                )
            ).mappings().one_or_none()
        if row is None:
            raise ResourceNotFoundError
        return dict(row)

    async def list_relationships(
        self,
        user_id: str,
        account_id: str,
        *,
        state: str | None,
        search: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        await self._require_account(user_id, account_id)
        clauses = ["s.x_account_id=:account"]
        parameters: dict[str, object] = {"account": account_id, "limit": limit + 1}
        cursor_value = _decode_cursor(cursor)
        if cursor_value is not None:
            clauses.append(
                "(s.updated_at < :cursor_time OR "
                "(s.updated_at = :cursor_time AND s.subject_x_user_id < :cursor_id))"
            )
            parameters["cursor_time"], parameters["cursor_id"] = cursor_value
        if state is not None:
            clauses.append("s.state=:state")
            parameters["state"] = state
        if search is not None:
            clauses.append(
                "(lower(coalesce(m.username,'')) LIKE :search ESCAPE '\\' "
                "OR lower(coalesce(m.display_name,'')) LIKE :search ESCAPE '\\' "
                "OR m.subject_x_user_id LIKE :search ESCAPE '\\')"
            )
            parameters["search"] = f"%{_escape_like(search.casefold())}%"
        async with self._database.session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT s.subject_x_user_id AS x_user_id,m.username,m.display_name,"
                        "s.state,s.non_followback_streak,s.last_snapshot_id AS snapshot_id,"
                        "s.updated_at AS cursor_time "
                        "FROM relationship_states s JOIN snapshot_memberships m "
                        "ON m.snapshot_id=s.last_snapshot_id "
                        "AND m.subject_x_user_id=s.subject_x_user_id WHERE "
                        + " AND ".join(clauses)
                        + " ORDER BY s.updated_at DESC,s.subject_x_user_id DESC LIMIT :limit"
                    ),
                    parameters,
                )
            ).mappings().all()
        items, next_cursor = _page(
            rows, limit, timestamp_key="cursor_time", resource_key="x_user_id"
        )
        for item in items:
            item.pop("cursor_time")
        return items, next_cursor

    async def list_events(
        self,
        user_id: str,
        account_id: str,
        *,
        status: str | None,
        category: str | None,
        event_type: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        await self._require_account(user_id, account_id)
        clauses = ["e.x_account_id=:account"]
        parameters: dict[str, object] = {"account": account_id, "limit": limit + 1}
        cursor_value = _decode_cursor(cursor)
        if cursor_value is not None:
            clauses.append(
                "(e.created_at < :cursor_time OR "
                "(e.created_at = :cursor_time AND e.id < :cursor_id))"
            )
            parameters["cursor_time"], parameters["cursor_id"] = cursor_value
        for column, value in (
            ("status", status),
            ("category", category),
            ("event_type", event_type),
        ):
            if value is not None:
                if column == "status" and value == "NEW":
                    clauses.append("e.status IN ('NEW','OPEN')")
                    continue
                clauses.append(f"e.{column}=:{column}")
                parameters[column] = value
        async with self._database.session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT e.id,e.x_account_id,e.scan_run_id,e.subject_x_user_id,"
                        "m.username,m.display_name,e.category,e.event_type,e.status,e.version,"
                        "e.created_at,e.acknowledged_at FROM relationship_events e "
                        "LEFT JOIN relationship_states s ON s.x_account_id=e.x_account_id "
                        "AND s.subject_x_user_id=e.subject_x_user_id "
                        "LEFT JOIN snapshot_memberships m ON m.snapshot_id=s.last_snapshot_id "
                        "AND m.subject_x_user_id=s.subject_x_user_id WHERE "
                        + " AND ".join(clauses)
                        + " ORDER BY e.created_at DESC,e.id DESC LIMIT :limit"
                    ),
                    parameters,
                )
            ).mappings().all()
        items, next_cursor = _page(
            rows, limit, timestamp_key="created_at", resource_key="id"
        )
        for item in items:
            if item["status"] == "OPEN":
                item["status"] = "NEW"
        return items, next_cursor

    async def acknowledge_event(
        self, user_id: str, event_id: str, expected_version: int
    ) -> dict[str, Any]:
        await self._require_event_role(user_id, event_id, mutation=True)
        now = datetime.now(UTC).isoformat()
        async with self._database.engine.connect() as connection:
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                current = (
                    await connection.execute(
                        text(
                            "SELECT e.version FROM relationship_events e "
                            "JOIN x_account_memberships m ON m.x_account_id=e.x_account_id "
                            "WHERE m.user_id=:user AND e.id=:event"
                        ),
                        {"user": user_id, "event": event_id},
                    )
                ).scalar_one_or_none()
                if current is None:
                    raise ResourceNotFoundError
                if int(current) != expected_version:
                    raise ApplicationError(
                        "RESOURCE_VERSION_CONFLICT", "Resource version has changed", 409
                    )
                await connection.execute(
                    text(
                        "UPDATE relationship_events SET status='ACKNOWLEDGED',"
                        "acknowledged_at=:now,version=version+1 "
                        "WHERE id=:event AND version=:version"
                    ),
                    {"now": now, "event": event_id, "version": expected_version},
                )
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise
        async with self._database.session() as session:
            row = (
                await session.execute(
                    text("SELECT * FROM relationship_events WHERE id=:event"),
                    {"event": event_id},
                )
            ).mappings().one()
        return dict(row)

    async def unbind_account(
        self,
        user_id: str,
        account_id: str,
        expected_version: int,
        request_id: str | None,
    ) -> None:
        role = await self._require_account_role(user_id, account_id, mutation=True)
        if role != "OWNER":
            raise ApplicationError("PERMISSION_DENIED", "Permission denied", 403)
        now = datetime.now(UTC).isoformat()
        replacement = f"unbound:{uuid4()}"
        replacement_hash = hashlib.sha256(replacement.encode()).hexdigest()
        async with self._database.engine.connect() as connection:
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                result = await connection.execute(
                    text(
                        "UPDATE x_accounts SET status='DISABLED',profile_ref=:profile,"
                        "profile_ref_hash=:profile_hash,version=version+1,updated_at=:now "
                        "WHERE id=:account AND owner_user_id=:user AND version=:version"
                    ),
                    {
                        "profile": replacement,
                        "profile_hash": replacement_hash,
                        "now": now,
                        "account": account_id,
                        "user": user_id,
                        "version": expected_version,
                    },
                )
                if result.rowcount != 1:
                    raise ApplicationError(
                        "RESOURCE_VERSION_CONFLICT", "Resource version has changed", 409
                    )
                await connection.execute(
                    text(
                        "DELETE FROM scan_runs WHERE x_account_id=:account "
                        "AND status IN ('QUEUED','RUNNING')"
                    ),
                    {"account": account_id},
                )
                await connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id,actor_user_id,action,resource_type,resource_id,request_id,"
                        "after_json,created_at) VALUES "
                        "(:id,:user,'X_ACCOUNT_UNBOUND','x_account',:account,:request,"
                        ":after,:now)"
                    ),
                    {
                        "id": str(uuid4()),
                        "user": user_id,
                        "account": account_id,
                        "request": request_id,
                        "after": json.dumps({"status": "DISABLED"}),
                        "now": now,
                    },
                )
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

    async def _require_account(self, user_id: str, account_id: str) -> None:
        await self._require_account_role(user_id, account_id)

    async def _require_account_role(
        self, user_id: str, account_id: str, *, mutation: bool = False
    ) -> str:
        async with self._database.session() as session:
            role = await session.scalar(
                text(
                    "SELECT role FROM x_account_memberships "
                    "WHERE user_id=:user AND x_account_id=:account"
                ),
                {"user": user_id, "account": account_id},
            )
        if role is None:
            raise ResourceNotFoundError
        role_value = str(role)
        if mutation and role_value not in {"OWNER", "OPERATOR"}:
            raise ApplicationError("PERMISSION_DENIED", "Permission denied", 403)
        return role_value

    async def _require_event_role(
        self, user_id: str, event_id: str, *, mutation: bool = False
    ) -> str:
        async with self._database.session() as session:
            role = await session.scalar(
                text(
                    "SELECT m.role FROM relationship_events e "
                    "JOIN x_account_memberships m ON m.x_account_id=e.x_account_id "
                    "WHERE m.user_id=:user AND e.id=:event"
                ),
                {"user": user_id, "event": event_id},
            )
        if role is None:
            raise ResourceNotFoundError
        role_value = str(role)
        if mutation and role_value not in {"OWNER", "OPERATOR"}:
            raise ApplicationError("PERMISSION_DENIED", "Permission denied", 403)
        return role_value


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _page(
    rows: Any,
    limit: int,
    *,
    timestamp_key: str,
    resource_key: str,
) -> tuple[list[dict[str, Any]], str | None]:
    items = [dict(row) for row in rows[:limit]]
    if len(rows) <= limit:
        return items, None
    last = items[-1]
    return items, _encode_cursor(last[timestamp_key], str(last[resource_key]))


def _encode_cursor(timestamp: object, resource_id: str) -> str:
    if isinstance(timestamp, datetime):
        timestamp = timestamp.isoformat()
    payload = json.dumps([str(timestamp), resource_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[str, str] | None:
    if cursor is None:
        return None
    try:
        padding = "=" * (-len(cursor) % 4)
        value = json.loads(base64.b64decode(cursor + padding, altchars=b"-_", validate=True))
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not all(isinstance(item, str) and item for item in value)
        ):
            raise ValueError
        datetime.fromisoformat(value[0].replace("Z", "+00:00"))
        return value[0], value[1]
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ApplicationError(
            "REQUEST_VALIDATION_FAILED", "Request validation failed", 422, {"errors": []}
        ) from None
