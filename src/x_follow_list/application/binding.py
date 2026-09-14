from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from x_follow_list.application.errors import ApplicationError, ResourceNotFoundError
from x_follow_list.persistence.database import Database


class BindingConflictError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            "BINDING_CONFLICT", "Browser profile or X account is already bound", 409
        )


class BindingStateError(ApplicationError):
    def __init__(self) -> None:
        super().__init__(
            "BINDING_STATE_CONFLICT", "Binding session is not in the required state", 409
        )


@dataclass(frozen=True, slots=True)
class BindingSession:
    session_id: str
    owner_user_id: str
    provider_config_id: str
    provider_code: str
    provider_config_version: int
    profile_ref: str
    target_account_id: str | None
    status: str
    detected_x_user_id: str | None
    detected_username: str | None
    detected_display_name: str | None
    confirmed_account_id: str | None
    error_code: str | None
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class BindingClaim:
    session_id: str
    worker_id: str
    claim_token: str


class BrowserBindingService:
    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] | None = None,
        session_ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        self._database = database
        self._clock = clock or (lambda: datetime.now(UTC))
        self._session_ttl = session_ttl

    async def create(
        self, owner_user_id: str, provider_config_id: str, profile_ref: str
    ) -> BindingSession:
        if not profile_ref or len(profile_ref) > 255:
            raise BindingStateError()
        now = self._now()
        session_id = str(uuid4())
        profile_hash = self._profile_hash(provider_config_id, profile_ref)
        async with self._database.session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            provider = (
                (
                    await session.execute(
                        text(
                            "SELECT provider_code,config_version FROM browser_provider_configs "
                            "WHERE id=:provider AND owner_user_id=:owner"
                        ),
                        {"provider": provider_config_id, "owner": owner_user_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if provider is None:
                await session.rollback()
                raise ResourceNotFoundError()
            try:
                await session.execute(
                    text(
                        "INSERT INTO browser_bind_sessions "
                        "(id,owner_user_id,provider_config_id,provider_code,"
                        "provider_config_version,profile_ref,profile_ref_hash,status,"
                        "expires_at,created_at,updated_at) VALUES "
                        "(:id,:owner,:provider,:code,:version,:profile,:profile_hash,"
                        "'QUEUED',:expires,:now,:now)"
                    ),
                    {
                        "id": session_id,
                        "owner": owner_user_id,
                        "provider": provider_config_id,
                        "code": str(provider["provider_code"]),
                        "version": int(provider["config_version"]),
                        "profile": profile_ref,
                        "profile_hash": profile_hash,
                        "expires": (now + self._session_ttl).isoformat(),
                        "now": now.isoformat(),
                    },
                )
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise BindingConflictError() from None
        return await self.get(owner_user_id, session_id)

    async def create_revalidation(
        self, owner_user_id: str, account_id: str
    ) -> BindingSession:
        now = self._now()
        session_id = str(uuid4())
        async with self._database.session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            account = (
                (
                    await session.execute(
                        text(
                            "SELECT a.provider_config_id,a.profile_ref,a.profile_ref_hash,"
                            "p.provider_code,p.config_version FROM x_accounts AS a "
                            "JOIN browser_provider_configs AS p "
                            "ON p.id=a.provider_config_id AND p.owner_user_id=a.owner_user_id "
                            "WHERE a.id=:account AND a.owner_user_id=:owner "
                            "AND a.status='REAUTH_REQUIRED'"
                        ),
                        {"account": account_id, "owner": owner_user_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if account is None:
                await session.rollback()
                raise ResourceNotFoundError()
            try:
                await session.execute(
                    text(
                        "INSERT INTO browser_bind_sessions "
                        "(id,owner_user_id,provider_config_id,provider_code,"
                        "provider_config_version,profile_ref,profile_ref_hash,"
                        "target_account_id,status,expires_at,created_at,updated_at) VALUES "
                        "(:id,:owner,:provider,:code,:version,:profile,:profile_hash,"
                        ":account,'QUEUED',:expires,:now,:now)"
                    ),
                    {
                        "id": session_id,
                        "owner": owner_user_id,
                        "provider": str(account["provider_config_id"]),
                        "code": str(account["provider_code"]),
                        "version": int(account["config_version"]),
                        "profile": str(account["profile_ref"]),
                        "profile_hash": str(account["profile_ref_hash"]),
                        "account": account_id,
                        "expires": (now + self._session_ttl).isoformat(),
                        "now": now.isoformat(),
                    },
                )
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise BindingConflictError() from None
        return await self.get(owner_user_id, session_id)

    async def mark_reauth_required(
        self, owner_user_id: str, account_id: str
    ) -> None:
        now = self._now().isoformat()
        async with self._database.session() as session:
            result = await session.execute(
                text(
                    "UPDATE x_accounts SET status='REAUTH_REQUIRED',updated_at=:now "
                    "WHERE id=:account AND owner_user_id=:owner "
                    "AND status IN ('READY','REAUTH_REQUIRED')"
                ),
                {"now": now, "account": account_id, "owner": owner_user_id},
            )
            if getattr(result, "rowcount", 0) != 1:
                await session.rollback()
                raise ResourceNotFoundError()
            await session.commit()

    async def get(self, owner_user_id: str, session_id: str) -> BindingSession:
        async with self._database.session() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM browser_bind_sessions "
                            "WHERE id=:id AND owner_user_id=:owner"
                        ),
                        {"id": session_id, "owner": owner_user_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ResourceNotFoundError()
        return self._binding_from_row(row)

    async def claim_next(self, worker_id: str) -> BindingClaim | None:
        now = self._now()
        claim_token = str(uuid4())
        async with self._database.session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            session_id = await session.scalar(
                text(
                    "SELECT id FROM browser_bind_sessions "
                    "WHERE status='QUEUED' AND expires_at>:now "
                    "ORDER BY created_at,id LIMIT 1"
                ),
                {"now": now.isoformat()},
            )
            if session_id is None:
                await session.rollback()
                return None
            result = await session.execute(
                text(
                    "UPDATE browser_bind_sessions SET status='RUNNING',worker_id=:worker,"
                    "claim_token=:token,updated_at=:now WHERE id=:id AND status='QUEUED'"
                ),
                {
                    "worker": worker_id,
                    "token": claim_token,
                    "now": now.isoformat(),
                    "id": session_id,
                },
            )
            if getattr(result, "rowcount", 0) != 1:
                await session.rollback()
                return None
            await session.commit()
        return BindingClaim(str(session_id), worker_id, claim_token)

    async def record_detected_identity(
        self,
        claim: BindingClaim,
        *,
        x_user_id: str,
        username: str | None,
        display_name: str | None,
    ) -> None:
        if not x_user_id:
            raise BindingStateError()
        now = self._now()
        async with self._database.session() as session:
            result = await session.execute(
                text(
                    "UPDATE browser_bind_sessions SET status='AWAITING_CONFIRMATION',"
                    "detected_x_user_id=:x,detected_username=:username,"
                    "detected_display_name=:display_name,updated_at=:now "
                    "WHERE id=:id AND status='RUNNING' AND worker_id=:worker "
                    "AND claim_token=:token AND expires_at>:now"
                ),
                {
                    "x": x_user_id,
                    "username": username,
                    "display_name": display_name,
                    "now": now.isoformat(),
                    "id": claim.session_id,
                    "worker": claim.worker_id,
                    "token": claim.claim_token,
                },
            )
            if getattr(result, "rowcount", 0) != 1:
                await session.rollback()
                raise BindingStateError()
            await session.commit()

    async def confirm(self, owner_user_id: str, session_id: str) -> str:
        now = self._now()
        account_id = str(uuid4())
        async with self._database.session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM browser_bind_sessions "
                            "WHERE id=:id AND owner_user_id=:owner"
                        ),
                        {"id": session_id, "owner": owner_user_id},
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                await session.rollback()
                raise ResourceNotFoundError()
            if row["status"] != "AWAITING_CONFIRMATION" or row["detected_x_user_id"] is None:
                await session.rollback()
                raise BindingStateError()
            try:
                if row["target_account_id"] is None:
                    await self._insert_account(
                        session, row, account_id, owner_user_id, now.isoformat()
                    )
                else:
                    account_id = str(row["target_account_id"])
                    updated = await session.execute(
                        text(
                            "UPDATE x_accounts SET username=:username,"
                            "display_name=:display_name,status='READY',updated_at=:now "
                            "WHERE id=:account AND owner_user_id=:owner "
                            "AND status='REAUTH_REQUIRED' AND x_user_id=:x"
                        ),
                        {
                            "username": row["detected_username"],
                            "display_name": row["detected_display_name"],
                            "now": now.isoformat(),
                            "account": account_id,
                            "owner": owner_user_id,
                            "x": str(row["detected_x_user_id"]),
                        },
                    )
                    if getattr(updated, "rowcount", 0) != 1:
                        await session.rollback()
                        raise BindingConflictError()
                await session.execute(
                    text(
                        "UPDATE browser_bind_sessions SET status='CONFIRMED',"
                        "confirmed_account_id=:account,updated_at=:now WHERE id=:id"
                    ),
                    {"account": account_id, "now": now.isoformat(), "id": session_id},
                )
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise BindingConflictError() from None
        return account_id

    @staticmethod
    async def _insert_account(
        session: AsyncSession,
        row: RowMapping,
        account_id: str,
        owner_user_id: str,
        now: str,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO x_accounts "
                "(id,owner_user_id,provider_config_id,profile_ref,profile_ref_hash,"
                "x_user_id,username,display_name,status,created_at,updated_at) VALUES "
                "(:id,:owner,:provider,:profile,:profile_hash,:x,:username,"
                ":display_name,'READY',:now,:now)"
            ),
            {
                "id": account_id,
                "owner": owner_user_id,
                "provider": str(row["provider_config_id"]),
                "profile": str(row["profile_ref"]),
                "profile_hash": str(row["profile_ref_hash"]),
                "x": str(row["detected_x_user_id"]),
                "username": row["detected_username"],
                "display_name": row["detected_display_name"],
                "now": now,
            },
        )
        await session.execute(
            text(
                "INSERT INTO x_account_memberships VALUES "
                "(:account,:owner,'OWNER',:now,:now)"
            ),
            {"account": account_id, "owner": owner_user_id, "now": now},
        )

    async def cancel(self, owner_user_id: str, session_id: str) -> None:
        now = self._now().isoformat()
        async with self._database.session() as session:
            result = await session.execute(
                text(
                    "UPDATE browser_bind_sessions SET status='CANCELLED',updated_at=:now "
                    "WHERE id=:id AND owner_user_id=:owner "
                    "AND status IN ('QUEUED','RUNNING','AWAITING_CONFIRMATION')"
                ),
                {"now": now, "id": session_id, "owner": owner_user_id},
            )
            if getattr(result, "rowcount", 0) != 1:
                exists = await session.scalar(
                    text(
                        "SELECT count(*) FROM browser_bind_sessions "
                        "WHERE id=:id AND owner_user_id=:owner"
                    ),
                    {"id": session_id, "owner": owner_user_id},
                )
                await session.rollback()
                if not exists:
                    raise ResourceNotFoundError()
                raise BindingStateError()
            await session.commit()

    async def expire_due(self) -> list[str]:
        now = self._now().isoformat()
        async with self._database.session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            ids = list(
                await session.scalars(
                    text(
                        "SELECT id FROM browser_bind_sessions WHERE expires_at<=:now "
                        "AND status IN ('QUEUED','RUNNING','AWAITING_CONFIRMATION') "
                        "ORDER BY created_at,id"
                    ),
                    {"now": now},
                )
            )
            if ids:
                placeholders = ",".join(f":id_{index}" for index in range(len(ids)))
                parameters = {f"id_{index}": value for index, value in enumerate(ids)}
                parameters["now"] = now
                await session.execute(
                    text(
                        "UPDATE browser_bind_sessions SET status='EXPIRED',"
                        "error_code='BIND_TIMEOUT',updated_at=:now "
                        f"WHERE id IN ({placeholders})"  # noqa: S608
                    ),
                    parameters,
                )
            await session.commit()
        return [str(value) for value in ids]

    async def expire_claim(self, claim: BindingClaim) -> None:
        await self._finish_claim(claim, "EXPIRED", "BIND_TIMEOUT")

    async def fail_claim(self, claim: BindingClaim, error_code: str) -> None:
        await self._finish_claim(claim, "FAILED", error_code)

    async def _finish_claim(
        self, claim: BindingClaim, status: str, error_code: str
    ) -> None:
        now = self._now().isoformat()
        async with self._database.session() as session:
            result = await session.execute(
                text(
                    "UPDATE browser_bind_sessions SET status=:status,error_code=:error,"
                    "updated_at=:now WHERE id=:id AND status='RUNNING' "
                    "AND worker_id=:worker AND claim_token=:token"
                ),
                {
                    "status": status,
                    "error": error_code,
                    "now": now,
                    "id": claim.session_id,
                    "worker": claim.worker_id,
                    "token": claim.claim_token,
                },
            )
            if getattr(result, "rowcount", 0) != 1:
                await session.rollback()
                raise BindingStateError()
            await session.commit()

    def _now(self) -> datetime:
        return self._clock().astimezone(UTC)

    @staticmethod
    def _profile_hash(provider_config_id: str, profile_ref: str) -> str:
        return hashlib.sha256(
            f"{provider_config_id}\0{profile_ref}".encode()
        ).hexdigest()

    @staticmethod
    def _binding_from_row(values: RowMapping) -> BindingSession:
        return BindingSession(
            session_id=str(values["id"]),
            owner_user_id=str(values["owner_user_id"]),
            provider_config_id=str(values["provider_config_id"]),
            provider_code=str(values["provider_code"]),
            provider_config_version=int(values["provider_config_version"]),
            profile_ref=str(values["profile_ref"]),
            target_account_id=(
                str(values["target_account_id"])
                if values["target_account_id"] is not None
                else None
            ),
            status=str(values["status"]),
            detected_x_user_id=(
                str(values["detected_x_user_id"])
                if values["detected_x_user_id"] is not None else None
            ),
            detected_username=(
                str(values["detected_username"])
                if values["detected_username"] is not None else None
            ),
            detected_display_name=(
                str(values["detected_display_name"])
                if values["detected_display_name"] is not None else None
            ),
            confirmed_account_id=(
                str(values["confirmed_account_id"])
                if values["confirmed_account_id"] is not None else None
            ),
            error_code=(
                str(values["error_code"])
                if values["error_code"] is not None else None
            ),
            expires_at=datetime.fromisoformat(str(values["expires_at"])),
        )
