from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import socket
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

import httpx
from playwright.async_api import async_playwright

from x_follow_list.browser.contracts import (
    BrowserCapabilities,
    BrowserSessionRequest,
    CapabilityReport,
    ProfileSummary,
    ProviderConfig,
)

_PROFILE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,119}$")
_MIN_ADSPOWER_VERSION = (3, 4, 1)
_TRANSIENT_STATUS_CODES = frozenset({408, 425, 429, 502, 503, 504})


class AdsPowerProviderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _AdsPowerApi(Protocol):
    async def check_status(self) -> None: ...

    async def list_profiles(self) -> list[dict[str, object]]: ...

    async def active(self, profile_ref: str) -> tuple[bool, str | None]: ...

    async def start(self, profile_ref: str, *, headless: bool) -> str: ...

    async def stop(self, profile_ref: str) -> None: ...


SecretResolver = Callable[[str], str | Awaitable[str]]
ApiFactory = Callable[[ProviderConfig, str], _AdsPowerApi]
RetrySleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _Settings:
    api_url: str
    provider_version: str
    timeout: float
    attempts: int
    max_response_bytes: int


def _parse_version(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", value)
    if match is None:
        raise AdsPowerProviderError(
            "INVALID_PROVIDER_CONFIG", "AdsPower provider config is invalid"
        )
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _settings(config: ProviderConfig) -> _Settings:
    if config.provider_code != "ADSPOWER" or config.config_version != 1:
        raise AdsPowerProviderError(
            "INVALID_PROVIDER_CONFIG", "AdsPower provider config is invalid"
        )
    if config.secret_ref is None:
        raise AdsPowerProviderError(
            "INVALID_PROVIDER_CONFIG", "AdsPower secret reference is required"
        )
    values = config.config
    api_url = values.get("api_url")
    api_version = values.get("api_version")
    provider_version = values.get("provider_version")
    timeout = values.get("request_timeout_seconds", 5.0)
    attempts = values.get("retry_attempts", 2)
    max_bytes = values.get("max_response_bytes", 1_048_576)
    if (
        not isinstance(api_url, str)
        or api_version != "v2"
        or not isinstance(provider_version, str)
        or isinstance(timeout, bool)
        or not isinstance(timeout, int | float)
        or not 0.1 <= float(timeout) <= 30.0
        or isinstance(attempts, bool)
        or not isinstance(attempts, int)
        or not 1 <= attempts <= 3
        or isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or not 1_024 <= max_bytes <= 4_194_304
    ):
        raise AdsPowerProviderError(
            "INVALID_PROVIDER_CONFIG", "AdsPower provider config is invalid"
        )
    if _parse_version(provider_version) < _MIN_ADSPOWER_VERSION:
        raise AdsPowerProviderError(
            "INVALID_PROVIDER_CONFIG", "AdsPower provider version is not supported"
        )
    return _Settings(
        api_url=api_url.rstrip("/"),
        provider_version=provider_version,
        timeout=float(timeout),
        attempts=attempts,
        max_response_bytes=max_bytes,
    )


def _default_secret_resolver(reference: str) -> str:
    parsed = urlsplit(reference)
    if parsed.scheme != "env":
        raise AdsPowerProviderError(
            "SECRET_UNAVAILABLE", "AdsPower provider credential is unavailable"
        )
    name = f"{parsed.netloc}{parsed.path}"
    value = os.environ.get(name)
    if not value:
        raise AdsPowerProviderError(
            "SECRET_UNAVAILABLE", "AdsPower provider credential is unavailable"
        )
    return value


def _safe_network_host(host: str, allowed_hosts: frozenset[str]) -> bool:
    normalized = host.rstrip(".").lower()
    if normalized in {item.rstrip(".").lower() for item in allowed_hosts}:
        return True
    try:
        addresses = {ip_address(normalized)}
    except ValueError:
        try:
            addresses = {
                ip_address(item[4][0])
                for item in socket.getaddrinfo(normalized, None, type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError):
            return False
    return bool(addresses) and all(
        (address.is_private or address.is_loopback)
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_unspecified
        for address in addresses
    )


def _validate_cdp_endpoint(endpoint: str, config: ProviderConfig) -> str:
    try:
        parsed = urlsplit(endpoint)
        host = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise AdsPowerProviderError(
            "UNSAFE_CDP_ENDPOINT", "CDP endpoint is not permitted by policy"
        ) from error
    if (
        parsed.scheme not in {"ws", "wss", "http", "https"}
        or not host
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not _safe_network_host(host, config.endpoint_policy.allowed_hosts)
    ):
        raise AdsPowerProviderError(
            "UNSAFE_CDP_ENDPOINT", "CDP endpoint is not permitted by policy"
        )
    return endpoint


class AdsPowerApiClient:
    """Narrow Local API V2 client with no profile mutation operations."""

    def __init__(
        self,
        config: ProviderConfig,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_sleeper: RetrySleeper = asyncio.sleep,
    ) -> None:
        self._config = config
        self._settings = _settings(config)
        self._token = token
        self._transport = transport
        self._retry_sleeper = retry_sleeper

    async def check_status(self) -> None:
        await self._request("GET", "/status")

    async def list_profiles(self) -> list[dict[str, object]]:
        profiles: list[dict[str, object]] = []
        for page in range(1, 11):
            payload = await self._request(
                "POST",
                "/api/v2/browser-profile/list",
                json_body={"page": page, "limit": 100},
            )
            data = payload.get("data")
            raw_items = data.get("list") if isinstance(data, Mapping) else None
            if not isinstance(raw_items, list):
                raise AdsPowerProviderError(
                    "PROVIDER_RESPONSE_INVALID", "AdsPower response is invalid"
                )
            items = [dict(item) for item in raw_items if isinstance(item, Mapping)]
            profiles.extend(items)
            if len(raw_items) < 100:
                return profiles
        raise AdsPowerProviderError(
            "PROVIDER_RESPONSE_INVALID", "AdsPower profile list exceeded its safe limit"
        )

    async def active(self, profile_ref: str) -> tuple[bool, str | None]:
        payload = await self._request(
            "GET",
            "/api/v2/browser-profile/active",
            params={"profile_id": profile_ref},
        )
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise AdsPowerProviderError(
                "PROVIDER_RESPONSE_INVALID", "AdsPower response is invalid"
            )
        status = data.get("status")
        if status not in {"Active", "Inactive"}:
            raise AdsPowerProviderError(
                "PROVIDER_RESPONSE_INVALID", "AdsPower response is invalid"
            )
        return status == "Active", self._cdp_from_data(data) if status == "Active" else None

    async def start(self, profile_ref: str, *, headless: bool) -> str:
        payload = await self._request(
            "POST",
            "/api/v2/browser-profile/start",
            json_body={"profile_id": profile_ref, "headless": "1" if headless else "0"},
        )
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise AdsPowerProviderError(
                "PROVIDER_RESPONSE_INVALID", "AdsPower response is invalid"
            )
        endpoint = self._cdp_from_data(data)
        if endpoint is None:
            raise AdsPowerProviderError(
                "PROVIDER_RESPONSE_INVALID", "AdsPower response is invalid"
            )
        return endpoint

    async def stop(self, profile_ref: str) -> None:
        await self._request(
            "POST",
            "/api/v2/browser-profile/stop",
            json_body={"profile_id": profile_ref},
        )

    @staticmethod
    def _cdp_from_data(data: Mapping[object, object]) -> str | None:
        ws = data.get("ws")
        endpoint = ws.get("puppeteer") if isinstance(ws, Mapping) else None
        return endpoint if isinstance(endpoint, str) and endpoint else None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        headers = {"Authorization": f"Bearer {self._token}"}
        timeout = httpx.Timeout(self._settings.timeout)
        for attempt in range(1, self._settings.attempts + 1):
            try:
                async with httpx.AsyncClient(
                    transport=self._transport,
                    timeout=timeout,
                    follow_redirects=False,
                ) as client:
                    async with client.stream(
                        method,
                        f"{self._settings.api_url}{path}",
                        headers=headers,
                        params=params,
                        json=json_body,
                    ) as response:
                        if response.is_redirect:
                            raise AdsPowerProviderError(
                                "PROVIDER_REDIRECT_REJECTED",
                                "AdsPower API redirects are not permitted",
                            )
                        if response.status_code in {401, 403}:
                            raise AdsPowerProviderError(
                                "PROVIDER_AUTH_FAILED", "AdsPower authentication failed"
                            )
                        if response.status_code in _TRANSIENT_STATUS_CODES:
                            if attempt < self._settings.attempts:
                                await self._retry_sleeper(0.1 * (2 ** (attempt - 1)))
                                continue
                            raise AdsPowerProviderError(
                                "PROVIDER_UNAVAILABLE", "AdsPower API is unavailable"
                            )
                        if response.status_code != 200:
                            raise AdsPowerProviderError(
                                "PROVIDER_REQUEST_FAILED", "AdsPower API request failed"
                            )
                        declared = response.headers.get("content-length")
                        if declared is not None:
                            try:
                                if int(declared) > self._settings.max_response_bytes:
                                    raise AdsPowerProviderError(
                                        "PROVIDER_RESPONSE_TOO_LARGE",
                                        "AdsPower response exceeded the size limit",
                                    )
                            except ValueError:
                                raise AdsPowerProviderError(
                                    "PROVIDER_RESPONSE_INVALID", "AdsPower response is invalid"
                                ) from None
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > self._settings.max_response_bytes:
                                raise AdsPowerProviderError(
                                    "PROVIDER_RESPONSE_TOO_LARGE",
                                    "AdsPower response exceeded the size limit",
                                )
            except AdsPowerProviderError:
                raise
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt < self._settings.attempts:
                    await self._retry_sleeper(0.1 * (2 ** (attempt - 1)))
                    continue
                raise AdsPowerProviderError(
                    "PROVIDER_UNAVAILABLE", "AdsPower API is unavailable"
                ) from None
            try:
                decoded = json.loads(body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise AdsPowerProviderError(
                    "PROVIDER_RESPONSE_INVALID", "AdsPower response is invalid"
                ) from None
            if not isinstance(decoded, dict) or decoded.get("code") != 0:
                raise AdsPowerProviderError(
                    "PROVIDER_REQUEST_FAILED", "AdsPower API request failed"
                )
            return cast(dict[str, object], decoded)
        raise AssertionError("retry loop must return or raise")


class AdsPowerSession:
    provider_code = "ADSPOWER"

    def __init__(
        self,
        *,
        context: Any,
        browser: Any,
        runtime: Any,
        api: _AdsPowerApi,
        profile_key: str,
        provider_version: str,
        started_by_current_task: bool,
        release_profile: Callable[[str], None],
    ) -> None:
        self.context = context
        self.profile_key = profile_key
        self.provider_version = provider_version
        self.browser_version = str(browser.version)
        self.started_by_current_task = started_by_current_task
        self._browser = browser
        self._runtime = runtime
        self._api = api
        self._release_profile = release_profile
        self._closed = False
        self._close_lock = asyncio.Lock()

    async def health_check(self) -> None:
        if self._closed:
            raise AdsPowerProviderError("SESSION_CLOSED", "browser session is closed")
        try:
            if not self._browser.is_connected():
                raise RuntimeError("disconnected")
            page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            await page.evaluate("1")
        except Exception:
            raise AdsPowerProviderError(
                "BROWSER_UNHEALTHY", "browser session failed its health check"
            ) from None

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            try:
                await self._browser.close()
            finally:
                try:
                    await self._runtime.stop()
                finally:
                    try:
                        if self.started_by_current_task:
                            await self._api.stop(self.profile_key)
                    finally:
                        self._release_profile(self.profile_key)


class AdsPowerProvider:
    code = "ADSPOWER"
    version = "1.0"
    capabilities = BrowserCapabilities(
        persistent_profiles=True,
        visible_login=True,
        remote_cdp=True,
        attach_existing_session=True,
    )

    def __init__(
        self,
        *,
        secret_resolver: SecretResolver = _default_secret_resolver,
        api_factory: ApiFactory | None = None,
        playwright_starter: Callable[[], Any] | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
        retry_sleeper: RetrySleeper = asyncio.sleep,
    ) -> None:
        self._secret_resolver = secret_resolver
        self._api_factory = api_factory
        self._playwright_starter = playwright_starter or async_playwright
        self._http_transport = http_transport
        self._retry_sleeper = retry_sleeper
        self._active_profiles: set[str] = set()
        self._acquire_lock = asyncio.Lock()

    async def validate_config(self, config: ProviderConfig) -> CapabilityReport:
        _settings(config)
        api = await self._api(config)
        await api.check_status()
        return CapabilityReport(self.code, self.version, self.capabilities)

    async def list_profiles(self, config: ProviderConfig) -> list[ProfileSummary]:
        api = await self._api(config)
        raw_profiles = await api.list_profiles()
        profiles: list[ProfileSummary] = []
        for raw in raw_profiles:
            profile_ref = raw.get("profile_id")
            if not isinstance(profile_ref, str) or not _PROFILE_REFERENCE.fullmatch(profile_ref):
                continue
            display_name = raw.get("name")
            running, _endpoint = await api.active(profile_ref)
            profiles.append(
                ProfileSummary(
                    profile_ref,
                    display_name if isinstance(display_name, str) and display_name else profile_ref,
                    running,
                )
            )
        return profiles

    async def acquire(self, request: BrowserSessionRequest) -> AdsPowerSession:
        settings = _settings(request.config)
        if not _PROFILE_REFERENCE.fullmatch(request.profile_ref):
            raise AdsPowerProviderError(
                "INVALID_PROFILE_REFERENCE", "AdsPower profile reference is invalid"
            )
        async with self._acquire_lock:
            if request.profile_ref in self._active_profiles:
                raise AdsPowerProviderError(
                    "PROFILE_ALREADY_ACQUIRED", "browser profile is already acquired"
                )
            self._active_profiles.add(request.profile_ref)

        api = await self._api(request.config)
        started = False
        runtime: Any | None = None
        browser: Any | None = None
        try:
            profiles = await api.list_profiles()
            if request.profile_ref not in {
                item.get("profile_id") for item in profiles if isinstance(item, Mapping)
            }:
                raise AdsPowerProviderError("PROFILE_NOT_FOUND", "browser profile was not found")
            running, endpoint = await api.active(request.profile_ref)
            if not running:
                endpoint = await api.start(request.profile_ref, headless=request.headless)
                started = True
            if endpoint is None:
                raise AdsPowerProviderError(
                    "PROVIDER_RESPONSE_INVALID", "AdsPower response is invalid"
                )
            safe_endpoint = _validate_cdp_endpoint(endpoint, request.config)
            runtime = await self._playwright_starter().start()
            browser = await runtime.chromium.connect_over_cdp(safe_endpoint)
            if not browser.contexts:
                raise AdsPowerProviderError(
                    "CDP_CONTEXT_UNAVAILABLE", "AdsPower browser context is unavailable"
                )
            return AdsPowerSession(
                context=browser.contexts[0],
                browser=browser,
                runtime=runtime,
                api=api,
                profile_key=request.profile_ref,
                provider_version=settings.provider_version,
                started_by_current_task=started,
                release_profile=self._release_profile,
            )
        except AdsPowerProviderError:
            if browser is not None:
                await browser.close()
            if runtime is not None:
                await runtime.stop()
            if started:
                await self._safe_stop(api, request.profile_ref)
            self._release_profile(request.profile_ref)
            raise
        except Exception:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            if runtime is not None:
                try:
                    await runtime.stop()
                except Exception:
                    pass
            if started:
                await self._safe_stop(api, request.profile_ref)
            self._release_profile(request.profile_ref)
            raise AdsPowerProviderError(
                "CDP_CONNECT_FAILED", "AdsPower browser connection failed"
            ) from None

    async def _api(self, config: ProviderConfig) -> _AdsPowerApi:
        _settings(config)
        reference = config.secret_ref
        if reference is None:
            raise AdsPowerProviderError(
                "INVALID_PROVIDER_CONFIG", "AdsPower secret reference is required"
            )
        resolved = self._secret_resolver(reference)
        token = await resolved if inspect.isawaitable(resolved) else resolved
        if not token:
            raise AdsPowerProviderError(
                "SECRET_UNAVAILABLE", "AdsPower provider credential is unavailable"
            )
        if self._api_factory is not None:
            return self._api_factory(config, token)
        return AdsPowerApiClient(
            config,
            token,
            transport=self._http_transport,
            retry_sleeper=self._retry_sleeper,
        )

    async def _safe_stop(self, api: _AdsPowerApi, profile_ref: str) -> None:
        try:
            await api.stop(profile_ref)
        except Exception:
            pass

    def _release_profile(self, profile_ref: str) -> None:
        self._active_profiles.discard(profile_ref)
