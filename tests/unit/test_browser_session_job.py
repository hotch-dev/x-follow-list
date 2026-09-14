import pytest

from x_follow_list.application.scan_coordination import ResourceLease, ScanClaim
from x_follow_list.browser.contracts import BrowserSession, BrowserSessionRequest, ProviderConfig
from x_follow_list.browser.fake import FakeBrowserProvider
from x_follow_list.browser.registry import BrowserProviderRegistry
from x_follow_list.worker.browser_session import BrowserSessionJob


def session_request(claim: ScanClaim) -> BrowserSessionRequest:
    return BrowserSessionRequest(
        config=ProviderConfig(
            provider_code="FAKE",
            config_version=1,
            config={"api_url": "http://127.0.0.1:50325"},
            secret_ref="env://FAKE_PROVIDER_TOKEN",
        ),
        profile_ref="fake-profile",
        owner_task_id=claim.run_id,
    )


@pytest.mark.asyncio
async def test_browser_session_job_uses_registry_and_always_closes_session() -> None:
    provider = FakeBrowserProvider()
    registry = BrowserProviderRegistry([provider])
    claim = ScanClaim("run-1", "account-1", "worker-1", "claim-token")
    leases = (ResourceLease("x-account:account-1", 1),)
    received: BrowserSession | None = None

    async def collect(
        actual_claim: ScanClaim,
        actual_leases: tuple[ResourceLease, ...],
        session: BrowserSession,
    ) -> None:
        nonlocal received
        assert actual_claim is claim
        assert actual_leases is leases
        received = session

    job = BrowserSessionJob(registry, session_request, collect)
    await job(claim, leases)

    assert received is not None
    assert provider.detach_count == 1
    assert provider.stop_count == 1


@pytest.mark.asyncio
async def test_browser_session_job_closes_session_when_collection_fails() -> None:
    provider = FakeBrowserProvider()
    registry = BrowserProviderRegistry([provider])
    claim = ScanClaim("run-2", "account-1", "worker-1", "claim-token")

    async def fail(
        _claim: ScanClaim,
        _leases: tuple[ResourceLease, ...],
        _session: BrowserSession,
    ) -> None:
        raise RuntimeError("collection failed")

    job = BrowserSessionJob(registry, session_request, fail)
    with pytest.raises(RuntimeError, match="collection failed"):
        await job(claim, ())

    assert provider.detach_count == 1
    assert provider.stop_count == 1
