from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from x_follow_list.application.errors import ResourceNotFoundError


class OwnedResourceRepository:
    """Resolve resources from membership, hiding both missing and foreign IDs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def require_x_account(self, user_id: str, account_id: str) -> Mapping[str, Any]:
        return await self._require(
            "SELECT a.* FROM x_account_memberships AS m "
            "JOIN x_accounts AS a ON a.id=m.x_account_id "
            "WHERE m.user_id=:user_id AND a.id=:resource_id",
            user_id,
            account_id,
        )

    async def require_scan_run(self, user_id: str, scan_run_id: str) -> Mapping[str, Any]:
        return await self._require(
            "SELECT r.* FROM x_account_memberships AS m "
            "JOIN scan_runs AS r ON r.x_account_id=m.x_account_id "
            "WHERE m.user_id=:user_id AND r.id=:resource_id",
            user_id,
            scan_run_id,
        )

    async def require_artifact(self, user_id: str, artifact_id: str) -> Mapping[str, Any]:
        return await self._require(
            "SELECT f.* FROM x_account_memberships AS m "
            "JOIN scan_runs AS r ON r.x_account_id=m.x_account_id "
            "JOIN artifacts AS f ON f.scan_run_id=r.id "
            "WHERE m.user_id=:user_id AND f.id=:resource_id",
            user_id,
            artifact_id,
        )

    async def _require(
        self, query: str, user_id: str, resource_id: str
    ) -> Mapping[str, Any]:
        row = (
            await self._session.execute(
                text(query), {"user_id": user_id, "resource_id": resource_id}
            )
        ).mappings().one_or_none()
        if row is None:
            raise ResourceNotFoundError
        return dict(row)
