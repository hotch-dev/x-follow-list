from __future__ import annotations

from collections.abc import Awaitable, Callable

from x_follow_list.application.scan_coordination import ResourceLease, ScanClaim
from x_follow_list.browser.contracts import BrowserSession, BrowserSessionRequest
from x_follow_list.browser.registry import BrowserProviderRegistry

BrowserRequestFactory = Callable[[ScanClaim], BrowserSessionRequest]
BrowserWork = Callable[
    [ScanClaim, tuple[ResourceLease, ...], BrowserSession], Awaitable[None]
]


class BrowserSessionJob:
    """Adapt provider-independent browser work to the durable scan worker handler."""

    def __init__(
        self,
        registry: BrowserProviderRegistry,
        request_factory: BrowserRequestFactory,
        work: BrowserWork,
    ) -> None:
        self._registry = registry
        self._request_factory = request_factory
        self._work = work

    async def __call__(
        self, claim: ScanClaim, leases: tuple[ResourceLease, ...]
    ) -> None:
        request = self._request_factory(claim)
        provider = self._registry.get(request.config.provider_code)
        session = await provider.acquire(request)
        try:
            await session.health_check()
            await self._work(claim, leases, session)
        finally:
            await session.close()
