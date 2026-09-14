import json
import logging
from io import StringIO
from typing import cast

from x_follow_list.observability.logging import (
    JsonFormatter,
    SecretRedactionFilter,
    log_context,
)


def test_secret_filter_redacts_nested_credentials_and_sensitive_urls() -> None:
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "provider request failed",
        (),
        None,
    )
    record.provider_response = {
        "token": "provider-token",
        "headers": {
            "Authorization": "Bearer bearer-value",
            "Cookie": "auth_token=cookie-value",
        },
        "cdp_url": "ws://localhost/devtools/browser/id?token=query-secret",
        "safe_count": 4,
    }

    assert SecretRedactionFilter().filter(record) is True

    rendered = json.dumps(record.__dict__["provider_response"])
    assert "provider-token" not in rendered
    assert "bearer-value" not in rendered
    assert "cookie-value" not in rendered
    assert "query-secret" not in rendered
    redacted_response = cast(dict[str, object], record.__dict__["provider_response"])
    assert redacted_response["safe_count"] == 4


def test_json_formatter_includes_correlation_context_without_leaking_secrets() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(SecretRedactionFilter())
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("x_follow_list.test.redaction")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    with log_context(request_id="req-1", scan_run_id="scan-2", x_account_id="acct-3"):
        logger.info("connected with token=%s", "plaintext-secret")

    payload = json.loads(stream.getvalue())
    assert payload["request_id"] == "req-1"
    assert payload["scan_run_id"] == "scan-2"
    assert payload["x_account_id"] == "acct-3"
    assert "plaintext-secret" not in payload["message"]


def test_filter_redacts_email_addresses_from_messages() -> None:
    record = logging.LogRecord(
        "test", logging.INFO, __file__, 1, "login failed for owner@example.test", (), None
    )

    SecretRedactionFilter().filter(record)

    assert "owner@example.test" not in record.getMessage()
