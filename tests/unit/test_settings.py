from pathlib import Path

import pytest
from pydantic import ValidationError

from x_follow_list.config import RuntimeEnvironment, Settings


def test_settings_load_typed_values_from_environment(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {
            "X_FOLLOW_LIST_ENV": "test",
            "X_FOLLOW_LIST_DATA_DIR": str(tmp_path),
            "X_FOLLOW_LIST_SQLITE_BUSY_TIMEOUT_MS": "7250",
            "X_FOLLOW_LIST_SESSION_TTL_SECONDS": "7200",
            "X_FOLLOW_LIST_APP_ORIGIN": "https://monitor.example/",
            "X_FOLLOW_LIST_LOG_LEVEL": "debug",
        }
    )

    assert settings.environment is RuntimeEnvironment.TEST
    assert settings.data_dir == tmp_path
    assert settings.sqlite_busy_timeout_ms == 7250
    assert settings.session_ttl_seconds == 7200
    assert settings.app_origin == "https://monitor.example"
    assert settings.log_level == "DEBUG"
    assert settings.database_path == tmp_path / "x-follow-list.sqlite3"
    assert settings.database_url.startswith("sqlite+aiosqlite:///")


def test_production_rejects_a_missing_or_weak_master_key(tmp_path: Path) -> None:
    common = {
        "X_FOLLOW_LIST_ENV": "production",
        "X_FOLLOW_LIST_DATA_DIR": str(tmp_path),
    }

    with pytest.raises(ValidationError, match="master key"):
        Settings.from_env(common)

    with pytest.raises(ValidationError, match="master key"):
        Settings.from_env({**common, "X_FOLLOW_LIST_MASTER_KEY": "development-key"})


def test_settings_reject_invalid_values_without_silent_fallback(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings.from_env(
            {
                "X_FOLLOW_LIST_ENV": "test",
                "X_FOLLOW_LIST_DATA_DIR": str(tmp_path),
                "X_FOLLOW_LIST_SQLITE_BUSY_TIMEOUT_MS": "not-an-integer",
            }
        )
