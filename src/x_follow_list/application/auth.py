from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from x_follow_list.application.errors import ApplicationError
from x_follow_list.config import Settings
from x_follow_list.security.bootstrap import BootstrapTokenManager
from x_follow_list.security.passwords import PasswordHasher

SESSION_COOKIE_NAME: Final = "x_follow_list_session"


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user_id: str
    login: str
    role: str
    session_id: str
    csrf_token_hash: str


@dataclass(frozen=True, slots=True)
class CreatedSession:
    token: str
    csrf_token: str
    user: AuthenticatedUser


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class AuthService:
    def __init__(
        self,
        settings: Settings,
        bootstrap_tokens: BootstrapTokenManager,
        password_hasher: PasswordHasher | None = None,
    ) -> None:
        self._settings = settings
        self._bootstrap_tokens = bootstrap_tokens
        self._passwords = password_hasher or PasswordHasher()
        self._dummy_password_hash = self._passwords.hash("timing-equalization-password")

    async def bootstrap_owner(
        self,
        session: AsyncSession,
        *,
        login: str,
        password: str,
        bootstrap_token: str,
        request_id: str,
    ) -> AuthenticatedUser:
        await session.execute(text("BEGIN IMMEDIATE"))
        try:
            user_count = await session.scalar(text("SELECT count(*) FROM users"))
            if user_count:
                self._bootstrap_tokens.consume()
                raise ApplicationError(
                    "BOOTSTRAP_ALREADY_COMPLETED", "Owner bootstrap is already complete", 409
                )
            expected_token = self._bootstrap_tokens.get_or_create()
            if not hmac.compare_digest(bootstrap_token, expected_token):
                raise ApplicationError("BOOTSTRAP_TOKEN_INVALID", "Bootstrap token is invalid", 401)

            user_id = str(uuid4())
            normalized_login = login.strip().casefold()
            now = _utc_now()
            await session.execute(
                text(
                    "INSERT INTO users (id,email,password_hash,role,created_at,updated_at) "
                    "VALUES (:id,:login,:password_hash,'OWNER',:now,:now)"
                ),
                {
                    "id": user_id,
                    "login": normalized_login,
                    "password_hash": self._passwords.hash(password),
                    "now": now,
                },
            )
            await self._audit(
                session,
                actor_user_id=user_id,
                action="AUTH_OWNER_BOOTSTRAPPED",
                resource_type="user",
                resource_id=user_id,
                request_id=request_id,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        self._bootstrap_tokens.consume()
        return AuthenticatedUser(user_id, normalized_login, "OWNER", "", "")

    async def create_session(
        self,
        session: AsyncSession,
        *,
        login: str,
        password: str,
        request_id: str,
    ) -> CreatedSession:
        normalized_login = login.strip().casefold()
        row = (
            await session.execute(
                text("SELECT id,email,password_hash,role FROM users WHERE email = :login"),
                {"login": normalized_login},
            )
        ).mappings().one_or_none()
        password_hash = str(row["password_hash"]) if row else self._dummy_password_hash
        valid_password = self._passwords.verify(password, password_hash)
        if row is None or not valid_password:
            raise ApplicationError("INVALID_CREDENTIALS", "Invalid login or password", 401)

        raw_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        session_id = str(uuid4())
        now_datetime = datetime.now(UTC)
        now = now_datetime.isoformat()
        expires_at = (
            now_datetime + timedelta(seconds=self._settings.session_ttl_seconds)
        ).isoformat()
        await session.execute(
            text(
                "INSERT INTO auth_sessions "
                "(id,user_id,token_hash,csrf_token_hash,created_at,expires_at,last_seen_at) "
                "VALUES (:id,:user_id,:token_hash,:csrf_hash,:now,:expires_at,:now)"
            ),
            {
                "id": session_id,
                "user_id": row["id"],
                "token_hash": _token_hash(raw_token),
                "csrf_hash": _token_hash(csrf_token),
                "now": now,
                "expires_at": expires_at,
            },
        )
        await self._audit(
            session,
            actor_user_id=str(row["id"]),
            action="AUTH_SESSION_CREATED",
            resource_type="auth_session",
            resource_id=session_id,
            request_id=request_id,
        )
        await session.commit()
        user = AuthenticatedUser(
            str(row["id"]),
            str(row["email"]),
            str(row["role"]),
            session_id,
            _token_hash(csrf_token),
        )
        return CreatedSession(raw_token, csrf_token, user)

    async def authenticate(
        self, session: AsyncSession, raw_token: str | None
    ) -> AuthenticatedUser:
        if not raw_token:
            raise ApplicationError("AUTHENTICATION_REQUIRED", "Authentication required", 401)
        row = (
            await session.execute(
                text(
                    "SELECT u.id AS user_id,u.email,u.role,s.id AS session_id,s.csrf_token_hash "
                    "FROM auth_sessions AS s JOIN users AS u ON u.id=s.user_id "
                    "WHERE s.token_hash=:token_hash AND s.revoked_at IS NULL "
                    "AND s.expires_at > :now"
                ),
                {"token_hash": _token_hash(raw_token), "now": _utc_now()},
            )
        ).mappings().one_or_none()
        if row is None:
            raise ApplicationError("AUTHENTICATION_REQUIRED", "Authentication required", 401)
        return AuthenticatedUser(
            str(row["user_id"]),
            str(row["email"]),
            str(row["role"]),
            str(row["session_id"]),
            str(row["csrf_token_hash"]),
        )

    async def revoke_session(
        self,
        session: AsyncSession,
        *,
        user: AuthenticatedUser,
        csrf_token: str | None,
        request_id: str,
    ) -> None:
        if not csrf_token or not hmac.compare_digest(
            _token_hash(csrf_token), user.csrf_token_hash
        ):
            raise ApplicationError("CSRF_REJECTED", "CSRF validation failed", 403)
        now = _utc_now()
        await session.execute(
            text("UPDATE auth_sessions SET revoked_at=:now WHERE id=:session_id"),
            {"now": now, "session_id": user.session_id},
        )
        await self._audit(
            session,
            actor_user_id=user.user_id,
            action="AUTH_SESSION_REVOKED",
            resource_type="auth_session",
            resource_id=user.session_id,
            request_id=request_id,
        )
        await session.commit()

    @staticmethod
    async def _audit(
        session: AsyncSession,
        *,
        actor_user_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        request_id: str,
    ) -> None:
        await session.execute(
            text(
                "INSERT INTO audit_logs "
                "(id,actor_user_id,action,resource_type,resource_id,request_id,"
                "after_json,created_at) "
                "VALUES (:id,:actor,:action,:resource_type,:resource_id,:request_id,:after,:now)"
            ),
            {
                "id": str(uuid4()),
                "actor": actor_user_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "request_id": request_id,
                "after": json.dumps({"result": "success"}),
                "now": _utc_now(),
            },
        )
