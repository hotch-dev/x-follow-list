from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from x_follow_list.collector.models import (
    ObservedProfile,
    RelationshipSide,
    ValidatedCollection,
)
from x_follow_list.collector.pipeline import RelationshipCollectionPipeline


class StubCollector:
    def __init__(self, fail_on: RelationshipSide | None = None) -> None:
        self.fail_on = fail_on

    async def collect(
        self,
        _page: Any,
        _navigate: Any,
        side: RelationshipSide,
        *,
        previous_ids: frozenset[str] = frozenset(),
        confirmed_drop_ids: frozenset[str] | None = None,
    ) -> ValidatedCollection:
        del previous_ids, confirmed_drop_ids
        if side is self.fail_on:
            raise RuntimeError("collection failed")
        return ValidatedCollection(
            items=(ObservedProfile(f"{side.value}-1"),),
            schema_versions=frozenset({"1"}),
        )


@pytest.mark.asyncio
async def test_pipeline_publishes_only_after_both_sides_are_complete() -> None:
    published: Mapping[RelationshipSide, ValidatedCollection] | None = None

    async def publish(
        collections: Mapping[RelationshipSide, ValidatedCollection],
    ) -> None:
        nonlocal published
        published = collections

    pipeline = RelationshipCollectionPipeline(StubCollector(), publish)
    await pipeline.collect(
        object(),
        {RelationshipSide.FOLLOWER: object(), RelationshipSide.FOLLOWING: object()},
    )

    assert published is not None
    assert set(published) == {RelationshipSide.FOLLOWER, RelationshipSide.FOLLOWING}


@pytest.mark.asyncio
async def test_pipeline_never_publishes_partial_results() -> None:
    publish_calls = 0

    async def publish(_collections: Mapping[RelationshipSide, ValidatedCollection]) -> None:
        nonlocal publish_calls
        publish_calls += 1

    pipeline = RelationshipCollectionPipeline(
        StubCollector(fail_on=RelationshipSide.FOLLOWING), publish
    )
    with pytest.raises(RuntimeError, match="collection failed"):
        await pipeline.collect(
            object(),
            {RelationshipSide.FOLLOWER: object(), RelationshipSide.FOLLOWING: object()},
        )

    assert publish_calls == 0
