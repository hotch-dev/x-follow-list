import asyncio

import pytest

from x_follow_list.application.binding import BindingClaim
from x_follow_list.browser.contracts import BrowserSession, BrowserSessionRequest, ProviderConfig
from x_follow_list.browser.fake import FakeBrowserProvider
from x_follow_list.browser.registry import BrowserProviderRegistry
from x_follow_list.worker.browser_binding import BrowserBindingJob, DetectedIdentity


class RecordingBindingService:
    def __init__(self) -> None:
        self.detected: tuple[BindingClaim, DetectedIdentity] | None = None
        self.expired: BindingClaim | None = None
        self.failed: tuple[BindingClaim, str] | None = None

    async def record_detected_identity(
        self,
        claim: BindingClaim,
        *,
        x_user_id: str,
        username: str | None,
        display_name: str | None,
    ) -> None:
        self.detected = (
            claim,
            DetectedIdentity(x_user_id, username, display_name),
        )

    async def expire_claim(self, claim: BindingClaim) -> None:
        self.expired = claim

    async def fail_claim(self, claim: BindingClaim, error_code: str) -> None:
        self.failed = (claim, error_code)


async def visible_request(claim: BindingClaim) -> BrowserSessionRequest:
    return BrowserSessionRequest(
        ProviderConfig("FAKE", 1, {}),
        "fake-profile",
        claim.session_id,
    )


@pytest.mark.asyncio
async def test_binding_job_waits_for_identity_and_always_closes_visible_session() -> None:
    provider = FakeBrowserProvider()
    service = RecordingBindingService()
    claim = BindingClaim("bind-1", "worker", "claim-token")
    observed_request: BrowserSessionRequest | None = None

    async def request_factory(actual_claim: BindingClaim) -> BrowserSessionRequest:
        nonlocal observed_request
        observed_request = await visible_request(actual_claim)
        return observed_request

    async def identity_reader(session: BrowserSession) -> DetectedIdentity:
        assert session.context is provider.context
        return DetectedIdentity("x-123", "alice", "Alice")

    job = BrowserBindingJob(
        service,
        BrowserProviderRegistry([provider]),
        request_factory,
        identity_reader,
    )
    await job(claim)

    assert observed_request is not None
    assert observed_request.headless is False
    assert service.detected == (claim, DetectedIdentity("x-123", "alice", "Alice"))
    assert provider.stop_count == 1
    assert provider.detach_count == 1


@pytest.mark.asyncio
async def test_binding_job_timeout_expires_claim_and_preserves_provider_profile() -> None:
    provider = FakeBrowserProvider()
    service = RecordingBindingService()
    claim = BindingClaim("bind-2", "worker", "claim-token")

    async def never_detect(_session: BrowserSession) -> DetectedIdentity:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    job = BrowserBindingJob(
        service,
        BrowserProviderRegistry([provider]),
        visible_request,
        never_detect,
        timeout_seconds=0.01,
    )
    await job(claim)

    assert service.expired is claim
    assert service.detected is None
    assert provider.stop_count == 1
    assert provider.detach_count == 1


@pytest.mark.asyncio
async def test_binding_job_records_sanitized_provider_failure_and_releases() -> None:
    provider = FakeBrowserProvider(fail_health_check=True)
    service = RecordingBindingService()
    claim = BindingClaim("bind-3", "worker", "claim-token")

    async def identity_reader(_session: BrowserSession) -> DetectedIdentity:
        raise AssertionError("identity reader must not run after a failed health check")

    job = BrowserBindingJob(
        service,
        BrowserProviderRegistry([provider]),
        visible_request,
        identity_reader,
    )
    await job(claim)

    assert service.failed == (claim, "BROWSER_UNHEALTHY")
    assert provider.stop_count == 1
    assert provider.detach_count == 1
