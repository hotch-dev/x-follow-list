from __future__ import annotations

import asyncio
from typing import Any

from x_follow_list.browser.contracts import (
    BrowserCapabilities,
    BrowserSessionRequest,
    CapabilityReport,
    ProfileSummary,
    ProviderConfig,
)


class ProviderOperationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ProfileAlreadyAcquiredError(ProviderOperationError):
    def __init__(self) -> None:
        super().__init__("PROFILE_ALREADY_ACQUIRED", "browser profile is already acquired")


class FakeBrowserSession:
    def __init__(
        self,
        provider: FakeBrowserProvider,
        *,
        profile_key: str,
        started_by_current_task: bool,
    ) -> None:
        self.context = provider.context
        self.provider_code = provider.code
        self.profile_key = profile_key
        self.started_by_current_task = started_by_current_task
        self._provider = provider
        self._closed = False
        self._close_lock = asyncio.Lock()

    async def health_check(self) -> None:
        if self._closed:
            raise ProviderOperationError("SESSION_CLOSED", "browser session is closed")
        if self._provider.fail_health_check:
            raise ProviderOperationError(
                "BROWSER_UNHEALTHY", "browser session failed its health check"
            )

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            await self._provider._release(self.started_by_current_task)


class FakeBrowserProvider:
    code = "FAKE"
    version = "1.0"
    capabilities = BrowserCapabilities(
        persistent_profiles=True,
        visible_login=True,
        remote_cdp=False,
        attach_existing_session=True,
    )

    def __init__(
        self,
        *,
        profile_already_running: bool = False,
        fail_health_check: bool = False,
    ) -> None:
        self.profile_already_running = profile_already_running
        self.fail_health_check = fail_health_check
        self.context: Any = object()
        self.detach_count = 0
        self.stop_count = 0
        self._acquired = False
        self._diagnostic_secret: str | None = None
        self._diagnostic_cdp_url: str | None = None

    def inject_sensitive_diagnostic(self, *, secret: str, cdp_url: str) -> None:
        self._diagnostic_secret = secret
        self._diagnostic_cdp_url = cdp_url

    async def validate_config(self, config: ProviderConfig) -> CapabilityReport:
        self._ensure_matching_config(config)
        return CapabilityReport(self.code, self.version, self.capabilities)

    async def list_profiles(self, config: ProviderConfig) -> list[ProfileSummary]:
        self._ensure_matching_config(config)
        return [ProfileSummary("fake-profile", "Fake profile", self.profile_already_running)]

    async def acquire(self, request: BrowserSessionRequest) -> FakeBrowserSession:
        self._ensure_matching_config(request.config)
        if request.profile_ref != "fake-profile":
            raise ProviderOperationError("PROFILE_NOT_FOUND", "browser profile was not found")
        if self._acquired:
            raise ProfileAlreadyAcquiredError()
        self._acquired = True
        return FakeBrowserSession(
            self,
            profile_key=request.profile_ref,
            started_by_current_task=not self.profile_already_running,
        )

    async def _release(self, started_by_current_task: bool) -> None:
        self.detach_count += 1
        if started_by_current_task:
            self.stop_count += 1
        self._acquired = False

    def _ensure_matching_config(self, config: ProviderConfig) -> None:
        if config.provider_code != self.code:
            raise ProviderOperationError(
                "PROVIDER_CONFIG_MISMATCH", "provider configuration does not match adapter"
            )
        if config.config_version != 1:
            raise ProviderOperationError(
                "UNSUPPORTED_CONFIG_VERSION", "provider config version is not supported"
            )
