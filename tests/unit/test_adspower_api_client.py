from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from x_follow_list.browser.adspower import (
    AdsPowerApiClient,
    AdsPowerProvider,
    AdsPowerProviderError,
)
from x_follow_list.browser.contracts import BrowserSessionRequest, ProviderConfig


def provider_config(**overrides: object) -> ProviderConfig:
    values: dict[str, object] = {
        "api_url": "http://127.0.0.1:50325",
        "api_version": "v2",
        "provider_version": "3.4.1",
        "request_timeout_seconds": 1,
        "retry_attempts": 2,
        "max_response_bytes": 4096,
    }
    values.update(overrides)
    return ProviderConfig("ADSPOWER", 1, values, "env://ADSPOWER_API_TOKEN")


def response(payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(payload).encode())


@pytest.mark.asyncio
async def test_v2_client_parses_only_safe_profile_fields_and_full_lifecycle() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/list"):
            return response(
                {
                    "code": 0,
                    "data": {
                        "list": [
                            {
                                "profile_id": "profile-1",
                                "name": "Existing",
                                "password": "must-not-propagate",
                                "user_proxy_config": {"password": "also-private"},
                            }
                        ]
                    },
                }
            )
        if path.endswith("/active"):
            return response(
                {
                    "code": 0,
                    "data": {
                        "status": "Active",
                        "ws": {"puppeteer": "ws://127.0.0.1:9222/devtools/browser/id"},
                    },
                }
            )
        if path.endswith("/start"):
            return response(
                {
                    "code": 0,
                    "data": {
                        "ws": {"puppeteer": "ws://127.0.0.1:9222/devtools/browser/id"}
                    },
                }
            )
        return response({"code": 0, "msg": "success"})

    client = AdsPowerApiClient(
        provider_config(),
        "token-value",
        transport=httpx.MockTransport(handler),
    )

    await client.check_status()
    profiles = await client.list_profiles()
    running, endpoint = await client.active("profile-1")
    started_endpoint = await client.start("profile-1", headless=True)
    await client.stop("profile-1")

    assert profiles == [{"profile_id": "profile-1", "name": "Existing"}]
    assert (running, endpoint) == (
        True,
        "ws://127.0.0.1:9222/devtools/browser/id",
    )
    assert started_endpoint == endpoint
    assert [request.url.path for request in requests] == [
        "/status",
        "/api/v2/browser-profile/list",
        "/api/v2/browser-profile/active",
        "/api/v2/browser-profile/start",
        "/api/v2/browser-profile/stop",
    ]
    assert all(request.headers["authorization"] == "Bearer token-value" for request in requests)
    assert json.loads(requests[3].content) == {"profile_id": "profile-1", "headless": "1"}
    assert json.loads(requests[4].content) == {"profile_id": "profile-1"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "operation", "expected_code"),
    [
        ({"code": 0, "data": {}}, "list", "PROVIDER_RESPONSE_INVALID"),
        ({"code": 0, "data": {"status": "Unknown"}}, "active", "PROVIDER_RESPONSE_INVALID"),
        ({"code": 0, "data": {}}, "start", "PROVIDER_RESPONSE_INVALID"),
        ({"code": -1, "msg": "secret diagnostic"}, "status", "PROVIDER_REQUEST_FAILED"),
    ],
)
async def test_v2_client_fails_closed_for_invalid_provider_payloads(
    payload: object,
    operation: str,
    expected_code: str,
) -> None:
    client = AdsPowerApiClient(
        provider_config(),
        "token-value",
        transport=httpx.MockTransport(lambda _request: response(payload)),
    )

    with pytest.raises(AdsPowerProviderError) as caught:
        if operation == "list":
            await client.list_profiles()
        elif operation == "active":
            await client.active("profile-1")
        elif operation == "start":
            await client.start("profile-1", headless=False)
        else:
            await client.check_status()

    assert caught.value.code == expected_code
    assert "secret diagnostic" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "expected_code"),
    [
        (lambda _request: httpx.Response(500), "PROVIDER_REQUEST_FAILED"),
        (lambda _request: httpx.Response(200, content=b"not-json"), "PROVIDER_RESPONSE_INVALID"),
        (
            lambda _request: httpx.Response(
                200, content=b"{}", headers={"content-length": "invalid"}
            ),
            "PROVIDER_RESPONSE_INVALID",
        ),
    ],
)
async def test_v2_client_maps_http_and_encoding_failures(
    handler: Callable[[httpx.Request], httpx.Response], expected_code: str
) -> None:
    client = AdsPowerApiClient(
        provider_config(), "token-value", transport=httpx.MockTransport(handler)
    )

    with pytest.raises(AdsPowerProviderError) as caught:
        await client.check_status()

    assert caught.value.code == expected_code


@pytest.mark.asyncio
async def test_transport_failure_retries_to_the_configured_limit() -> None:
    attempts = 0

    def fail(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("private endpoint and token", request=request)

    client = AdsPowerApiClient(
        provider_config(retry_attempts=3),
        "token-value",
        transport=httpx.MockTransport(fail),
        retry_sleeper=lambda _delay: asyncio.sleep(0),
    )

    with pytest.raises(AdsPowerProviderError) as caught:
        await client.check_status()

    assert caught.value.code == "PROVIDER_UNAVAILABLE"
    assert attempts == 3
    assert "private endpoint" not in str(caught.value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"request_timeout_seconds": 0},
        {"request_timeout_seconds": True},
        {"retry_attempts": 0},
        {"retry_attempts": True},
        {"max_response_bytes": 100},
        {"max_response_bytes": True},
        {"provider_version": "not-semver"},
        {"api_url": "http://127.0.0.1:50325/uncontrolled-prefix"},
    ],
)
@pytest.mark.asyncio
async def test_invalid_operational_limits_fail_before_network_access(
    overrides: dict[str, object],
) -> None:
    provider = AdsPowerProvider(
        secret_resolver=lambda _reference: "token-value",
        http_transport=httpx.MockTransport(
            lambda _request: pytest.fail("network must not be reached")
        ),
    )

    with pytest.raises(AdsPowerProviderError) as caught:
        await provider.validate_config(provider_config(**overrides))

    assert caught.value.code == "INVALID_PROVIDER_CONFIG"


@pytest.mark.asyncio
async def test_default_env_secret_resolution_and_missing_profile_are_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADSPOWER_API_TOKEN", "resolved-token")
    seen_token: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_token.append(request.headers["authorization"])
        if request.url.path.endswith("/list"):
            return response({"code": 0, "data": {"list": []}})
        return response({"code": 0})

    provider = AdsPowerProvider(http_transport=httpx.MockTransport(handler))
    await provider.validate_config(provider_config())

    with pytest.raises(AdsPowerProviderError) as caught:
        await provider.acquire(
            BrowserSessionRequest(provider_config(), "missing-profile", "scan-missing")
        )

    assert caught.value.code == "PROFILE_NOT_FOUND"
    assert seen_token == ["Bearer resolved-token", "Bearer resolved-token"]
