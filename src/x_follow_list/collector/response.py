from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from x_follow_list.collector.completeness import CompletenessGuard
from x_follow_list.collector.models import ParsedPage, RelationshipSide, ValidatedCollection
from x_follow_list.collector.parser import VersionedRelationshipParser


class ResponseLike(Protocol):
    url: str

    async def json(self) -> object: ...


Navigate = Callable[[Any, RelationshipSide], Awaitable[int | None]]


class ResponseCollector:
    def __init__(
        self,
        parser: VersionedRelationshipParser,
        guard: CompletenessGuard | None = None,
    ) -> None:
        self._parser = parser
        self._guard = guard or CompletenessGuard()

    async def collect(
        self,
        page: Any,
        navigate: Navigate,
        side: RelationshipSide,
        *,
        previous_ids: frozenset[str] = frozenset(),
        confirmed_drop_ids: frozenset[str] | None = None,
    ) -> ValidatedCollection:
        parsed_pages: list[ParsedPage] = []
        parse_tasks: list[asyncio.Task[None]] = []

        async def parse_response(response: ResponseLike) -> None:
            if "/api/relationships" not in response.url:
                return
            payload = await response.json()
            parsed_pages.append(self._parser.parse(payload, side))

        def on_response(response: ResponseLike) -> None:
            parse_tasks.append(asyncio.create_task(parse_response(response)))

        page.on("response", on_response)
        try:
            displayed_total = await navigate(page, side)
            await asyncio.sleep(0)
            if parse_tasks:
                await asyncio.gather(*parse_tasks)
            return self._guard.validate(
                parsed_pages,
                displayed_total=displayed_total,
                previous_ids=previous_ids,
                confirmed_drop_ids=confirmed_drop_ids,
            )
        finally:
            page.remove_listener("response", on_response)
