from __future__ import annotations

from sqlalchemy import text

from x_follow_list.application.binding import (
    BindingClaim,
    BrowserBindingService,
)
from x_follow_list.browser.contracts import BrowserSessionRequest
from x_follow_list.browser.registry import BrowserProviderRegistry
from x_follow_list.persistence.database import Database
from x_follow_list.worker.browser_binding import BrowserBindingJob, IdentityReader
from x_follow_list.worker.provider_execution import provider_config_from_row


class BindingConfigurationError(RuntimeError):
    code = "BINDING_CONFIGURATION_INVALID"


class ConfiguredBindingJob:
    """Load a claimed binding's durable provider configuration before execution."""

    def __init__(
        self,
        database: Database,
        service: BrowserBindingService,
        registry: BrowserProviderRegistry,
        identity_reader: IdentityReader,
    ) -> None:
        self._database = database
        self._job = BrowserBindingJob(
            service,
            registry,
            self._request_for_claim,
            identity_reader,
        )

    async def __call__(self, claim: BindingClaim) -> None:
        await self._job(claim)

    async def _request_for_claim(self, claim: BindingClaim) -> BrowserSessionRequest:
        async with self._database.session() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT b.profile_ref,p.provider_code,p.config_version,p.config_json,"
                        "p.secret_ref FROM browser_bind_sessions b "
                        "JOIN browser_provider_configs p ON p.id=b.provider_config_id "
                        "WHERE b.id=:id AND b.status='RUNNING' AND b.worker_id=:worker "
                        "AND b.claim_token=:token"
                    ),
                    {
                        "id": claim.session_id,
                        "worker": claim.worker_id,
                        "token": claim.claim_token,
                    },
                )
            ).mappings().one_or_none()
        if row is None:
            raise BindingConfigurationError("claimed binding configuration was not found")
        try:
            provider = provider_config_from_row(row)
        except (KeyError, TypeError, ValueError):
            raise BindingConfigurationError("provider configuration is invalid") from None
        return BrowserSessionRequest(
            provider, str(row["profile_ref"]), claim.session_id, headless=False
        )
