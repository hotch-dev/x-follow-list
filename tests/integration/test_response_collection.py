from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from x_follow_list.collector.models import RelationshipSide
from x_follow_list.collector.navigation import NeedsUserActionError
from x_follow_list.collector.parser import VersionedRelationshipParser
from x_follow_list.collector.response import ResponseCollector


class FakeResponse:
    def __init__(self, payload: object, url: str) -> None:
        self.url = url
        self._payload = payload

    async def json(self) -> object:
        return self._payload


class FakePage:
    def __init__(self) -> None:
        self.listener: Callable[[FakeResponse], None] | None = None

    def on(self, event: str, listener: Callable[[FakeResponse], None]) -> None:
        assert event == "response"
        self.listener = listener

    def remove_listener(self, event: str, listener: Callable[[FakeResponse], None]) -> None:
        assert event == "response" and listener is self.listener
        self.listener = None

    def emit(
        self,
        payload: object,
        url: str = "http://fixture.test/api/relationships",
    ) -> None:
        assert self.listener is not None, "collector must listen before navigation"
        self.listener(FakeResponse(payload, url))


def payload(*ids: str, terminal: bool) -> dict[str, Any]:
    return {
        "kind": "relationship_list",
        "schema_version": "1",
        "relationship": "FOLLOWER",
        "items": [{"user": {"id": value, "username": f"user-{value}"}} for value in ids],
        "next_cursor": None if terminal else "next",
        "terminal": terminal,
        "empty_confirmed": terminal and not ids,
    }


@pytest.mark.asyncio
async def test_collector_registers_before_navigation_and_returns_validated_members() -> None:
    page = FakePage()

    async def navigate(actual_page: FakePage, side: RelationshipSide) -> int | None:
        assert side is RelationshipSide.FOLLOWER
        actual_page.emit(payload("1", "2", terminal=False))
        actual_page.emit(payload("2", "3", terminal=True))
        return 3

    collector = ResponseCollector(VersionedRelationshipParser())
    result = await collector.collect(page, navigate, RelationshipSide.FOLLOWER)

    assert [member.x_user_id for member in result.items] == ["1", "2", "3"]
    assert page.listener is None


@pytest.mark.asyncio
async def test_collector_removes_listener_and_propagates_navigation_failure() -> None:
    page = FakePage()

    async def challenge(_page: FakePage, _side: RelationshipSide) -> int | None:
        raise NeedsUserActionError("account requires user action")

    collector = ResponseCollector(VersionedRelationshipParser())
    with pytest.raises(NeedsUserActionError):
        await collector.collect(page, challenge, RelationshipSide.FOLLOWER)

    assert page.listener is None


@pytest.mark.asyncio
async def test_collector_classifies_by_payload_structure_instead_of_endpoint_hash() -> None:
    page = FakePage()

    async def navigate(actual_page: FakePage, _side: RelationshipSide) -> int | None:
        actual_page.emit(
            {"kind": "unrelated", "schema_version": "1"},
            "http://fixture.test/api/background-data",
        )
        actual_page.emit(
            payload("9", terminal=True),
            "http://fixture.test/i/api/graphql/changing-query-hash",
        )
        return 1

    result = await ResponseCollector(VersionedRelationshipParser()).collect(
        page, navigate, RelationshipSide.FOLLOWER
    )

    assert [member.x_user_id for member in result.items] == ["9"]
