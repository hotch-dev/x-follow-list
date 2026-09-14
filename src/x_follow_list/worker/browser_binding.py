from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from x_follow_list.application.binding import BindingClaim, BindingStateError
from x_follow_list.browser.contracts import BrowserSession, BrowserSessionRequest
from x_follow_list.browser.registry import BrowserProviderRegistry

_SAFE_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


@dataclass(frozen=True, slots=True)
class DetectedIdentity:
    x_user_id: str
    username: str | None
    display_name: str | None


class BindingLifecycle(Protocol):
    async def record_detected_identity(
        self,
        claim: BindingClaim,
        *,
        x_user_id: str,
        username: str | None,
        display_name: str | None,
    ) -> None: ...

    async def expire_claim(self, claim: BindingClaim) -> None: ...

    async def fail_claim(self, claim: BindingClaim, error_code: str) -> None: ...


BindingRequestFactory = Callable[[BindingClaim], Awaitable[BrowserSessionRequest]]
IdentityReader = Callable[[BrowserSession], Awaitable[DetectedIdentity]]


class BrowserBindingJob:
    """Run one claimed, visible, manual-login binding session."""

    def __init__(
        self,
        service: BindingLifecycle,
        registry: BrowserProviderRegistry,
        request_factory: BindingRequestFactory,
        identity_reader: IdentityReader,
        *,
        timeout_seconds: float = 15 * 60,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("binding timeout must be positive")
        self._service = service
        self._registry = registry
        self._request_factory = request_factory
        self._identity_reader = identity_reader
        self._timeout_seconds = timeout_seconds

    async def __call__(self, claim: BindingClaim) -> None:
        session: BrowserSession | None = None
        try:
            request = await self._request_factory(claim)
            if request.headless:
                raise ValueError("interactive binding requires a visible browser")
            provider = self._registry.get(request.config.provider_code)
            session = await provider.acquire(request)
            await session.health_check()
            async with asyncio.timeout(self._timeout_seconds):
                identity = await self._identity_reader(session)
            await self._service.record_detected_identity(
                claim,
                x_user_id=identity.x_user_id,
                username=identity.username,
                display_name=identity.display_name,
            )
        except TimeoutError:
            await self._finish_if_claim_active(self._service.expire_claim, claim)
        except BindingStateError:
            # Cancellation/expiry won the conditional state transition.
            pass
        except Exception as error:
            error_code = getattr(error, "code", "BROWSER_FAILED")
            if not isinstance(error_code, str) or not _SAFE_ERROR_CODE.fullmatch(error_code):
                error_code = "BROWSER_FAILED"
            await self._finish_if_claim_active(
                lambda active_claim: self._service.fail_claim(active_claim, error_code),
                claim,
            )
        finally:
            if session is not None:
                await session.close()

    @staticmethod
    async def _finish_if_claim_active(
        finish: Callable[[BindingClaim], Awaitable[None]], claim: BindingClaim
    ) -> None:
        try:
            await finish(claim)
        except BindingStateError:
            pass
