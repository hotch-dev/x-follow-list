from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from x_follow_list.application.errors import ApplicationError, ResourceNotFoundError
from x_follow_list.application.rule_conflicts import (
    open_blocklist_conflict,
    resolve_blocklist_conflict,
)
from x_follow_list.persistence.database import Database


class AccountRuleService:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create(
        self,
        user_id: str,
        *,
        account_id: str,
        subject_x_user_id: str,
        rule_type: str,
        reason: str,
        idempotency_key: str,
        request_id: str | None,
    ) -> dict[str, Any]:
        request_hash = hashlib.sha256(
            json.dumps(
                [account_id, subject_x_user_id, rule_type, reason], separators=(",", ":")
            ).encode()
        ).hexdigest()
        scope = f"account-rule:create:{account_id}"
        now = datetime.now(UTC)
        now_value = now.isoformat()
        async with self._database.session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            try:
                await self._require_account(session, user_id, account_id, mutation=True)
                replay = (
                    (
                        await session.execute(
                            text(
                                "SELECT request_hash,response_json FROM idempotency_keys "
                                "WHERE user_id=:user AND scope=:scope AND idempotency_key=:key"
                            ),
                            {"user": user_id, "scope": scope, "key": idempotency_key},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if replay is not None:
                    if replay["request_hash"] != request_hash:
                        raise ApplicationError(
                            "IDEMPOTENCY_CONFLICT",
                            "Idempotency key conflicts with this request",
                            409,
                        )
                    value = replay["response_json"]
                    await session.rollback()
                    return json.loads(value) if isinstance(value, str) else dict(value)

                existing = await session.scalar(
                    text(
                        "SELECT id FROM account_rules WHERE x_account_id=:account "
                        "AND subject_x_user_id=:subject"
                    ),
                    {"account": account_id, "subject": subject_x_user_id},
                )
                if existing is not None:
                    raise ApplicationError(
                        "RULE_CONFLICT", "Remove the existing rule before changing its type", 409
                    )
                rule_id = str(uuid4())
                await session.execute(
                    text(
                        "INSERT INTO account_rules "
                        "(id,x_account_id,subject_x_user_id,rule_type,reason,created_by,"
                        "created_at,updated_at) VALUES "
                        "(:id,:account,:subject,:type,:reason,:user,:now,:now)"
                    ),
                    {
                        "id": rule_id,
                        "account": account_id,
                        "subject": subject_x_user_id,
                        "type": rule_type,
                        "reason": reason,
                        "user": user_id,
                        "now": now_value,
                    },
                )
                snapshot = (
                    (
                        await session.execute(
                            text(
                                "SELECT id,scan_run_id FROM relationship_snapshots "
                            "WHERE x_account_id=:account "
                            "ORDER BY captured_at DESC,id DESC LIMIT 1"
                            ),
                            {"account": account_id},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if rule_type == "BUSINESS_BLOCKLIST" and snapshot is not None:
                    following = await session.scalar(
                        text(
                            "SELECT i_follow FROM snapshot_memberships "
                            "WHERE snapshot_id=:snapshot AND subject_x_user_id=:subject"
                        ),
                        {"snapshot": snapshot["id"], "subject": subject_x_user_id},
                    )
                    if following:
                        await open_blocklist_conflict(
                            session,
                            account_id=account_id,
                            subject_x_user_id=subject_x_user_id,
                            scan_run_id=str(snapshot["scan_run_id"]),
                            source_key=f"rule:{rule_id}",
                            reason=reason,
                            now=now_value,
                        )
                result: dict[str, Any] = {
                    "id": rule_id,
                    "x_account_id": account_id,
                    "subject_x_user_id": subject_x_user_id,
                    "rule_type": rule_type,
                    "reason": reason,
                    "version": 1,
                    "created_at": now_value,
                    "updated_at": now_value,
                    "scan_required": snapshot is None,
                }
                await session.execute(
                    text(
                        "INSERT INTO idempotency_keys "
                        "(id,user_id,scope,idempotency_key,request_hash,response_status,"
                        "response_json,created_at,expires_at) VALUES "
                        "(:id,:user,:scope,:key,:hash,201,:response,:now,:expires)"
                    ),
                    {
                        "id": str(uuid4()),
                        "user": user_id,
                        "scope": scope,
                        "key": idempotency_key,
                        "hash": request_hash,
                        "response": json.dumps(result),
                        "now": now_value,
                        "expires": (now + timedelta(hours=24)).isoformat(),
                    },
                )
                await self._audit(
                    session,
                    user_id=user_id,
                    action="ACCOUNT_RULE_CREATED",
                    rule_id=rule_id,
                    request_id=request_id,
                    before=None,
                    after=result,
                    now=now_value,
                )
                await session.commit()
                return result
            except BaseException:
                await session.rollback()
                raise

    async def list_rules(
        self, user_id: str, account_id: str, limit: int, cursor: str | None
    ) -> tuple[list[dict[str, Any]], str | None]:
        async with self._database.session() as session:
            await self._require_account(session, user_id, account_id)
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT id,x_account_id,subject_x_user_id,rule_type,reason,"
                            "version,created_at,updated_at FROM account_rules "
                            "WHERE x_account_id=:account AND (:cursor IS NULL OR id>:cursor) "
                            "ORDER BY id LIMIT :limit"
                        ),
                        {"account": account_id, "cursor": cursor, "limit": limit + 1},
                    )
                )
                .mappings()
                .all()
            )
        page = [dict(row) for row in rows[:limit]]
        return page, str(page[-1]["id"]) if len(rows) > limit else None

    async def delete(
        self,
        user_id: str,
        rule_id: str,
        version: int,
        request_id: str | None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        async with self._database.session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            try:
                row = (
                    (
                        await session.execute(
                            text(
                                "SELECT id,x_account_id,subject_x_user_id,rule_type,reason,version "
                                "FROM account_rules WHERE id=:id"
                            ),
                            {"id": rule_id},
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if row is None:
                    raise ResourceNotFoundError
                account_id = str(row["x_account_id"])
                await self._require_account(session, user_id, account_id, mutation=True)
                if int(row["version"]) != version:
                    raise ApplicationError(
                        "RESOURCE_VERSION_CONFLICT", "Resource version has changed", 409
                    )
                if row["rule_type"] == "BUSINESS_BLOCKLIST":
                    await resolve_blocklist_conflict(
                        session,
                        account_id=account_id,
                        subject_x_user_id=str(row["subject_x_user_id"]),
                        now=now,
                    )
                await session.execute(
                    text("DELETE FROM account_rules WHERE id=:id"), {"id": rule_id}
                )
                await self._audit(
                    session,
                    user_id=user_id,
                    action="ACCOUNT_RULE_DELETED",
                    rule_id=rule_id,
                    request_id=request_id,
                    before=dict(row),
                    after=None,
                    now=now,
                )
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    @staticmethod
    async def _require_account(
        session: AsyncSession, user_id: str, account_id: str, *, mutation: bool = False
    ) -> None:
        role = await session.scalar(
            text(
                "SELECT role FROM x_account_memberships "
                "WHERE x_account_id=:account AND user_id=:user"
            ),
            {"account": account_id, "user": user_id},
        )
        if role is None:
            raise ResourceNotFoundError
        if mutation and role not in {"OWNER", "OPERATOR"}:
            raise ApplicationError("PERMISSION_DENIED", "Permission denied", 403)

    @staticmethod
    async def _audit(
        session: AsyncSession,
        *,
        user_id: str,
        action: str,
        rule_id: str,
        request_id: str | None,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        now: str,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO audit_logs "
                "(id,actor_user_id,action,resource_type,resource_id,request_id,"
                "before_json,after_json,created_at) VALUES "
                "(:id,:user,:action,'account_rule',:rule,:request,:before,:after,:now)"
            ),
            {
                "id": str(uuid4()),
                "user": user_id,
                "action": action,
                "rule": rule_id,
                "request": request_id,
                "before": json.dumps(before) if before is not None else None,
                "after": json.dumps(after) if after is not None else None,
                "now": now,
            },
        )
