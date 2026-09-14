from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

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
        self, user_id: str, account_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        try:
            run_id = await self._coordinator.enqueue(
                user_id,
                account_id,
                idempotency_key,
                f"manual-scan:v1:{account_id}",
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

    async def list_scans(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        async with self._database.session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT r.*,a.last_successful_scan_at FROM x_account_memberships m "
                        "JOIN scan_runs r ON r.x_account_id=m.x_account_id "
                        "JOIN x_accounts a ON a.id=r.x_account_id "
                        "WHERE m.user_id=:user ORDER BY r.created_at DESC,r.id DESC LIMIT :limit"
                    ),
                    {"user": user_id, "limit": limit},
                )
            ).mappings().all()
        return [dict(row) for row in rows]

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
    ) -> list[dict[str, Any]]:
        await self._require_account(user_id, account_id)
        clauses = ["s.x_account_id=:account"]
        parameters: dict[str, object] = {"account": account_id, "limit": limit}
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
                        "s.state,s.non_followback_streak,s.last_snapshot_id AS snapshot_id "
                        "FROM relationship_states s JOIN snapshot_memberships m "
                        "ON m.snapshot_id=s.last_snapshot_id "
                        "AND m.subject_x_user_id=s.subject_x_user_id WHERE "
                        + " AND ".join(clauses)
                        + " ORDER BY s.updated_at DESC,s.subject_x_user_id DESC LIMIT :limit"
                    ),
                    parameters,
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    async def list_events(
        self,
        user_id: str,
        account_id: str,
        *,
        status: str | None,
        category: str | None,
        event_type: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        await self._require_account(user_id, account_id)
        clauses = ["e.x_account_id=:account"]
        parameters: dict[str, object] = {"account": account_id, "limit": limit}
        for column, value in (
            ("status", status),
            ("category", category),
            ("event_type", event_type),
        ):
            if value is not None:
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
        return [dict(row) for row in rows]

    async def acknowledge_event(
        self, user_id: str, event_id: str, expected_version: int
    ) -> dict[str, Any]:
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

    async def _require_account(self, user_id: str, account_id: str) -> None:
        async with self._database.session() as session:
            found = await session.scalar(
                text(
                    "SELECT 1 FROM x_account_memberships "
                    "WHERE user_id=:user AND x_account_id=:account"
                ),
                {"user": user_id, "account": account_id},
            )
        if found is None:
            raise ResourceNotFoundError


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

