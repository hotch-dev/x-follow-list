from __future__ import annotations

from collections.abc import Sequence

from x_follow_list.collector.models import ParsedPage, ValidatedCollection


class CompletenessError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CompletenessGuard:
    """Fail closed when pagination or extracted membership integrity is uncertain."""

    def validate(
        self,
        pages: Sequence[ParsedPage],
        *,
        displayed_total: int | None = None,
        previous_ids: frozenset[str] = frozenset(),
        confirmed_drop_ids: frozenset[str] | None = None,
    ) -> ValidatedCollection:
        if not pages or not pages[-1].terminal or any(page.terminal for page in pages[:-1]):
            raise CompletenessError(
                "INCOMPLETE_SUSPECTED", "relationship pagination did not end explicitly"
            )

        members: dict[str, object] = {}
        seen_cursors: set[str] = set()
        consecutive_empty = 0
        accepted_count = 0
        rejected_count = 0
        for page in pages:
            new_ids = {item.x_user_id for item in page.items} - members.keys()
            if page.next_cursor is not None:
                if page.next_cursor in seen_cursors and not new_ids:
                    raise CompletenessError(
                        "INCOMPLETE_SUSPECTED",
                        "relationship pagination cursor repeated without progress",
                    )
                seen_cursors.add(page.next_cursor)
            if not page.items and not page.terminal:
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    raise CompletenessError(
                        "PARSER_CHANGED", "relationship responses were repeatedly empty"
                    )
            else:
                consecutive_empty = 0
            for item in page.items:
                members[item.x_user_id] = item
            accepted_count += len(page.items) + page.duplicate_count
            rejected_count += page.rejected_count

        total_seen = accepted_count + rejected_count
        if rejected_count > 10 or (
            total_seen > 0 and rejected_count / total_seen > 0.01
        ):
            raise CompletenessError(
                "PARSER_CHANGED", "too many relationship entries could not be parsed"
            )
        if not members and not pages[-1].empty_confirmed:
            raise CompletenessError(
                "INCOMPLETE_SUSPECTED", "empty relationship list was not explicitly confirmed"
            )
        if displayed_total is not None:
            tolerance = max(20, displayed_total * 0.02)
            if abs(displayed_total - len(members)) > tolerance:
                raise CompletenessError(
                    "INCOMPLETE_SUSPECTED",
                    "extracted relationship count differs from the exact displayed total",
                )

        current_ids = frozenset(members)
        unusual_drop = self._is_unusual_drop(previous_ids, current_ids)
        confirmed = unusual_drop and confirmed_drop_ids == current_ids
        if unusual_drop and not confirmed:
            raise CompletenessError(
                "INCOMPLETE_SUSPECTED",
                "relationship count dropped unusually and requires independent confirmation",
            )
        return ValidatedCollection(
            items=tuple(members.values()),  # type: ignore[arg-type]
            schema_versions=frozenset(page.schema_version for page in pages),
            unusual_drop_confirmed=confirmed,
        )

    @staticmethod
    def _is_unusual_drop(previous_ids: frozenset[str], current_ids: frozenset[str]) -> bool:
        if not previous_ids:
            return False
        decrease = len(previous_ids) - len(current_ids)
        return decrease >= 50 and decrease / len(previous_ids) > 0.20
