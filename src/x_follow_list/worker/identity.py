from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from x_follow_list.browser.contracts import BrowserSession
from x_follow_list.worker.browser_binding import DetectedIdentity


class IdentityPayloadError(ValueError):
    pass


def parse_viewer_identity(payload: object) -> DetectedIdentity:
    result = _mapping_path(payload, "data", "viewer", "user_results", "result")
    x_user_id = result.get("rest_id")
    if not isinstance(x_user_id, str) or not x_user_id:
        raise IdentityPayloadError("Viewer response has no stable user ID")
    legacy = result.get("legacy")
    username: str | None = None
    display_name: str | None = None
    if isinstance(legacy, Mapping):
        raw_username = legacy.get("screen_name")
        raw_display_name = legacy.get("name")
        username = raw_username if isinstance(raw_username, str) else None
        display_name = raw_display_name if isinstance(raw_display_name, str) else None
    return DetectedIdentity(x_user_id, username, display_name)


class CurrentIdentityReader:
    def __init__(self, home_url: str = "https://x.com/home") -> None:
        self._home_url = home_url

    async def __call__(self, session: BrowserSession) -> DetectedIdentity:
        return await self.read_page(session.context.pages[0])

    async def read_page(self, page: Any) -> DetectedIdentity:
        loop = asyncio.get_running_loop()
        detected: asyncio.Future[DetectedIdentity] = loop.create_future()
        pending: set[asyncio.Task[None]] = set()

        async def inspect_response(response: Any) -> None:
            try:
                identity = parse_viewer_identity(await response.json())
            except (IdentityPayloadError, TypeError, ValueError):
                return
            if not detected.done():
                detected.set_result(identity)

        def on_response(response: Any) -> None:
            if urlsplit(str(response.url)).path.rsplit("/", 1)[-1] != "Viewer":
                return
            task = asyncio.create_task(inspect_response(response))
            pending.add(task)
            task.add_done_callback(pending.discard)

        page.on("response", on_response)
        try:
            await page.goto(self._home_url, wait_until="domcontentloaded")
            return await detected
        finally:
            page.remove_listener("response", on_response)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)


def _mapping_path(value: object, *keys: str) -> Mapping[str, object]:
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            raise IdentityPayloadError("Viewer response shape is invalid")
        current = current.get(key)
    if not isinstance(current, Mapping):
        raise IdentityPayloadError("Viewer response shape is invalid")
    return current
