from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from x_follow_list.application.scan_coordination import ResourceLease, ScanClaim
from x_follow_list.browser.contracts import BrowserSessionRequest, ProviderConfig
from x_follow_list.browser.registry import BrowserProviderRegistry
from x_follow_list.persistence.database import Database
from x_follow_list.worker.browser_session import BrowserSessionJob, BrowserWork

_X_USERNAME = re.compile(r"^[A-Za-z0-9_]{1,64}$")
ScanWorkFactory = Callable[[str], BrowserWork]
ProfileUrlFactory = Callable[[str], str]


class ScanConfigurationError(RuntimeError):
    code = "SCAN_CONFIGURATION_INVALID"


@dataclass(frozen=True, slots=True)
class _ExecutionConfiguration:
    provider: ProviderConfig
    profile_ref: str
    username: str


def x_profile_url(username: str) -> str:
    if not _X_USERNAME.fullmatch(username):
        raise ScanConfigurationError("bound account username is invalid")
    return f"https://x.com/{username}"


class ConfiguredScanJob:
    """Resolve durable account configuration before opening a browser session."""

    def __init__(
        self,
        database: Database,
        registry: BrowserProviderRegistry,
        work_factory: ScanWorkFactory,
        *,
        profile_url_factory: ProfileUrlFactory = x_profile_url,
        headless: bool = False,
    ) -> None:
        self._database = database
        self._registry = registry
        self._work_factory = work_factory
        self._profile_url_factory = profile_url_factory
        self._headless = headless

    async def __call__(
        self, claim: ScanClaim, leases: tuple[ResourceLease, ...]
    ) -> None:
        execution = await self._load_execution_configuration(claim)
        request = BrowserSessionRequest(
            execution.provider,
            execution.profile_ref,
            claim.run_id,
            headless=self._headless,
        )
        work = self._work_factory(self._profile_url_factory(execution.username))
        await BrowserSessionJob(self._registry, lambda _claim: request, work)(
            claim, leases
        )

    async def _load_execution_configuration(
        self, claim: ScanClaim
    ) -> _ExecutionConfiguration:
        async with self._database.session() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT a.profile_ref,a.username,p.provider_code,p.config_version,"
                        "p.config_json,p.secret_ref FROM x_accounts a "
                        "JOIN browser_provider_configs p ON p.id=a.provider_config_id "
                        "WHERE a.id=:account AND a.status='READY'"
                    ),
                    {"account": claim.x_account_id},
                )
            ).mappings().one_or_none()
        if row is None or row["username"] is None:
            raise ScanConfigurationError("ready account configuration was not found")
        raw_config: Any = row["config_json"]
        if isinstance(raw_config, str):
            try:
                raw_config = json.loads(raw_config)
            except json.JSONDecodeError:
                raise ScanConfigurationError("provider configuration is invalid") from None
        if not isinstance(raw_config, dict):
            raise ScanConfigurationError("provider configuration is invalid")
        try:
            provider = ProviderConfig(
                str(row["provider_code"]),
                int(row["config_version"]),
                raw_config,
                secret_ref=row["secret_ref"],
            )
            self._registry.get(provider.provider_code)
        except (TypeError, ValueError, LookupError):
            raise ScanConfigurationError("provider configuration is invalid") from None
        return _ExecutionConfiguration(
            provider, str(row["profile_ref"]), str(row["username"])
        )
