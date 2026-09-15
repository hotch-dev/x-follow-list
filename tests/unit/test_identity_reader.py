import pytest

from x_follow_list.worker.identity import (
    CurrentIdentityReader,
    IdentityPayloadError,
    parse_viewer_identity,
)


class FakeResponse:
    def __init__(self, url: str, payload: object) -> None:
        self.url = url
        self._payload = payload

    async def json(self) -> object:
        return self._payload


class FakePage:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = responses
        self._listener: object | None = None
        self.visited: str | None = None

    def on(self, event: str, listener: object) -> None:
        assert event == "response"
        self._listener = listener

    def remove_listener(self, event: str, listener: object) -> None:
        assert event == "response" and listener is self._listener
        self._listener = None

    async def goto(self, url: str, *, wait_until: str) -> None:
        assert wait_until == "domcontentloaded"
        self.visited = url
        assert callable(self._listener)
        for response in self._responses:
            self._listener(response)


def test_viewer_identity_parser_accepts_only_the_expected_viewer_shape() -> None:
    identity = parse_viewer_identity(
        {
            "data": {
                "viewer": {
                    "user_results": {
                        "result": {
                            "rest_id": "12345",
                            "legacy": {"screen_name": "alice", "name": "Alice"},
                        }
                    }
                }
            }
        }
    )

    assert identity.x_user_id == "12345"
    assert identity.username == "alice"
    assert identity.display_name == "Alice"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": {"user": {"rest_id": "someone-else"}}},
        {"data": {"viewer": {"user_results": {"result": {"rest_id": ""}}}}},
    ],
)
def test_viewer_identity_parser_rejects_ambiguous_or_incomplete_payloads(
    payload: object,
) -> None:
    with pytest.raises(IdentityPayloadError):
        parse_viewer_identity(payload)


@pytest.mark.asyncio
async def test_identity_reader_ignores_unrelated_responses_and_detaches_listener() -> None:
    viewer_payload = {
        "data": {
            "viewer": {
                "user_results": {
                    "result": {
                        "rest_id": "12345",
                        "legacy": {"screen_name": "alice", "name": "Alice"},
                    }
                }
            }
        }
    }
    page = FakePage(
        [
            FakeResponse("https://x.com/i/api/graphql/hash/UserByScreenName", {}),
            FakeResponse("https://x.com/i/api/graphql/hash/Viewer", viewer_payload),
        ]
    )

    identity = await CurrentIdentityReader("http://fixture.test/home").read_page(page)

    assert identity.x_user_id == "12345"
    assert page.visited == "http://fixture.test/home"
    assert page._listener is None
