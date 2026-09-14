from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from x_follow_list.persistence.database import Database


class IdempotencyConflictError(RuntimeError):
    """An idempotency key was reused for a different request."""


class ScanAlreadyActiveError(RuntimeError):
    """The account already has a queued or running scan."""


class LeaseConflictError(RuntimeError):
    """A required browser resource is held by another task."""


class LeaseLostError(RuntimeError):
    """A worker no longer owns its claim or resource fence."""


@dataclass(frozen=True, slots=True)
class ScanClaim:
    run_id: str
    x_account_id: str
    worker_id: str
    claim_token: str


@dataclass(frozen=True, slots=True)
class ResourceLease:
    resource_key: str
    fencing_token: int


class ScanCoordinator:
    def __init__(self, database: Database, lease_ttl: timedelta = timedelta(seconds=30)) -> None:
        if lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be positive")
        self._database = database
        self._lease_ttl = lease_ttl

    async def enqueue(
        self,
        user_id: str,
        x_account_id: str,
        idempotency_key: str,
        request_hash: str,
        request_id: str | None = None,
    ) -> str:
        now = datetime.now(UTC)
        scope = f"scan-run:{x_account_id}"
        async with self._database.engine.connect() as connection:
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                existing = (
                    await connection.execute(
                        text(
                            "SELECT request_hash,response_json FROM idempotency_keys "
                            "WHERE user_id=:user AND scope=:scope AND idempotency_key=:key"
                        ),
                        {"user": user_id, "scope": scope, "key": idempotency_key},
                    )
                ).first()
                if existing is not None:
                    if str(existing[0]) != request_hash:
                        raise IdempotencyConflictError("idempotency key request does not match")
                    response = existing[1]
                    if isinstance(response, str):
                        response = json.loads(response)
                    await connection.commit()
                    return str(response["scan_run_id"])

                account_exists = await connection.scalar(
                    text(
                        "SELECT 1 FROM x_accounts "
                        "WHERE id=:account AND owner_user_id=:user AND status='READY'"
                    ),
                    {"account": x_account_id, "user": user_id},
                )
                if account_exists is None:
                    raise LookupError("ready account not found")
                active_run = await connection.scalar(
                    text(
                        "SELECT 1 FROM scan_runs WHERE x_account_id=:account "
                        "AND status IN ('QUEUED','RUNNING') LIMIT 1"
                    ),
                    {"account": x_account_id},
                )
                if active_run is not None:
                    raise ScanAlreadyActiveError("account already has an active scan")
                run_id = str(uuid4())
                now_value = now.isoformat()
                await connection.execute(
                    text(
                        "INSERT INTO scan_runs "
                        "(id,x_account_id,requested_by_user_id,status,created_at) "
                        "VALUES (:id,:account,:user,'QUEUED',:now)"
                    ),
                    {"id": run_id, "account": x_account_id, "user": user_id, "now": now_value},
                )
                await connection.execute(
                    text(
                        "INSERT INTO idempotency_keys "
                        "(id,user_id,scope,idempotency_key,request_hash,response_status,response_json,"
                        "created_at,expires_at) VALUES "
                        "(:id,:user,:scope,:key,:hash,202,:response,:now,:expires)"
                    ),
                    {
                        "id": str(uuid4()),
                        "user": user_id,
                        "scope": scope,
                        "key": idempotency_key,
                        "hash": request_hash,
                        "response": json.dumps({"scan_run_id": run_id}),
                        "now": now_value,
                        "expires": (now + timedelta(hours=24)).isoformat(),
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(id,actor_user_id,action,resource_type,resource_id,request_id,"
                        "after_json,created_at) VALUES "
                        "(:id,:user,'SCAN_RUN_CREATED','scan_run',:run,:request,:after,:now)"
                    ),
                    {
                        "id": str(uuid4()),
                        "user": user_id,
                        "run": run_id,
                        "request": request_id,
                        "after": json.dumps({"status": "QUEUED", "x_account_id": x_account_id}),
                        "now": now_value,
                    },
                )
                await connection.commit()
                return run_id
            except BaseException:
                await connection.rollback()
                raise

    async def claim_next(self, worker_id: str) -> ScanClaim | None:
        now = datetime.now(UTC).isoformat()
        token = str(uuid4())
        async with self._database.engine.connect() as connection:
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                row = (
                    await connection.execute(
                        text(
                            "SELECT id,x_account_id FROM scan_runs WHERE status='QUEUED' "
                            "ORDER BY created_at,id LIMIT 1"
                        )
                    )
                ).first()
                if row is None:
                    await connection.commit()
                    return None
                result = await connection.execute(
                    text(
                        "UPDATE scan_runs SET status='RUNNING',worker_id=:worker,"
                        "claim_token=:token,started_at=:now,heartbeat_at=:now "
                        "WHERE id=:id AND status='QUEUED'"
                    ),
                    {"worker": worker_id, "token": token, "now": now, "id": row[0]},
                )
                if result.rowcount != 1:
                    await connection.rollback()
                    return None
                await connection.commit()
                return ScanClaim(str(row[0]), str(row[1]), worker_id, token)
            except BaseException:
                await connection.rollback()
                raise

    async def acquire_scan_leases(self, claim: ScanClaim) -> tuple[ResourceLease, ...]:
        now = datetime.now(UTC)
        async with self._database.engine.connect() as connection:
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                await self._require_claim(connection, claim)
                account = (
                    await connection.execute(
                        text(
                            "SELECT provider_config_id,profile_ref_hash FROM x_accounts "
                            "WHERE id=:account"
                        ),
                        {"account": claim.x_account_id},
                    )
                ).one()
                keys = (
                    f"x-account:{claim.x_account_id}",
                    f"browser-profile:{account[0]}:{account[1]}",
                )
                leases = tuple(
                    [await self._acquire_one(connection, key, claim.run_id, now) for key in keys]
                )
                await connection.commit()
                return leases
            except BaseException:
                await connection.rollback()
                raise

    async def heartbeat(
        self, claim: ScanClaim, leases: tuple[ResourceLease, ...] | list[ResourceLease]
    ) -> None:
        now = datetime.now(UTC)
        async with self._database.engine.connect() as connection:
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                await self._require_claim(connection, claim)
                await self._require_active_leases(connection, claim.run_id, leases, now)
                expires = (now + self._lease_ttl).isoformat()
                for lease in leases:
                    await connection.execute(
                        text(
                            "UPDATE resource_leases SET heartbeat_at=:now,expires_at=:expires "
                            "WHERE resource_key=:key AND owner_task_id=:run "
                            "AND fencing_token=:token"
                        ),
                        {
                            "now": now.isoformat(),
                            "expires": expires,
                            "key": lease.resource_key,
                            "run": claim.run_id,
                            "token": lease.fencing_token,
                        },
                    )
                await connection.execute(
                    text("UPDATE scan_runs SET heartbeat_at=:now WHERE id=:run"),
                    {"now": now.isoformat(), "run": claim.run_id},
                )
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

    async def finish_failed(
        self,
        claim: ScanClaim,
        leases: tuple[ResourceLease, ...] | list[ResourceLease],
        error_code: str,
        error_summary: str,
    ) -> None:
        now = datetime.now(UTC)
        async with self._database.engine.connect() as connection:
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                await self._require_claim(connection, claim)
                await self._require_active_leases(connection, claim.run_id, leases, now)
                result = await connection.execute(
                    text(
                        "UPDATE scan_runs SET status='FAILED',error_code=:code,"
                        "error_summary=:summary,finished_at=:now "
                        "WHERE id=:run AND status='RUNNING' AND worker_id=:worker "
                        "AND claim_token=:claim"
                    ),
                    {
                        "code": error_code,
                        "summary": error_summary,
                        "now": now.isoformat(),
                        "run": claim.run_id,
                        "worker": claim.worker_id,
                        "claim": claim.claim_token,
                    },
                )
                if result.rowcount != 1:
                    raise LeaseLostError("scan claim was lost")
                await self._expire_owned_leases(connection, claim.run_id, leases, now)
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

    async def finish_unleased_failed(
        self, claim: ScanClaim, error_code: str, error_summary: str
    ) -> None:
        """Close a valid claim that could not acquire its complete resource set."""
        now = datetime.now(UTC).isoformat()
        async with self._database.engine.connect() as connection:
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                await self._require_claim(connection, claim)
                result = await connection.execute(
                    text(
                        "UPDATE scan_runs SET status='FAILED',error_code=:code,"
                        "error_summary=:summary,finished_at=:now "
                        "WHERE id=:run AND status='RUNNING' AND worker_id=:worker "
                        "AND claim_token=:claim"
                    ),
                    {
                        "code": error_code,
                        "summary": error_summary,
                        "now": now,
                        "run": claim.run_id,
                        "worker": claim.worker_id,
                        "claim": claim.claim_token,
                    },
                )
                if result.rowcount != 1:
                    raise LeaseLostError("scan claim was lost")
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

    async def recover_expired_runs(self) -> list[str]:
        current = datetime.now(UTC)
        now = current.isoformat()
        heartbeat_cutoff = (current - self._lease_ttl).isoformat()
        async with self._database.engine.connect() as connection:
            await connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                run_ids = [
                    str(value)
                    for value in (
                        await connection.scalars(
                            text(
                                "SELECT r.id FROM scan_runs AS r WHERE r.status='RUNNING' AND ("
                                "r.heartbeat_at IS NULL OR r.heartbeat_at <= :heartbeat_cutoff OR "
                                "EXISTS (SELECT 1 FROM resource_leases AS l "
                                "WHERE l.owner_task_id=r.id AND l.expires_at <= :now)) "
                                "ORDER BY r.id"
                            ),
                            {"now": now, "heartbeat_cutoff": heartbeat_cutoff},
                        )
                    ).all()
                ]
                for run_id in run_ids:
                    await connection.execute(
                        text(
                            "UPDATE scan_runs SET status='FAILED',error_code='BROWSER_CRASH',"
                            "error_summary='worker heartbeat expired; scan was not resumed',"
                            "finished_at=:now WHERE id=:run AND status='RUNNING'"
                        ),
                        {"now": now, "run": run_id},
                    )
                await connection.commit()
                return run_ids
            except BaseException:
                await connection.rollback()
                raise

    @classmethod
    async def require_fence(
        cls,
        connection: AsyncConnection | AsyncSession,
        claim: ScanClaim,
        leases: tuple[ResourceLease, ...] | list[ResourceLease],
        now: datetime | None = None,
    ) -> None:
        """Validate claim and both resource tokens inside a caller's write transaction."""
        await cls._require_claim(connection, claim)
        await cls._require_active_leases(connection, claim.run_id, leases, now or datetime.now(UTC))

    async def _acquire_one(
        self, connection: AsyncConnection, resource_key: str, run_id: str, now: datetime
    ) -> ResourceLease:
        row = (
            await connection.execute(
                text(
                    "SELECT owner_task_id,fencing_token,expires_at FROM resource_leases "
                    "WHERE resource_key=:key"
                ),
                {"key": resource_key},
            )
        ).first()
        expires = (now + self._lease_ttl).isoformat()
        if row is None:
            token = 1
            await connection.execute(
                text(
                    "INSERT INTO resource_leases "
                    "(resource_key,owner_task_type,owner_task_id,fencing_token,heartbeat_at,"
                    "expires_at) VALUES (:key,'SCAN',:run,:token,:now,:expires)"
                ),
                {
                    "key": resource_key,
                    "run": run_id,
                    "token": token,
                    "now": now.isoformat(),
                    "expires": expires,
                },
            )
        elif str(row[0]) == run_id and self._as_datetime(row[2]) > now:
            token = int(row[1])
        elif self._as_datetime(row[2]) <= now:
            token = int(row[1]) + 1
            await connection.execute(
                text(
                    "UPDATE resource_leases SET owner_task_type='SCAN',owner_task_id=:run,"
                    "fencing_token=:token,heartbeat_at=:now,expires_at=:expires "
                    "WHERE resource_key=:key"
                ),
                {
                    "run": run_id,
                    "token": token,
                    "now": now.isoformat(),
                    "expires": expires,
                    "key": resource_key,
                },
            )
        else:
            raise LeaseConflictError(f"resource is leased: {resource_key}")
        return ResourceLease(resource_key, token)

    @staticmethod
    async def _require_claim(
        connection: AsyncConnection | AsyncSession, claim: ScanClaim
    ) -> None:
        valid = await connection.scalar(
            text(
                "SELECT 1 FROM scan_runs WHERE id=:run AND x_account_id=:account "
                "AND status='RUNNING' AND worker_id=:worker AND claim_token=:claim"
            ),
            {
                "run": claim.run_id,
                "account": claim.x_account_id,
                "worker": claim.worker_id,
                "claim": claim.claim_token,
            },
        )
        if valid is None:
            raise LeaseLostError("scan claim was lost")

    @classmethod
    async def _require_active_leases(
        cls,
        connection: AsyncConnection | AsyncSession,
        run_id: str,
        leases: tuple[ResourceLease, ...] | list[ResourceLease],
        now: datetime,
    ) -> None:
        if len(leases) != 2:
            raise LeaseLostError("scan requires account and profile leases")
        for lease in leases:
            row = (
                await connection.execute(
                    text(
                        "SELECT owner_task_id,fencing_token,expires_at FROM resource_leases "
                        "WHERE resource_key=:key"
                    ),
                    {"key": lease.resource_key},
                )
            ).first()
            if (
                row is None
                or str(row[0]) != run_id
                or int(row[1]) != lease.fencing_token
                or cls._as_datetime(row[2]) <= now
            ):
                raise LeaseLostError(f"resource lease was lost: {lease.resource_key}")

    @staticmethod
    async def _expire_owned_leases(
        connection: AsyncConnection,
        run_id: str,
        leases: tuple[ResourceLease, ...] | list[ResourceLease],
        now: datetime,
    ) -> None:
        for lease in leases:
            await connection.execute(
                text(
                    "UPDATE resource_leases SET heartbeat_at=:now,expires_at=:now "
                    "WHERE resource_key=:key AND owner_task_id=:run AND fencing_token=:token"
                ),
                {
                    "now": now.isoformat(),
                    "key": lease.resource_key,
                    "run": run_id,
                    "token": lease.fencing_token,
                },
            )

    @staticmethod
    def _as_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
