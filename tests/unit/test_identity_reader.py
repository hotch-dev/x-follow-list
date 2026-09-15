import pytest

from x_follow_list.worker.identity import IdentityPayloadError, parse_viewer_identity


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
