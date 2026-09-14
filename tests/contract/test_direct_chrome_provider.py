from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from x_follow_list.browser.contracts import (
    BrowserProvider,
    BrowserSessionRequest,
    ProviderConfig,
)
from x_follow_list.browser.direct_chrome import (
    DirectChromeProvider,
    DirectChromeProviderError,
)


class FakePage:
    def __init__(self) -> None:
        self.evaluations: list[str] = []

    async def evaluate(self, expression: str) -> object:
        self.evaluations.append(expression)
        return 1


class FakeContext:
    def __init__(self) -> None:
        self.pages = [FakePage()]
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1


class FakeChromium:
    def __init__(self, context: FakeContext, fail_start: bool = False) -> None:
        self.context = context
        self.fail_start = fail_start
        self.launch_options: dict[str, Any] | None = None

    async def launch_persistent_context(self, **options: Any) -> FakeContext:
        self.launch_options = options
        if self.fail_start:
            raise RuntimeError(
                "launch failed token=do-not-leak path=C:/private/chrome-profile"
            )
        return self.context


class FakePlaywright:
    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium
        self.stop_count = 0

    async def stop(self) -> None:
        self.stop_count += 1


class FakePlaywrightStarter:
    def __init__(self, runtime: FakePlaywright) -> None:
        self.runtime = runtime
        self.start_count = 0

    async def start(self) -> FakePlaywright:
        self.start_count += 1
        return self.runtime


def provider_config(root: Path) -> ProviderConfig:
    return ProviderConfig(
        provider_code="DIRECT_CHROME",
        config_version=1,
        config={"profiles_root": str(root)},
    )


def provider_with_fake_runtime(
    *, fail_start: bool = False, forbidden_roots: frozenset[Path] = frozenset()
) -> tuple[DirectChromeProvider, FakePlaywrightStarter, FakePlaywright, FakeContext]:
    context = FakeContext()
    runtime = FakePlaywright(FakeChromium(context, fail_start=fail_start))
    starter = FakePlaywrightStarter(runtime)
    provider = DirectChromeProvider(
        playwright_starter=lambda: starter,
        forbidden_profile_roots=forbidden_roots,
    )
    return provider, starter, runtime, context


def test_direct_chrome_implements_shared_provider_contract() -> None:
    provider, _starter, _runtime, _context = provider_with_fake_runtime()

    assert isinstance(provider, BrowserProvider)
    assert provider.code == "DIRECT_CHROME"
    assert provider.capabilities.persistent_profiles is True
    assert provider.capabilities.visible_login is True
    assert provider.capabilities.remote_cdp is False


@pytest.mark.asyncio
async def test_acquire_uses_an_isolated_persistent_chrome_profile(tmp_path: Path) -> None:
    provider, starter, runtime, context = provider_with_fake_runtime()
    config = provider_config(tmp_path / "managed-profiles")
    request = BrowserSessionRequest(
        config=config,
        profile_ref="account-profile",
        owner_task_id="bind-1",
    )

    report = await provider.validate_config(config)
    session = await provider.acquire(request)
    await session.health_check()
    await session.close()
    await session.close()

    expected_profile = tmp_path / "managed-profiles" / "account-profile"
    assert report.provider_code == "DIRECT_CHROME"
    assert session.context is context
    assert session.started_by_current_task is True
    assert starter.start_count == 1
    assert runtime.chromium.launch_options == {
        "channel": "chrome",
        "user_data_dir": str(expected_profile.resolve()),
        "headless": False,
    }
    assert context.pages[0].evaluations == ["1"]
    assert context.close_count == 1
    assert runtime.stop_count == 1
    assert (expected_profile / ".x-follow-list-profile").is_file()


@pytest.mark.asyncio
async def test_headless_mode_is_explicit_for_repeatable_local_e2e(tmp_path: Path) -> None:
    provider, _starter, runtime, _context = provider_with_fake_runtime()
    request = BrowserSessionRequest(
        config=provider_config(tmp_path / "profiles"),
        profile_ref="e2e-profile",
        owner_task_id="scan-1",
        headless=True,
    )

    session = await provider.acquire(request)
    await session.close()

    assert runtime.chromium.launch_options is not None
    assert runtime.chromium.launch_options["headless"] is True


@pytest.mark.asyncio
async def test_profile_release_preserves_data_but_unbind_deletes_managed_profile(
    tmp_path: Path,
) -> None:
    provider, _starter, _runtime, _context = provider_with_fake_runtime()
    root = tmp_path / "profiles"
    config = provider_config(root)
    request = BrowserSessionRequest(config, "owned-profile", "bind-2")

    session = await provider.acquire(request)
    profile_path = root / "owned-profile"
    state_file = profile_path / "browser-state"
    state_file.write_text("persistent", encoding="utf-8")
    await session.close()
    assert state_file.is_file()

    assert await provider.delete_profile(config, "owned-profile") is True
    assert not profile_path.exists()
    assert await provider.delete_profile(config, "owned-profile") is False


@pytest.mark.parametrize(
    "profile_ref",
    ["../daily-profile", "nested/profile", "nested\\profile", ".", ""],
)
@pytest.mark.asyncio
async def test_profile_reference_cannot_escape_the_managed_root(
    tmp_path: Path, profile_ref: str
) -> None:
    provider, _starter, _runtime, _context = provider_with_fake_runtime()
    request = BrowserSessionRequest(
        provider_config(tmp_path / "profiles"), profile_ref or "temporary", "bind-3"
    )
    if not profile_ref:
        object.__setattr__(request, "profile_ref", "")

    with pytest.raises(DirectChromeProviderError, match="profile reference"):
        await provider.acquire(request)


@pytest.mark.asyncio
async def test_daily_chrome_profile_root_is_rejected(tmp_path: Path) -> None:
    daily_root = (tmp_path / "Google" / "Chrome" / "User Data").resolve()
    provider, _starter, _runtime, _context = provider_with_fake_runtime(
        forbidden_roots=frozenset({daily_root})
    )

    with pytest.raises(DirectChromeProviderError, match="not system managed"):
        await provider.validate_config(provider_config(daily_root))


@pytest.mark.asyncio
async def test_start_failure_is_mapped_without_exposing_profile_or_diagnostics(
    tmp_path: Path,
) -> None:
    provider, _starter, _runtime, _context = provider_with_fake_runtime(fail_start=True)
    profile_root = tmp_path / "sensitive-profile-root"
    request = BrowserSessionRequest(
        provider_config(profile_root), "account-profile", "bind-4"
    )

    with pytest.raises(DirectChromeProviderError) as caught:
        await provider.acquire(request)

    rendered = f"{caught.value!r} {caught.value}"
    assert caught.value.code == "BROWSER_START_FAILED"
    assert str(profile_root) not in rendered
    assert "do-not-leak" not in rendered
    assert "C:/private/chrome-profile" not in rendered
