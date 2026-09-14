from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from x_follow_list.browser.adspower import (
    AdsPowerProvider,
    AdsPowerProviderError,
)
from x_follow_list.browser.contracts import (
    BrowserProvider,
    BrowserSessionRequest,
    ProviderConfig,
)


TOKEN = "super-secret-adspower-token"
CDP_URL = "ws://127.0.0.1:49200/devtools/browser/private-browser-id"


def config(**overrides: object) -> ProviderConfig:
    values: dict[str, object] = {
        "api_url": "http://127.0.0.1:50325",
        "api_version": "v2",
        "provider_version": "3.4.1",
        "request_timeout_seconds": 2.0,
        "retry_attempts": 2,
        "max_response_bytes": 4096,
    }
    values.update(overrides)
    return ProviderConfig(
        "ADSPOWER", 1, values, secret_ref="env://ADSPOWER_API_TOKEN"
    )


class FakePage:
    def __init__(self, *, fail_health: bool = False) -> None:
        self.fail_health = fail_health
        self.evaluations: list[str] = []

    async def evaluate(self, expression: str) -> int:
        self.evaluations.append(expression)
        if self.fail_health:
            raise RuntimeError(f"CDP disconnected at {CDP_URL}?token={TOKEN}")
        return 1


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.pages = [page]


class FakeBrowser:
    def __init__(self, *, fail_health: bool = False, connected: bool = True) -> None:
        self.contexts = [FakeContext(FakePage(fail_health=fail_health))]
        self.version = "Chrome/134.0.0.0"
        self.connected = connected
        self.close_count = 0

    def is_connected(self) -> bool:
        return self.connected

    async def close(self) -> None:
        self.close_count += 1


class FakeChromium:
    def __init__(self, browser: FakeBrowser, *, fail_connect: bool = False) -> None:
        self.browser = browser
        self.fail_connect = fail_connect
        self.endpoints: list[str] = []

    async def connect_over_cdp(self, endpoint: str) -> FakeBrowser:
        self.endpoints.append(endpoint)
        if self.fail_connect:
            raise RuntimeError(f"connect failed {endpoint}?token={TOKEN}")
        return self.browser


class FakeRuntime:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium
        self.stop_count = 0

    async def stop(self) -> None:
        self.stop_count += 1


class FakeStarter:
    def __init__(self, runtime: FakeRuntime) -> None:
        self.runtime = runtime

    async def start(self) -> FakeRuntime:
        return self.runtime


class FakeAdsPowerApi:
    def __init__(self, *, already_running: bool = False, cdp_url: str = CDP_URL) -> None:
        self.already_running = already_running
        self.cdp_url = cdp_url
        self.calls: list[tuple[str, str]] = []
        self.stop_count = 0

    async def check_status(self) -> None:
        self.calls.append(("GET", "/status"))

    async def list_profiles(self) -> list[dict[str, object]]:
        self.calls.append(("POST", "/api/v2/browser-profile/list"))
        return [
            {"profile_id": "existing-1", "name": "Existing one"},
            {"profile_id": "existing-2", "name": "Existing two"},
        ]

    async def active(self, profile_ref: str) -> tuple[bool, str | None]:
        self.calls.append(("GET", "/api/v2/browser-profile/active"))
        return self.already_running, self.cdp_url if self.already_running else None

    async def start(self, profile_ref: str, *, headless: bool) -> str:
        self.calls.append(("POST", "/api/v2/browser-profile/start"))
        return self.cdp_url

    async def stop(self, profile_ref: str) -> None:
        self.calls.append(("POST", "/api/v2/browser-profile/stop"))
        self.stop_count += 1


def provider_with_fakes(
    *,
    already_running: bool = False,
    cdp_url: str = CDP_URL,
    fail_connect: bool = False,
    fail_health: bool = False,
) -> tuple[AdsPowerProvider, FakeAdsPowerApi, FakeBrowser, FakeRuntime]:
    api = FakeAdsPowerApi(already_running=already_running, cdp_url=cdp_url)
    browser = FakeBrowser(fail_health=fail_health)
    runtime = FakeRuntime(FakeChromium(browser, fail_connect=fail_connect))
    provider = AdsPowerProvider(
        secret_resolver=lambda _reference: TOKEN,
        api_factory=lambda _config, _token: api,
        playwright_starter=lambda: FakeStarter(runtime),
    )
    return provider, api, browser, runtime


