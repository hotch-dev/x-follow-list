from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from types import MappingProxyType
from typing import Any, Protocol

from x_follow_list.collector.models import RelationshipSide, ValidatedCollection


class CollectionSource(Protocol):
    async def collect(
        self,
        page: Any,
        navigate: Any,
        side: RelationshipSide,
        *,
        previous_ids: frozenset[str] = frozenset(),
        confirmed_drop_ids: frozenset[str] | None = None,
    ) -> ValidatedCollection: ...


PublishCollections = Callable[
    [Mapping[RelationshipSide, ValidatedCollection]], Awaitable[None]
]


class RelationshipCollectionPipeline:
    """Publish only after both relationship sides independently pass validation."""

    def __init__(self, collector: CollectionSource, publish: PublishCollections) -> None:
        self._collector = collector
        self._publish = publish

    async def collect(
        self,
        page: Any,
        navigations: Mapping[RelationshipSide, Any],
        *,
        previous_ids: Mapping[RelationshipSide, frozenset[str]] | None = None,
        confirmed_drop_ids: Mapping[RelationshipSide, frozenset[str]] | None = None,
    ) -> Mapping[RelationshipSide, ValidatedCollection]:
        previous = previous_ids or {}
        confirmed = confirmed_drop_ids or {}
        collections: dict[RelationshipSide, ValidatedCollection] = {}
        for side in (RelationshipSide.FOLLOWER, RelationshipSide.FOLLOWING):
            collections[side] = await self._collector.collect(
                page,
                navigations[side],
                side,
                previous_ids=previous.get(side, frozenset()),
                confirmed_drop_ids=confirmed.get(side),
            )
        complete = MappingProxyType(collections)
        await self._publish(complete)
        return complete
