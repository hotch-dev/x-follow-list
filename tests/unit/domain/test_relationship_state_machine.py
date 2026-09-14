from __future__ import annotations

from itertools import product

import pytest

from x_follow_list.domain.relationships import (
    EventCategory,
    Membership,
    RelationshipEventType,
    RelationshipState,
    evaluate_transition,
    event_dedupe_key,
)

MEMBERSHIPS = {
    RelationshipState.MUTUAL: Membership(follows_me=True, i_follow=True),
    RelationshipState.NOT_FOLLOWING_BACK: Membership(follows_me=False, i_follow=True),
    RelationshipState.FOLLOWS_ME_ONLY: Membership(follows_me=True, i_follow=False),
    RelationshipState.ABSENT: Membership(follows_me=False, i_follow=False),
}


def expected_change_events(
    previous: Membership, current: Membership
) -> set[RelationshipEventType]:
    events: set[RelationshipEventType] = set()
    if previous.follows_me != current.follows_me:
        events.add(
            RelationshipEventType.NEW_FOLLOWER
            if current.follows_me
            else RelationshipEventType.LOST_FOLLOWER
        )
    if previous.i_follow != current.i_follow:
        events.add(
            RelationshipEventType.NEW_FOLLOWING
            if current.i_follow
            else RelationshipEventType.REMOVED_FOLLOWING
        )
    return events


@pytest.mark.parametrize(
    ("previous_state", "current_state"),
    list(product(RelationshipState, repeat=2)),
)
def test_all_relationship_state_transitions(
    previous_state: RelationshipState,
    current_state: RelationshipState,
) -> None:
    result = evaluate_transition(
        previous=MEMBERSHIPS[previous_state],
        current=MEMBERSHIPS[current_state],
        previous_state=previous_state,
        previous_non_followback_streak=(
            5 if previous_state is RelationshipState.NOT_FOLLOWING_BACK else 0
        ),
        scan_run_id="scan-1",
        subject_x_user_id="subject-1",
    )

    assert result.state is current_state
    change_types = {
        event.event_type
        for event in result.events
        if event.category is EventCategory.RELATIONSHIP_CHANGE
    }
    assert change_types == expected_change_events(
        MEMBERSHIPS[previous_state], MEMBERSHIPS[current_state]
    )
    action_types = {
        event.event_type
        for event in result.events
        if event.category is EventCategory.ACTION_ITEM
    }
    assert action_types == (
        {RelationshipEventType.UNFOLLOWED_ME_AFTER_MUTUAL}
        if previous_state is RelationshipState.MUTUAL
        and current_state is RelationshipState.NOT_FOLLOWING_BACK
        else set()
    )
    expected_streak = (
        6
        if previous_state is current_state is RelationshipState.NOT_FOLLOWING_BACK
        else 1
        if current_state is RelationshipState.NOT_FOLLOWING_BACK
        else 0
    )
    assert result.non_followback_streak == expected_streak


@pytest.mark.parametrize("current_state", list(RelationshipState))
def test_first_scan_builds_a_baseline_without_change_events(
    current_state: RelationshipState,
) -> None:
    result = evaluate_transition(
        previous=None,
        current=MEMBERSHIPS[current_state],
        previous_state=None,
        previous_non_followback_streak=0,
        scan_run_id="first-scan",
        subject_x_user_id="subject-1",
    )

    assert result.state is current_state
    assert result.events == ()
    assert result.non_followback_streak == (
        1 if current_state is RelationshipState.NOT_FOLLOWING_BACK else 0
    )


def test_mutual_to_one_way_following_creates_the_action_item_once() -> None:
    result = evaluate_transition(
        previous=MEMBERSHIPS[RelationshipState.MUTUAL],
        current=MEMBERSHIPS[RelationshipState.NOT_FOLLOWING_BACK],
        previous_state=RelationshipState.MUTUAL,
        previous_non_followback_streak=0,
        scan_run_id="scan-2",
        subject_x_user_id="42",
    )

    assert [event.event_type for event in result.events] == [
        RelationshipEventType.LOST_FOLLOWER,
        RelationshipEventType.UNFOLLOWED_ME_AFTER_MUTUAL,
    ]
    action_item = result.events[1]
    assert action_item.category is EventCategory.ACTION_ITEM
    assert action_item.dedupe_key == event_dedupe_key(
        "scan-2", "42", RelationshipEventType.UNFOLLOWED_ME_AFTER_MUTUAL
    )


def test_streak_resets_after_followback_or_when_i_stop_following() -> None:
    for current_state in (
        RelationshipState.MUTUAL,
        RelationshipState.FOLLOWS_ME_ONLY,
        RelationshipState.ABSENT,
    ):
        result = evaluate_transition(
            previous=MEMBERSHIPS[RelationshipState.NOT_FOLLOWING_BACK],
            current=MEMBERSHIPS[current_state],
            previous_state=RelationshipState.NOT_FOLLOWING_BACK,
            previous_non_followback_streak=9,
            scan_run_id="scan-reset",
            subject_x_user_id="subject-1",
        )
        assert result.non_followback_streak == 0


def test_event_dedupe_keys_are_stable_and_scope_every_identity_part() -> None:
    event_type = RelationshipEventType.NEW_FOLLOWER
    original = event_dedupe_key("scan", "subject", event_type)

    assert event_dedupe_key("scan", "subject", event_type) == original
    assert event_dedupe_key("other-scan", "subject", event_type) != original
    assert event_dedupe_key("scan", "other-subject", event_type) != original
    assert event_dedupe_key("scan", "subject", RelationshipEventType.LOST_FOLLOWER) != original
    with pytest.raises(ValueError):
        event_dedupe_key("", "subject", event_type)


@pytest.mark.parametrize(
    ("previous_state", "streak"),
    [(RelationshipState.MUTUAL, 0), (None, 1)],
)
def test_first_observation_rejects_stale_previous_state(
    previous_state: RelationshipState | None,
    streak: int,
) -> None:
    with pytest.raises(ValueError, match="first observation"):
        evaluate_transition(
            previous=None,
            current=MEMBERSHIPS[RelationshipState.MUTUAL],
            previous_state=previous_state,
            previous_non_followback_streak=streak,
            scan_run_id="scan",
            subject_x_user_id="subject",
        )


def test_inconsistent_previous_state_fails_closed() -> None:
    with pytest.raises(ValueError, match="previous membership"):
        evaluate_transition(
            previous=MEMBERSHIPS[RelationshipState.MUTUAL],
            current=MEMBERSHIPS[RelationshipState.MUTUAL],
            previous_state=RelationshipState.ABSENT,
            previous_non_followback_streak=0,
            scan_run_id="scan",
            subject_x_user_id="subject",
        )


@pytest.mark.parametrize(
    ("scan_run_id", "subject_x_user_id", "streak"),
    [("", "subject", 0), ("scan", "", 0), ("scan", "subject", -1)],
)
def test_invalid_transition_identity_or_streak_fails_fast(
    scan_run_id: str,
    subject_x_user_id: str,
    streak: int,
) -> None:
    with pytest.raises(ValueError):
        evaluate_transition(
            previous=None,
            current=MEMBERSHIPS[RelationshipState.MUTUAL],
            previous_state=None,
            previous_non_followback_streak=streak,
            scan_run_id=scan_run_id,
            subject_x_user_id=subject_x_user_id,
        )
