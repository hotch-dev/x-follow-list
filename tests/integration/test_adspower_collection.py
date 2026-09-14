from __future__ import annotations

from collections.abc import Callable

import pytest

from x_follow_list.browser.adspower import AdsPowerProvider
from x_follow_list.browser.contracts import BrowserSessionRequest, ProviderConfig
from x_follow_list.collector.models import RelationshipSide
from x_follow_list.collector.parser import VersionedRelationshipParser
from x_follow_list.collector.response import ResponseCollector


class RelationshipResponse:
    url = "http://local.test/api/relationships"
    headers = {"content-type": "application/json"}

    async def json(self) -> object:
        return {
            "kind": "relationship_list",
            "schema_version": "1",
            "relationship": "FOLLOWER",
            "items": [
                {"user": {"id": "101", "username": "one"}},
                {"user": {"id": "102", "username": "two"}},
            ],
            "next_cursor": None,
            "terminal": True,
            "empty_confirmed": False,
            "displayed_total": 2,
        }


class ControlledPage:
    def __init__(self) -> None:
        self._listener: Callable[[RelationshipResponse], None] | None = None

    def on(self, event: str, listener: Callable[[RelationshipResponse], None]) -> None:
        assert event == "response"
        self._listener = listener

    def remove_listener(
        self, event: str, listener: Callable[[RelationshipResponse], None]
    ) -> None:
        assert event == "response" and listener is self._listener
        self._listener = None

    async def evaluate(self, _expression: str) -> int:
        return 1

    def emit_relationship_response(self) -> None:
        assert self._listener is not None
        self._listener(RelationshipResponse())


class ControlledBrowser:
    version = "Chrome/134.0.0.0"

    def __init__(self, page: ControlledPage) -> None:
        self.contexts = [type("Context", (), {"pages": [page]})()]
        self.closed = False

    def is_connected(self) -> bool:
        return not self.closed

    async def close(self) -> None:
        self.closed = True


class ControlledRuntime:
    def __init__(self, browser: ControlledBrowser) -> None:
        self.chromium = self
        self.browser = browser
        self.connected_endpoint: str | None = None

    async def start(self) -> ControlledRuntime:
        return self

    async def connect_over_cdp(self, endpoint: str) -> ControlledBrowser:
        self.connected_endpoint = endpoint
        return self.browser

    async def stop(self) -> None:
        return None


class FakeLocalApi:
    endpoint = "ws://127.0.0.1:9222/devtools/browser/controlled"

    async def check_status(self) -> None:
        return None

    async def list_profiles(self) -> list[dict[str, object]]:
        return [{"profile_id": "existing-profile", "name": "Existing"}]

    async def active(self, _profile_ref: str) -> tuple[bool, str | None]:
        return True, self.endpoint

    async def start(self, _profile_ref: str, *, headless: bool) -> str:
        raise AssertionError(f"already-running profile must not be started: {headless}")

    async def stop(self, _profile_ref: str) -> None:
        raise AssertionError("already-running profile must not be stopped")


@pytest.mark.asyncio
async def test_adspower_session_reuses_the_a08_collector_without_provider_branches() -> None:
    page = ControlledPage()
    browser = ControlledBrowser(page)
    runtime = ControlledRuntime(browser)
    api = FakeLocalApi()
    provider = AdsPowerProvider(
        secret_resolver=lambda _reference: "test-token",
        api_factory=lambda _config, _token: api,
        playwright_starter=lambda: runtime,
    )
    config = ProviderConfig(
        "ADSPOWER",
        1,
        {
            "api_url": "http://127.0.0.1:50325",
            "api_version": "v2",
            "provider_version": "3.4.1",
        },
        "env://ADSPOWER_API_TOKEN",
    )
    session = await provider.acquire(
        BrowserSessionRequest(config, "existing-profile", "scan-local")
    )

    async def navigate(actual_page: ControlledPage, side: RelationshipSide) -> int:
        assert side is RelationshipSide.FOLLOWER
        actual_page.emit_relationship_response()
        return 2

    result = await ResponseCollector(VersionedRelationshipParser()).collect(
        session.context.pages[0], navigate, RelationshipSide.FOLLOWER
    )
    await session.close()

    assert [item.x_user_id for item in result.items] == ["101", "102"]
    assert runtime.connected_endpoint == api.endpoint
    assert browser.closed is True
