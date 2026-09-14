from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from x_follow_list.collector.completeness import CompletenessGuard
from x_follow_list.collector.models import RelationshipSide, ValidatedCollection
from x_follow_list.collector.parser import VersionedRelationshipParser


class ResponseLike(Protocol):
    url: str
    headers: Mapping[str, str]

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
        responses: list[ResponseLike] = []

        def on_response(response: ResponseLike) -> None:
            content_type = response.headers.get("content-type", "").lower()
            if "json" not in content_type:
                return
            responses.append(response)

        page.on("response", on_response)
        try:
            displayed_total = await navigate(page, side)
            parsed_pages = []
            for response in responses:
                payload = await response.json()
                if self._parser.is_candidate(payload):
                    parsed_pages.append(self._parser.parse(payload, side))
            return self._guard.validate(
                parsed_pages,
                displayed_total=displayed_total,
                previous_ids=previous_ids,
                confirmed_drop_ids=confirmed_drop_ids,
            )
        finally:
            page.remove_listener("response", on_response)
