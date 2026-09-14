from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from x_follow_list.application.errors import ApplicationError, ResourceNotFoundError
from x_follow_list.browser.contracts import (
    BrowserProvider,
    CapabilityReport,
    ProfileSummary,
    ProviderConfig,
)
from x_follow_list.browser.registry import (
    BrowserProviderRegistry,
    ProviderDescriptor,
    ProviderNotFoundError,
)
from x_follow_list.persistence.database import Database


class ProviderConfigService:
    def __init__(self, database: Database, registry: BrowserProviderRegistry) -> None:
        self._database = database
        self._registry = registry

    def descriptors(self) -> tuple[ProviderDescriptor, ...]:
        return self._registry.descriptors()

    async def create(
        self,
        user_id: str,
        *,
        provider_code: str,
        display_name: str,
        config_version: int,
        config: dict[str, object],
        secret_ref: str | None,
    ) -> dict[str, Any]:
        try:
            self._registry.get(provider_code)
            validated = ProviderConfig(
                provider_code, config_version, config, secret_ref=secret_ref
            )
        except (ProviderNotFoundError, TypeError, ValueError):
            raise ApplicationError(
                "PROVIDER_CONFIG_INVALID", "Browser provider configuration is invalid", 422
            ) from None
        config_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        async with self._database.session() as session:
            await session.execute(
                text(
                    "INSERT INTO browser_provider_configs "
                    "(id,owner_user_id,provider_code,config_version,display_name,config_json,"
                    "secret_ref,created_at,updated_at) VALUES "
                    "(:id,:owner,:code,:version,:name,:config,:secret,:now,:now)"
                ),
                {
                    "id": config_id,
                    "owner": user_id,
                    "code": validated.provider_code,
                    "version": validated.config_version,
                    "name": display_name,
                    "config": json.dumps(dict(validated.config)),
                    "secret": validated.secret_ref,
                    "now": now,
                },
            )
            await session.commit()
        return await self.get(user_id, config_id)

    async def list_configs(self, user_id: str) -> list[dict[str, Any]]:
        async with self._database.session() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT id,provider_code,config_version,display_name,"
                        "secret_ref IS NOT NULL AS has_secret,created_at,updated_at "
                        "FROM browser_provider_configs WHERE owner_user_id=:owner "
                        "ORDER BY created_at,id"
                    ),
                    {"owner": user_id},
                )
            ).mappings().all()
        return [dict(row) for row in rows]

    async def get(self, user_id: str, config_id: str) -> dict[str, Any]:
        async with self._database.session() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT id,provider_code,config_version,display_name,"
                        "secret_ref IS NOT NULL AS has_secret,created_at,updated_at "
                        "FROM browser_provider_configs "
                        "WHERE owner_user_id=:owner AND id=:id"
                    ),
                    {"owner": user_id, "id": config_id},
                )
            ).mappings().one_or_none()
        if row is None:
            raise ResourceNotFoundError
        return dict(row)

    async def validate(self, user_id: str, config_id: str) -> CapabilityReport:
        provider, config = await self._provider_and_config(user_id, config_id)
        try:
            return await provider.validate_config(config)
        except Exception:
            raise ApplicationError(
                "PROVIDER_VALIDATION_FAILED", "Browser provider validation failed", 422
            ) from None

    async def profiles(self, user_id: str, config_id: str) -> list[ProfileSummary]:
        provider, config = await self._provider_and_config(user_id, config_id)
        try:
            return list(await provider.list_profiles(config))
        except Exception:
            raise ApplicationError(
                "PROVIDER_PROFILES_UNAVAILABLE", "Browser provider profiles are unavailable", 502
            ) from None

    async def _provider_and_config(
        self, user_id: str, config_id: str
    ) -> tuple[BrowserProvider, ProviderConfig]:
        async with self._database.session() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT provider_code,config_version,config_json,secret_ref "
                        "FROM browser_provider_configs "
                        "WHERE owner_user_id=:owner AND id=:id"
                    ),
                    {"owner": user_id, "id": config_id},
                )
            ).mappings().one_or_none()
        if row is None:
            raise ResourceNotFoundError
        raw_config = row["config_json"]
        if isinstance(raw_config, str):
            raw_config = json.loads(raw_config)
        try:
            provider = self._registry.get(str(row["provider_code"]))
            config = ProviderConfig(
                str(row["provider_code"]),
                int(row["config_version"]),
                raw_config,
                secret_ref=row["secret_ref"],
            )
        except (ProviderNotFoundError, TypeError, ValueError):
            raise ApplicationError(
                "PROVIDER_CONFIG_INVALID", "Browser provider configuration is invalid", 422
            ) from None
        return provider, config
