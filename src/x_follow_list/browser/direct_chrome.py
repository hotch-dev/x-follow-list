from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from x_follow_list.browser.contracts import (
    BrowserCapabilities,
    BrowserSessionRequest,
    CapabilityReport,
    ProfileSummary,
    ProviderConfig,
)

_PROFILE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_PROFILE_MARKER = ".x-follow-list-profile"


class DirectChromeProviderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DirectChromeSession:
    provider_code = "DIRECT_CHROME"
    started_by_current_task = True

    def __init__(
        self,
        context: Any,
        runtime: Any,
        profile_key: str,
        release_profile: Callable[[str], None],
    ) -> None:
        self.context = context
        self.profile_key = profile_key
        self._runtime = runtime
        self._release_profile = release_profile
        self._closed = False
        self._close_lock = asyncio.Lock()

    async def health_check(self) -> None:
        if self._closed:
            raise DirectChromeProviderError("SESSION_CLOSED", "browser session is closed")
        try:
            page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            await page.evaluate("1")
        except Exception:
            raise DirectChromeProviderError(
                "BROWSER_UNHEALTHY", "browser session failed its health check"
            ) from None

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            try:
                await self.context.close()
            finally:
                try:
                    await self._runtime.stop()
                finally:
                    self._release_profile(self.profile_key)


class DirectChromeProvider:
    code = "DIRECT_CHROME"
    version = "1.0"
    capabilities = BrowserCapabilities(
        persistent_profiles=True,
        visible_login=True,
        remote_cdp=False,
        attach_existing_session=False,
    )

    def __init__(
        self,
        *,
        playwright_starter: Callable[[], Any] | None = None,
        forbidden_profile_roots: frozenset[Path] | None = None,
    ) -> None:
        self._playwright_starter = playwright_starter or async_playwright
        self._forbidden_roots = (
            self._default_forbidden_roots()
            if forbidden_profile_roots is None
            else frozenset(path.resolve() for path in forbidden_profile_roots)
        )
        self._active_profiles: set[str] = set()
        self._acquire_lock = asyncio.Lock()

    async def validate_config(self, config: ProviderConfig) -> CapabilityReport:
        self._profiles_root(config)
        return CapabilityReport(self.code, self.version, self.capabilities)

    async def list_profiles(self, config: ProviderConfig) -> list[ProfileSummary]:
        root = self._profiles_root(config)
        if not root.exists():
            return []
        return [
            ProfileSummary(path.name, path.name, path.name in self._active_profiles)
            for path in sorted(root.iterdir(), key=lambda item: item.name)
            if path.is_dir() and (path / _PROFILE_MARKER).is_file()
        ]

    async def acquire(self, request: BrowserSessionRequest) -> DirectChromeSession:
        root = self._profiles_root(request.config)
        profile_path = self._profile_path(root, request.profile_ref)
        async with self._acquire_lock:
            if request.profile_ref in self._active_profiles:
                raise DirectChromeProviderError(
                    "PROFILE_ALREADY_ACQUIRED", "browser profile is already acquired"
                )
            self._active_profiles.add(request.profile_ref)

        runtime: Any | None = None
        try:
            await asyncio.to_thread(self._prepare_profile, profile_path)
            runtime = await self._playwright_starter().start()
            context = await runtime.chromium.launch_persistent_context(
                channel="chrome",
                user_data_dir=str(profile_path),
                headless=request.headless,
            )
        except Exception:
            if runtime is not None:
                await runtime.stop()
            self._release_profile(request.profile_ref)
            raise DirectChromeProviderError(
                "BROWSER_START_FAILED", "Google Chrome could not be started"
            ) from None
        return DirectChromeSession(
            context, runtime, request.profile_ref, self._release_profile
        )

    async def delete_profile(self, config: ProviderConfig, profile_ref: str) -> bool:
        root = self._profiles_root(config)
        profile_path = self._profile_path(root, profile_ref)
        if profile_ref in self._active_profiles:
            raise DirectChromeProviderError(
                "PROFILE_ALREADY_ACQUIRED", "browser profile is already acquired"
            )
        if not profile_path.exists():
            return False
        if not (profile_path / _PROFILE_MARKER).is_file():
            raise DirectChromeProviderError(
                "PROFILE_NOT_MANAGED", "browser profile is not system managed"
            )
        await asyncio.to_thread(shutil.rmtree, profile_path)
        return True

    def _profiles_root(self, config: ProviderConfig) -> Path:
        if config.provider_code != self.code or config.config_version != 1:
            raise DirectChromeProviderError(
                "INVALID_PROVIDER_CONFIG", "Direct Chrome provider config is invalid"
            )
        if config.secret_ref is not None:
            raise DirectChromeProviderError(
                "INVALID_PROVIDER_CONFIG", "Direct Chrome does not accept a secret reference"
            )
        raw_root = config.config.get("profiles_root")
        if not isinstance(raw_root, str):
            raise DirectChromeProviderError(
                "INVALID_PROVIDER_CONFIG", "managed profiles root is required"
            )
        root = Path(raw_root)
        if not root.is_absolute():
            raise DirectChromeProviderError(
                "INVALID_PROVIDER_CONFIG", "managed profiles root must be absolute"
            )
        resolved = root.resolve()
        if any(
            resolved == forbidden
            or resolved.is_relative_to(forbidden)
            or forbidden.is_relative_to(resolved)
            for forbidden in self._forbidden_roots
        ):
            raise DirectChromeProviderError(
                "PROFILE_NOT_MANAGED", "browser profile root is not system managed"
            )
        return resolved

    @staticmethod
    def _profile_path(root: Path, profile_ref: str) -> Path:
        if not _PROFILE_REFERENCE.fullmatch(profile_ref) or profile_ref in {".", ".."}:
            raise DirectChromeProviderError(
                "INVALID_PROFILE_REFERENCE", "browser profile reference is invalid"
            )
        profile_path = (root / profile_ref).resolve()
        if not profile_path.is_relative_to(root):
            raise DirectChromeProviderError(
                "INVALID_PROFILE_REFERENCE", "browser profile reference is invalid"
            )
        return profile_path

    @staticmethod
    def _prepare_profile(profile_path: Path) -> None:
        profile_path.mkdir(parents=True, exist_ok=True)
        (profile_path / _PROFILE_MARKER).touch(exist_ok=True)

    def _release_profile(self, profile_ref: str) -> None:
        self._active_profiles.discard(profile_ref)

    @staticmethod
    def _default_forbidden_roots() -> frozenset[Path]:
        home = Path.home()
        return frozenset(
            path.resolve()
            for path in (
                home / "AppData/Local/Google/Chrome/User Data",
                home / "Library/Application Support/Google/Chrome",
                home / ".config/google-chrome",
            )
        )
