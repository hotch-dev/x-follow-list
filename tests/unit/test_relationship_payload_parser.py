import json
from pathlib import Path

import pytest

from x_follow_list.collector.models import RelationshipSide
from x_follow_list.collector.parser import ParserChangedError, VersionedRelationshipParser

FIXTURES = Path(__file__).parents[1] / "fixtures" / "x_payloads"


def load_fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_v1_parser_extracts_stable_ids_and_display_fields() -> None:
    page = VersionedRelationshipParser().parse(
        load_fixture("v1_followers_page.json"), RelationshipSide.FOLLOWER
    )

    assert [item.x_user_id for item in page.items] == ["101", "102"]
    assert page.items[0].username == "alice"
    assert page.items[0].display_name == "Alice"
    assert page.next_cursor == "followers-2"
    assert page.schema_version == "1"
    assert page.terminal is False


def test_parser_deduplicates_by_x_user_id_and_keeps_latest_display_cache() -> None:
    payload = {
        "kind": "relationship_list",
        "schema_version": "1",
        "relationship": "FOLLOWING",
        "items": [
            {"user": {"id": "77", "username": "old"}},
            {"user": {"id": "77", "username": "new"}},
        ],
        "next_cursor": None,
        "terminal": True,
        "empty_confirmed": False,
    }

    page = VersionedRelationshipParser().parse(payload, RelationshipSide.FOLLOWING)

    assert [(item.x_user_id, item.username) for item in page.items] == [("77", "new")]
    assert page.duplicate_count == 1


def test_parser_counts_missing_or_invalid_stable_ids_as_rejected() -> None:
    payload = {
        "kind": "relationship_list",
        "schema_version": "1",
        "relationship": "FOLLOWER",
        "items": [{"user": {}}, {"user": {"id": ""}}, {"not_user": {}}],
        "next_cursor": None,
        "terminal": True,
        "empty_confirmed": False,
    }

    page = VersionedRelationshipParser().parse(payload, RelationshipSide.FOLLOWER)

    assert page.items == ()
    assert page.rejected_count == 3


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"kind": "unrelated", "schema_version": "1", "items": []},
    ],
)
def test_parser_fails_closed_for_unknown_root_structures(payload: object) -> None:
    with pytest.raises(ParserChangedError, match="response structure"):
        VersionedRelationshipParser().parse(payload, RelationshipSide.FOLLOWER)


def test_parser_fails_closed_for_unknown_schema_version() -> None:
    with pytest.raises(ParserChangedError, match="schema version"):
        VersionedRelationshipParser().parse(
            load_fixture("unknown_schema.json"), RelationshipSide.FOLLOWER
        )


def test_parser_rejects_payload_for_the_other_relationship_side() -> None:
    with pytest.raises(ParserChangedError, match="relationship side"):
        VersionedRelationshipParser().parse(
            load_fixture("v1_followers_page.json"), RelationshipSide.FOLLOWING
        )
