from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.async_api import async_playwright

from x_follow_list.collector.completeness import CompletenessError
from x_follow_list.collector.models import RelationshipSide
from x_follow_list.collector.navigation import DomNavigator, NavigationError, NeedsUserActionError
from x_follow_list.collector.parser import ParserChangedError, VersionedRelationshipParser
from x_follow_list.collector.response import ResponseCollector

FIXTURE_HTML = (
    Path(__file__).parents[1] / "fixtures" / "local_x_app" / "index.html"
).read_bytes()


def relationship_payload(side: str, page: int, scenario: str) -> dict[str, object]:
    ids = ["101", "102"] if page == 1 else ["102", "103"]
    cursor = "page-2" if page == 1 else None
    terminal = page >= 2
    if scenario == "duplicate_cursor":
        ids, cursor, terminal = (["101"], "stuck", page >= 3)
    elif scenario == "empty_payload":
        ids, cursor, terminal = ([], f"empty-{page}", page >= 3)
    return {
        "kind": "relationship_list",
        "schema_version": "future" if scenario == "unknown_schema" else "1",
        "relationship": side,
        "items": [{"user": {"id": value, "username": f"user-{value}"}} for value in ids],
        "next_cursor": cursor,
        "terminal": terminal,
        "empty_confirmed": terminal and not ids and scenario == "normal",
        "displayed_total": 3,
    }


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/api/relationships":
            query = parse_qs(parsed.query)
            scenario = query.get("scenario", ["normal"])[0]
            page_number = int(query.get("page", ["1"])[0])
            if scenario == "disconnect" and page_number > 1:
                self.connection.close()
                return
            payload = relationship_payload(
                query.get("side", ["FOLLOWER"])[0],
                page_number,
                scenario,
            )
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(FIXTURE_HTML)))
        self.end_headers()
        self.wfile.write(FIXTURE_HTML)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture
def fixture_origin() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
