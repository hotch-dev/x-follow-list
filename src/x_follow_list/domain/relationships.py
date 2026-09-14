from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum


class RelationshipState(StrEnum):
    MUTUAL = "MUTUAL"
    NOT_FOLLOWING_BACK = "NOT_FOLLOWING_BACK"
    FOLLOWS_ME_ONLY = "FOLLOWS_ME_ONLY"
    ABSENT = "ABSENT"


class EventCategory(StrEnum):
    RELATIONSHIP_CHANGE = "RELATIONSHIP_CHANGE"
    ACTION_ITEM = "ACTION_ITEM"


class RelationshipEventType(StrEnum):
    NEW_FOLLOWER = "NEW_FOLLOWER"
    LOST_FOLLOWER = "LOST_FOLLOWER"
    NEW_FOLLOWING = "NEW_FOLLOWING"
    REMOVED_FOLLOWING = "REMOVED_FOLLOWING"
    UNFOLLOWED_ME_AFTER_MUTUAL = "UNFOLLOWED_ME_AFTER_MUTUAL"
    FOLLOWING_BLOCKLISTED_ACCOUNT = "FOLLOWING_BLOCKLISTED_ACCOUNT"


@dataclass(frozen=True, slots=True)
class Membership:
    follows_me: bool
    i_follow: bool

    @property
    def state(self) -> RelationshipState:
        if self.follows_me and self.i_follow:
            return RelationshipState.MUTUAL
        if self.i_follow:
            return RelationshipState.NOT_FOLLOWING_BACK
        if self.follows_me:
            return RelationshipState.FOLLOWS_ME_ONLY
        return RelationshipState.ABSENT


@dataclass(frozen=True, slots=True)
class RelationshipEvent:
    category: EventCategory
    event_type: RelationshipEventType
    dedupe_key: str


@dataclass(frozen=True, slots=True)
class TransitionResult:
    state: RelationshipState
    non_followback_streak: int
    events: tuple[RelationshipEvent, ...]


def event_dedupe_key(
    scan_run_id: str,
    subject_x_user_id: str,
    event_type: RelationshipEventType,
) -> str:
    if not scan_run_id or not subject_x_user_id:
        raise ValueError("scan run ID and subject X user ID are required")
    identity = "\0".join((scan_run_id, subject_x_user_id, event_type.value))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def evaluate_transition(
    *,
    previous: Membership | None,
    current: Membership,
    previous_state: RelationshipState | None,
    previous_non_followback_streak: int,
    scan_run_id: str,
    subject_x_user_id: str,
) -> TransitionResult:
    """Evaluate one subject after a successful, complete scan."""
    if not scan_run_id or not subject_x_user_id:
        raise ValueError("transition identity values are required")
    if previous_non_followback_streak < 0:
        raise ValueError("previous non-followback streak cannot be negative")
    if previous is None:
        if previous_state is not None or previous_non_followback_streak != 0:
            raise ValueError("first observation cannot have previous relationship state")
    elif previous_state is not previous.state:
        raise ValueError("previous membership and state are inconsistent")

    current_state = current.state
    if current_state is RelationshipState.NOT_FOLLOWING_BACK:
        streak = (
            previous_non_followback_streak + 1
            if previous_state is RelationshipState.NOT_FOLLOWING_BACK
            else 1
        )
    else:
        streak = 0

    if previous is None:
        return TransitionResult(current_state, streak, ())

    event_types = _relationship_changes(previous, current)
    if (
        previous_state is RelationshipState.MUTUAL
        and current_state is RelationshipState.NOT_FOLLOWING_BACK
    ):
        event_types.append(
            (EventCategory.ACTION_ITEM, RelationshipEventType.UNFOLLOWED_ME_AFTER_MUTUAL)
        )

    events = tuple(
        RelationshipEvent(
            category=category,
            event_type=event_type,
            dedupe_key=event_dedupe_key(scan_run_id, subject_x_user_id, event_type),
        )
        for category, event_type in event_types
    )
    return TransitionResult(current_state, streak, events)


def _relationship_changes(
    previous: Membership,
    current: Membership,
) -> list[tuple[EventCategory, RelationshipEventType]]:
    changes: list[tuple[EventCategory, RelationshipEventType]] = []
    if previous.follows_me != current.follows_me:
        changes.append(
            (
                EventCategory.RELATIONSHIP_CHANGE,
                RelationshipEventType.NEW_FOLLOWER
                if current.follows_me
                else RelationshipEventType.LOST_FOLLOWER,
            )
        )
    if previous.i_follow != current.i_follow:
        changes.append(
            (
                EventCategory.RELATIONSHIP_CHANGE,
                RelationshipEventType.NEW_FOLLOWING
                if current.i_follow
                else RelationshipEventType.REMOVED_FOLLOWING,
            )
        )
    return changes
