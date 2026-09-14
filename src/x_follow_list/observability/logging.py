from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any, Final

REDACTED: Final = "[REDACTED]"
_SENSITIVE_KEY_PARTS: Final = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "cdp_url",
    "websocket_url",
)
_MESSAGE_PATTERNS: Final = (
    re.compile(r"(?i)(bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:token|password|secret|api[_-]?key)\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)((?:cookie|authorization)\s*[=:]\s*)[^\s,;]+"),
)
_EMAIL_PATTERN: Final = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_STANDARD_RECORD_FIELDS: Final = frozenset(logging.makeLogRecord({}).__dict__)

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_scan_run_id: ContextVar[str | None] = ContextVar("scan_run_id", default=None)
_x_account_id: ContextVar[str | None] = ContextVar("x_account_id", default=None)


def _sensitive_key(key: object) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _redact_message(value: str) -> str:
    redacted = _EMAIL_PATTERN.sub("[REDACTED_EMAIL]", value)
    for pattern in _MESSAGE_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}{REDACTED}", redacted)
    return redacted


def redact_secrets(value: Any, *, parent_key: object | None = None) -> Any:
    if parent_key is not None and _sensitive_key(parent_key):
        return REDACTED
    if isinstance(value, Mapping):
        return {key: redact_secrets(item, parent_key=key) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    if isinstance(value, str):
        return _redact_message(value)
    return value


class SecretRedactionFilter(logging.Filter):
    """Redact secrets from messages, arguments, and structured logging extras."""

    def filter(self, record: logging.LogRecord) -> bool:
        rendered_message = record.getMessage()
        record.msg = _redact_message(rendered_message)
        record.args = ()
        for key, value in tuple(record.__dict__.items()):
            if key not in _STANDARD_RECORD_FIELDS:
                setattr(record, key, redact_secrets(value, parent_key=key))
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": _request_id.get(),
            "scan_run_id": _scan_run_id.get(),
            "x_account_id": _x_account_id.get(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = _redact_message(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


@contextmanager
def log_context(
    *,
    request_id: str | None = None,
    scan_run_id: str | None = None,
    x_account_id: str | None = None,
) -> Iterator[None]:
    tokens: list[tuple[ContextVar[str | None], Token[str | None]]] = []
    for variable, value in (
        (_request_id, request_id),
        (_scan_run_id, scan_run_id),
        (_x_account_id, x_account_id),
    ):
        if value is not None:
            tokens.append((variable, variable.set(value)))
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.addFilter(SecretRedactionFilter())
    handler.setFormatter(JsonFormatter())
    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(level)
