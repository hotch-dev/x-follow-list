import asyncio
from typing import cast

import pytest

from x_follow_list.browser.contracts import (
    BrowserProvider,
    BrowserSessionRequest,
    ProviderConfig,
)
from x_follow_list.browser.fake import FakeBrowserProvider

ProviderFactory = type[FakeBrowserProvider]


@pytest.fixture(params=[FakeBrowserProvider], ids=["fake"])
def provider_factory(request: pytest.FixtureRequest) -> ProviderFactory:
    return cast(ProviderFactory, request.param)


def provider_config() -> ProviderConfig:
    return ProviderConfig(
        provider_code="FAKE",
        config_version=1,
        config={"api_url": "http://127.0.0.1:50325"},
        secret_ref="env://FAKE_PROVIDER_TOKEN",
    )


def test_provider_implements_shared_protocol(provider_factory: ProviderFactory) -> None:
    provider = provider_factory()

    assert isinstance(provider, BrowserProvider)
    assert provider.code == "FAKE"
    assert provider.version
    assert provider.capabilities.persistent_profiles is True


@pytest.mark.asyncio
async def test_provider_validates_config_and_lists_profiles(
    provider_factory: ProviderFactory,
) -> None:
    provider = provider_factory()
    config = provider_config()

    report = await provider.validate_config(config)
    profiles = await provider.list_profiles(config)

    assert report.provider_code == provider.code
    assert report.provider_version == provider.version
    assert report.capabilities == provider.capabilities
    assert [profile.profile_ref for profile in profiles] == ["fake-profile"]


@pytest.mark.asyncio
async def test_provider_rejects_an_unsupported_config_version(
    provider_factory: ProviderFactory,
) -> None:
    provider = provider_factory()
    unsupported = ProviderConfig(
        provider_code="FAKE",
        config_version=999,
        config={"api_url": "http://127.0.0.1:50325"},
        secret_ref="env://FAKE_PROVIDER_TOKEN",
    )

    with pytest.raises(Exception, match="not supported"):
        await provider.validate_config(unsupported)


@pytest.mark.asyncio
async def test_acquired_session_is_healthy_and_close_is_idempotent(
    provider_factory: ProviderFactory,
) -> None:
    provider = provider_factory()
    request = BrowserSessionRequest(
        config=provider_config(), profile_ref="fake-profile", owner_task_id="run-1"
    )

    session = await provider.acquire(request)
    await session.health_check()
    await session.close()
    await session.close()

    assert session.context is provider.context
    assert session.provider_code == provider.code
    assert session.profile_key == "fake-profile"
    assert provider.detach_count == 1
    assert provider.stop_count == 1


@pytest.mark.asyncio
async def test_session_only_stops_a_browser_started_by_current_task(
    provider_factory: ProviderFactory,
) -> None:
    provider = provider_factory(profile_already_running=True)
    request = BrowserSessionRequest(
        config=provider_config(), profile_ref="fake-profile", owner_task_id="run-2"
    )

    session = await provider.acquire(request)
    await session.close()

    assert session.started_by_current_task is False
    assert provider.detach_count == 1
    assert provider.stop_count == 0


@pytest.mark.asyncio
async def test_concurrent_close_still_releases_the_session_once(
    provider_factory: ProviderFactory,
) -> None:
    provider = provider_factory()
    session = await provider.acquire(
        BrowserSessionRequest(
            config=provider_config(), profile_ref="fake-profile", owner_task_id="run-close"
        )
    )

    await asyncio.gather(session.close(), session.close(), session.close())

    assert provider.detach_count == 1
    assert provider.stop_count == 1


@pytest.mark.asyncio
async def test_profile_cannot_be_acquired_twice_until_the_session_is_released(
    provider_factory: ProviderFactory,
) -> None:
    provider = provider_factory()
    first_request = BrowserSessionRequest(
        config=provider_config(), profile_ref="fake-profile", owner_task_id="run-first"
    )
    second_request = BrowserSessionRequest(
        config=provider_config(), profile_ref="fake-profile", owner_task_id="run-second"
    )
    first_session = await provider.acquire(first_request)

    with pytest.raises(Exception, match="already acquired"):
        await provider.acquire(second_request)
    await first_session.close()
    second_session = await provider.acquire(second_request)
    await second_session.close()

    assert provider.detach_count == 2


@pytest.mark.asyncio
async def test_provider_failures_do_not_expose_sensitive_values(
    provider_factory: ProviderFactory,
) -> None:
    provider = provider_factory(fail_health_check=True)
    secret = "contract-secret-value"
    endpoint = "ws://127.0.0.1/devtools/browser/private-id"
    provider.inject_sensitive_diagnostic(secret=secret, cdp_url=endpoint)
    request = BrowserSessionRequest(
        config=provider_config(), profile_ref="fake-profile", owner_task_id="run-3"
    )
    session = await provider.acquire(request)

    with pytest.raises(Exception) as caught:
        await session.health_check()

    rendered = f"{caught.value!r} {caught.value}"
    assert secret not in rendered
    assert endpoint not in rendered
