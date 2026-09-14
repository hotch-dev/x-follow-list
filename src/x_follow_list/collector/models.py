from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RelationshipSide(StrEnum):
    FOLLOWER = "FOLLOWER"
    FOLLOWING = "FOLLOWING"


@dataclass(frozen=True, slots=True)
class ObservedProfile:
    x_user_id: str
    username: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedPage:
    side: RelationshipSide
    items: tuple[ObservedProfile, ...]
    next_cursor: str | None
    terminal: bool
    schema_version: str
    rejected_count: int
    duplicate_count: int
    empty_confirmed: bool


@dataclass(frozen=True, slots=True)
class ValidatedCollection:
    items: tuple[ObservedProfile, ...]
    schema_versions: frozenset[str]
    unusual_drop_confirmed: bool = False