def test_adspower_implements_shared_contract_and_declares_remote_cdp() -> None:
    provider, _api, _browser, _runtime = provider_with_fakes()

    assert isinstance(provider, BrowserProvider)
    assert provider.code == "ADSPOWER"
    assert provider.version == "1.0"
    assert provider.capabilities.persistent_profiles is True
    assert provider.capabilities.remote_cdp is True
    assert provider.capabilities.attach_existing_session is True


@pytest.mark.asyncio
async def test_validate_config_checks_version_secret_reference_and_connectivity() -> None:
    provider, api, _browser, _runtime = provider_with_fakes()

    report = await provider.validate_config(config())

    assert report.provider_code == "ADSPOWER"
    assert report.provider_version == "1.0"
    assert api.calls == [("GET", "/status")]

    for invalid in (
        config(api_version="v1"),
        config(provider_version="3.4.0"),
        ProviderConfig("ADSPOWER", 1, dict(config().config), secret_ref=None),
    ):
        with pytest.raises(AdsPowerProviderError) as caught:
            await provider.validate_config(invalid)
        assert caught.value.code == "INVALID_PROVIDER_CONFIG"


@pytest.mark.asyncio
async def test_lists_only_existing_profiles_without_mutating_them() -> None:
    provider, api, _browser, _runtime = provider_with_fakes(already_running=True)

    profiles = await provider.list_profiles(config())

    assert [(item.profile_ref, item.display_name, item.is_running) for item in profiles] == [
        ("existing-1", "Existing one", True),
        ("existing-2", "Existing two", True),
    ]
    assert all("create" not in path and "update" not in path and "delete" not in path for _, path in api.calls)


@pytest.mark.asyncio
async def test_lock_is_taken_before_status_check_and_owned_profile_is_stopped() -> None:
    provider, api, browser, runtime = provider_with_fakes()
    request = BrowserSessionRequest(config(), "existing-1", "scan-1", headless=True)

    first = await provider.acquire(request)
    with pytest.raises(AdsPowerProviderError) as caught:
        await provider.acquire(request)
    assert caught.value.code == "PROFILE_ALREADY_ACQUIRED"
    assert [path for _, path in api.calls].count("/api/v2/browser-profile/active") == 1

    await first.health_check()
    await asyncio.gather(first.close(), first.close())

    assert first.started_by_current_task is True
    assert first.provider_version == "3.4.1"
    assert first.browser_version == "Chrome/134.0.0.0"
    assert browser.close_count == 1
    assert runtime.stop_count == 1
    assert api.stop_count == 1


@pytest.mark.asyncio
async def test_already_running_profile_is_only_detached() -> None:
    provider, api, browser, runtime = provider_with_fakes(already_running=True)

    session = await provider.acquire(
        BrowserSessionRequest(config(), "existing-1", "bind-1")
    )
    await session.close()

    assert session.started_by_current_task is False
    assert browser.close_count == 1
    assert runtime.stop_count == 1
    assert api.stop_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [
        "ws://8.8.8.8:9222/devtools/browser/stolen",
        "file:///private/browser",
        "ws://user:password@127.0.0.1:9222/devtools/browser/id",
        "ws://127.0.0.1:9222/devtools/browser/id#fragment",
    ],
)
async def test_rejects_untrusted_cdp_endpoints_and_stops_owned_profile(endpoint: str) -> None:
    provider, api, _browser, _runtime = provider_with_fakes(cdp_url=endpoint)

    with pytest.raises(AdsPowerProviderError) as caught:
        await provider.acquire(BrowserSessionRequest(config(), "existing-1", "scan-bad"))

    assert caught.value.code == "UNSAFE_CDP_ENDPOINT"
    assert endpoint not in f"{caught.value!r} {caught.value}"
    assert api.stop_count == 1


