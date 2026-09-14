from __future__ import annotations

import os
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class RuntimeEnvironment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseModel):
    """Validated process configuration loaded from the X_FOLLOW_LIST namespace."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    environment: RuntimeEnvironment = RuntimeEnvironment.DEVELOPMENT
    data_dir: Path = Field(default_factory=lambda: (Path.cwd() / "data").resolve())
    master_key: SecretStr | None = None
    bootstrap_token: SecretStr | None = None
    sqlite_busy_timeout_ms: int = Field(default=5000, ge=100, le=120_000)
    session_ttl_seconds: int = Field(default=43_200, ge=300, le=604_800)
    app_origin: str = "http://127.0.0.1:8000"
    log_level: str = "INFO"

    @model_validator(mode="after")
    def validate_runtime_safety(self) -> Self:
        if not self.data_dir.is_absolute():
            raise ValueError("data directory must be an absolute path")

        normalized_level = self.log_level.upper()
        if normalized_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("log level is invalid")
        object.__setattr__(self, "log_level", normalized_level)

        origin = self.app_origin.rstrip("/")
        parsed_origin = urlsplit(origin)
        if (
            parsed_origin.scheme not in {"http", "https"}
            or not parsed_origin.netloc
            or parsed_origin.path
            or parsed_origin.query
            or parsed_origin.fragment
            or parsed_origin.username is not None
        ):
            raise ValueError("app origin must be an HTTP(S) origin without path or credentials")
        object.__setattr__(self, "app_origin", origin)

        if self.environment is RuntimeEnvironment.PRODUCTION:
            secret = self.master_key.get_secret_value() if self.master_key else ""
            known_defaults = {"development-key", "change-me", "changeme"}
            if len(secret) < 32 or secret.lower() in known_defaults:
                raise ValueError("production master key must be a non-default 32+ character secret")
        return self

    @property
    def database_path(self) -> Path:
        return self.data_dir / "x-follow-list.sqlite3"

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.database_path.as_posix()}"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Self:
        source = os.environ if environ is None else environ
        field_names = {
            "X_FOLLOW_LIST_ENV": "environment",
            "X_FOLLOW_LIST_DATA_DIR": "data_dir",
            "X_FOLLOW_LIST_MASTER_KEY": "master_key",
            "X_FOLLOW_LIST_BOOTSTRAP_TOKEN": "bootstrap_token",
            "X_FOLLOW_LIST_SQLITE_BUSY_TIMEOUT_MS": "sqlite_busy_timeout_ms",
            "X_FOLLOW_LIST_SESSION_TTL_SECONDS": "session_ttl_seconds",
            "X_FOLLOW_LIST_APP_ORIGIN": "app_origin",
            "X_FOLLOW_LIST_LOG_LEVEL": "log_level",
        }
        values = {
            field_name: source[environment_name]
            for environment_name, field_name in field_names.items()
            if source.get(environment_name, "") != ""
        }
        return cls.model_validate(values)
