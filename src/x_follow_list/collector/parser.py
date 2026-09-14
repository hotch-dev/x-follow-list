from __future__ import annotations

from collections.abc import Mapping

from x_follow_list.collector.models import ObservedProfile, ParsedPage, RelationshipSide


class ParserChangedError(ValueError):
    """A safe, actionable parsing failure that never includes raw response data."""


class PayloadClassifier:
    """Recognize candidate relationship payloads by structure, not endpoint hashes."""

    @staticmethod
    def is_candidate(payload: object) -> bool:
        return isinstance(payload, Mapping) and payload.get("kind") == "relationship_list"

    @staticmethod
    def classify(payload: object) -> str | None:
        if not PayloadClassifier.is_candidate(payload) or not isinstance(payload, Mapping):
            return None
        schema_version = payload.get("schema_version")
        return schema_version if isinstance(schema_version, str) else None


class VersionedRelationshipParser:
    version = "1"

    def __init__(self, classifier: PayloadClassifier | None = None) -> None:
        self._classifier = classifier or PayloadClassifier()

    def is_candidate(self, payload: object) -> bool:
        return self._classifier.is_candidate(payload)

    def parse(self, payload: object, expected_side: RelationshipSide) -> ParsedPage:
        schema_version = self._classifier.classify(payload)
        if schema_version is None or not isinstance(payload, Mapping):
            raise ParserChangedError("relationship response structure is not recognized")
        if schema_version != self.version:
            raise ParserChangedError("relationship response schema version is not supported")
        if payload.get("relationship") != expected_side.value:
            raise ParserChangedError("relationship side does not match the active collection")
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise ParserChangedError("relationship response structure is not recognized")

        members: dict[str, ObservedProfile] = {}
        rejected_count = 0
        duplicate_count = 0
        for raw_item in raw_items:
            member = self._parse_member(raw_item)
            if member is None:
                rejected_count += 1
                continue
            if member.x_user_id in members:
                duplicate_count += 1
            members[member.x_user_id] = member

        terminal = payload.get("terminal")
        empty_confirmed = payload.get("empty_confirmed", False)
        next_cursor = payload.get("next_cursor")
        if not isinstance(terminal, bool) or not isinstance(empty_confirmed, bool):
            raise ParserChangedError("relationship response structure is not recognized")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise ParserChangedError("relationship response structure is not recognized")
        return ParsedPage(
            side=expected_side,
            items=tuple(members.values()),
            next_cursor=next_cursor,
            terminal=terminal,
            schema_version=self.version,
            rejected_count=rejected_count,
            duplicate_count=duplicate_count,
            empty_confirmed=empty_confirmed,
        )

    @staticmethod
    def _parse_member(raw_item: object) -> ObservedProfile | None:
        if not isinstance(raw_item, Mapping):
            return None
        raw_user = raw_item.get("user")
        if not isinstance(raw_user, Mapping):
            return None
        x_user_id = raw_user.get("id")
        if not isinstance(x_user_id, str) or not x_user_id:
            return None
        return ObservedProfile(
            x_user_id=x_user_id,
            username=VersionedRelationshipParser._optional_text(raw_user.get("username")),
            display_name=VersionedRelationshipParser._optional_text(
                raw_user.get("display_name")
            ),
            avatar_url=VersionedRelationshipParser._optional_text(raw_user.get("avatar_url")),
        )

    @staticmethod
    def _optional_text(value: object) -> str | None:
        return value if isinstance(value, str) else None
