from __future__ import annotations

from typing import Any

from x_follow_list.collector.models import RelationshipSide


class NavigationError(RuntimeError):
    """The fixture/page could not reach a trustworthy terminal state."""


class NeedsUserActionError(NavigationError):
    """Navigation encountered a challenge that must be handled by the user."""


class DomNavigator:
    def __init__(self, profile_url: str, *, max_pages: int = 1000) -> None:
        if max_pages < 1:
            raise ValueError("max pages must be positive")
        self._profile_url = profile_url
        self._max_pages = max_pages

    async def collect(self, page: Any, side: RelationshipSide) -> int | None:
        await page.goto(self._profile_url, wait_until="domcontentloaded")
        await self._raise_for_challenge(page)
        link_name = "Followers" if side is RelationshipSide.FOLLOWER else "Following"
        await page.get_by_role("link", name=link_name, exact=True).click()

        for _page_number in range(self._max_pages):
            await page.wait_for_function(
                """() => Boolean(
                    document.querySelector('[data-testid="relationship-view"]') ||
                    document.querySelector('[data-testid="challenge"]') ||
                    document.querySelector('[data-testid="load-error"]')
                )"""
            )
            await self._raise_for_challenge(page)
            if await page.locator('[data-testid="load-error"]').count():
                raise NavigationError("relationship page loading was interrupted")
            if await page.locator('[data-testid="terminal"]').count():
                total = await page.locator(
                    '[data-testid="relationship-view"]'
                ).get_attribute("data-total")
                return int(total) if total is not None and total.isdigit() else None

            load_more = page.locator('[data-testid="load-more"]')
            if not await load_more.count():
                raise NavigationError("relationship page has no terminal or continuation")
            previous = await page.locator("body").get_attribute("data-page-count")
            await load_more.click()
            await page.wait_for_function(
                """previous => (
                    document.body.dataset.pageCount !== previous ||
                    Boolean(document.querySelector('[data-testid="challenge"]')) ||
                    Boolean(document.querySelector('[data-testid="load-error"]'))
                )""",
                arg=previous,
            )
        raise NavigationError("relationship pagination exceeded its safety limit")

    @staticmethod
    async def _raise_for_challenge(page: Any) -> None:
        if await page.locator('[data-testid="challenge"]').count():
            raise NeedsUserActionError("account requires user action")
