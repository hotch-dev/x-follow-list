from __future__ import annotations

import pytest
from playwright.async_api import async_playwright

from x_follow_list.collector.completeness import CompletenessError
from x_follow_list.collector.models import RelationshipSide
from x_follow_list.collector.navigation import DomNavigator, NavigationError, NeedsUserActionError
from x_follow_list.collector.parser import ParserChangedError, VersionedRelationshipParser
from x_follow_list.collector.response import ResponseCollector


@pytest.mark.asyncio
async def test_local_spa_covers_success_and_fail_closed_scenarios(
    fixture_origin: str,
) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="chrome", headless=True)
        try:
            collector = ResponseCollector(VersionedRelationshipParser())

            page = await browser.new_page()
            navigator = DomNavigator(f"{fixture_origin}/profile?scenario=normal")
            result = await collector.collect(page, navigator.collect, RelationshipSide.FOLLOWER)
            assert [member.x_user_id for member in result.items] == ["101", "102", "103"]
            await page.close()

            scenarios: list[tuple[str, type[BaseException]]] = [
                ("unknown_schema", ParserChangedError),
                ("duplicate_cursor", CompletenessError),
                ("empty_payload", CompletenessError),
                ("challenge", NeedsUserActionError),
                ("disconnect", NavigationError),
            ]
            for scenario, error_type in scenarios:
                page = await browser.new_page()
                navigator = DomNavigator(
                    f"{fixture_origin}/profile?scenario={scenario}", max_pages=3
                )
                with pytest.raises(error_type):
                    await collector.collect(
                        page, navigator.collect, RelationshipSide.FOLLOWER
                    )
                await page.close()
        finally:
            await browser.close()
