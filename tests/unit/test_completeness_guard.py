import pytest

from x_follow_list.collector.completeness import (
    CompletenessError,
    CompletenessGuard,
)
from x_follow_list.collector.models import ObservedProfile, ParsedPage, RelationshipSide


def profile(x_user_id: str) -> ObservedProfile:
    return ObservedProfile(x_user_id=x_user_id, username=f"user-{x_user_id}")


def page(
    *ids: str,
    cursor: str | None = None,
    terminal: bool = False,
    empty_confirmed: bool = False,
    rejected: int = 0,
) -> ParsedPage:
    return ParsedPage(
        side=RelationshipSide.FOLLOWER,
        items=tuple(profile(value) for value in ids),
        next_cursor=cursor,
        terminal=terminal,
        schema_version="1",
        rejected_count=rejected,
        duplicate_count=0,
        empty_confirmed=empty_confirmed,
    )


def test_complete_pages_are_deduplicated_by_stable_id() -> None:
    result = CompletenessGuard().validate(
        [page("1", "2", cursor="next"), page("2", "3", terminal=True)]
    )

    assert [item.x_user_id for item in result.items] == ["1", "2", "3"]
    assert result.schema_versions == frozenset({"1"})


def test_collection_must_reach_an_explicit_terminal_page() -> None:
    with pytest.raises(CompletenessError) as caught:
        CompletenessGuard().validate([page("1", cursor="next")])

    assert caught.value.code == "INCOMPLETE_SUSPECTED"


def test_empty_collection_requires_explicit_empty_confirmation() -> None:
    with pytest.raises(CompletenessError):
        CompletenessGuard().validate([page(terminal=True)])

    result = CompletenessGuard().validate([page(terminal=True, empty_confirmed=True)])
    assert result.items == ()


def test_repeated_cursor_without_new_ids_fails_closed() -> None:
    pages = [
        page("1", cursor="stuck"),
        page("1", cursor="stuck"),
        page("1", cursor="stuck"),
    ]

    with pytest.raises(CompletenessError) as caught:
        CompletenessGuard().validate(pages)

    assert caught.value.code == "INCOMPLETE_SUSPECTED"


def test_two_consecutive_nonterminal_empty_payloads_signal_parser_change() -> None:
    pages = [
        page("1", cursor="a"),
        page(cursor="b"),
        page(cursor="c"),
        page("2", terminal=True),
    ]

    with pytest.raises(CompletenessError) as caught:
        CompletenessGuard().validate(pages)

    assert caught.value.code == "PARSER_CHANGED"


@pytest.mark.parametrize("rejected,accepted", [(2, 197), (11, 2000)])
def test_rejected_item_threshold_cannot_be_disabled(rejected: int, accepted: int) -> None:
    ids = tuple(str(index) for index in range(accepted))

    with pytest.raises(CompletenessError) as caught:
        CompletenessGuard().validate([page(*ids, terminal=True, rejected=rejected)])

    assert caught.value.code == "PARSER_CHANGED"


def test_exact_displayed_total_uses_twenty_or_two_percent_tolerance() -> None:
    items = tuple(str(index) for index in range(100))
    guard = CompletenessGuard()

    assert len(guard.validate([page(*items, terminal=True)], displayed_total=120).items) == 100
    with pytest.raises(CompletenessError) as caught:
        guard.validate([page(*items, terminal=True)], displayed_total=121)

    assert caught.value.code == "INCOMPLETE_SUSPECTED"


def test_large_drop_requires_a_second_identical_complete_collection() -> None:
    previous_ids = frozenset(str(index) for index in range(300))
    current_ids = tuple(str(index) for index in range(200))
    guard = CompletenessGuard()

    with pytest.raises(CompletenessError) as caught:
        guard.validate([page(*current_ids, terminal=True)], previous_ids=previous_ids)
    assert caught.value.code == "INCOMPLETE_SUSPECTED"

    confirmed = guard.validate(
        [page(*current_ids, terminal=True)],
        previous_ids=previous_ids,
        confirmed_drop_ids=frozenset(current_ids),
    )
    assert confirmed.unusual_drop_confirmed is True