@pytest.mark.asyncio
async def test_connect_failure_is_sanitized_stops_owned_profile_and_releases_lock() -> None:
    provider, api, _browser, _runtime = provider_with_fakes(fail_connect=True)
    request = BrowserSessionRequest(config(), "existing-1", "scan-connect")

    with pytest.raises(AdsPowerProviderError) as caught:
        await provider.acquire(request)
    provider._playwright_starter = lambda: FakeStarter(  # type: ignore[attr-defined]
        FakeRuntime(FakeChromium(FakeBrowser()))
    )
    session = await provider.acquire(request)
    await session.close()

    rendered = f"{caught.value!r} {caught.value}"
    assert caught.value.code == "CDP_CONNECT_FAILED"
    assert TOKEN not in rendered
    assert CDP_URL not in rendered
    assert api.stop_count == 2


@pytest.mark.asyncio
async def test_health_check_maps_disconnect_without_sensitive_diagnostics() -> None:
    provider, _api, _browser, _runtime = provider_with_fakes(
        already_running=True, fail_health=True
    )
    session = await provider.acquire(
        BrowserSessionRequest(config(), "existing-1", "scan-health")
    )

    with pytest.raises(AdsPowerProviderError) as caught:
        await session.health_check()
    await session.close()

    rendered = f"{caught.value!r} {caught.value}"
    assert caught.value.code == "BROWSER_UNHEALTHY"
    assert TOKEN not in rendered
    assert CDP_URL not in rendered


def json_response(payload: object, *, status: int = 200, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(payload).encode(), headers=headers)


@pytest.mark.asyncio
async def test_controlled_api_uses_bearer_token_and_retries_only_transient_failures() -> None:
    attempts = 0
    seen_authorization: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        seen_authorization.append(request.headers.get("authorization"))
        if attempts == 1:
            return json_response({"code": -1, "msg": f"temporary {TOKEN}"}, status=503)
        return json_response({"code": 0, "msg": "success"})

    provider = AdsPowerProvider(
        secret_resolver=lambda _reference: TOKEN,
        http_transport=httpx.MockTransport(handler),
        retry_sleeper=lambda _delay: asyncio.sleep(0),
    )

    await provider.validate_config(config())

    assert attempts == 2
    assert seen_authorization == [f"Bearer {TOKEN}", f"Bearer {TOKEN}"]


@pytest.mark.asyncio
async def test_invalid_token_is_not_retried_and_error_is_redacted() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return json_response({"code": -1, "msg": f"invalid {TOKEN}"}, status=401)

    provider = AdsPowerProvider(
        secret_resolver=lambda _reference: TOKEN,
        http_transport=httpx.MockTransport(handler),
    )

    with pytest.raises(AdsPowerProviderError) as caught:
        await provider.validate_config(config())

    assert caught.value.code == "PROVIDER_AUTH_FAILED"
    assert attempts == 1
    assert TOKEN not in f"{caught.value!r} {caught.value}"


@pytest.mark.asyncio
async def test_redirect_and_oversized_response_are_rejected_without_following() -> None:
    requested: list[str] = []

    def redirect_handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://8.8.8.8/steal"})

    redirecting = AdsPowerProvider(
        secret_resolver=lambda _reference: TOKEN,
        http_transport=httpx.MockTransport(redirect_handler),
    )
    with pytest.raises(AdsPowerProviderError) as redirect_error:
        await redirecting.validate_config(config())
    assert redirect_error.value.code == "PROVIDER_REDIRECT_REJECTED"
    assert requested == ["http://127.0.0.1:50325/status"]

    oversized = AdsPowerProvider(
        secret_resolver=lambda _reference: TOKEN,
        http_transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                content=b"x" * 5000,
                headers={"content-length": "5000"},
            )
        ),
    )
    with pytest.raises(AdsPowerProviderError) as size_error:
        await oversized.validate_config(config(max_response_bytes=1024))
    assert size_error.value.code == "PROVIDER_RESPONSE_TOO_LARGE"

