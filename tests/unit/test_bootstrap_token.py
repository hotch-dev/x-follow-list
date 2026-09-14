from pathlib import Path

import pytest
from pydantic import SecretStr

from x_follow_list.config import RuntimeEnvironment, Settings
from x_follow_list.security.bootstrap import BootstrapTokenManager


def test_bootstrap_token_is_generated_once_and_removed_when_consumed(tmp_path: Path) -> None:
    settings = Settings(environment=RuntimeEnvironment.TEST, data_dir=tmp_path)
    manager = BootstrapTokenManager(settings)

    token = manager.get_or_create()

    assert len(token) >= 32
    assert manager.get_or_create() == token
    assert manager.token_path.read_text(encoding="utf-8").strip() == token

    manager.consume()

    assert manager.token_path.exists() is False
    restarted_manager = BootstrapTokenManager(settings)
    assert restarted_manager.is_consumed is True
    with pytest.raises(RuntimeError, match="consumed"):
        restarted_manager.get_or_create()


def test_configured_bootstrap_token_is_not_written_to_disk(tmp_path: Path) -> None:
    settings = Settings(
        environment=RuntimeEnvironment.TEST,
        data_dir=tmp_path,
        bootstrap_token=SecretStr("configured-bootstrap-token"),
    )
    manager = BootstrapTokenManager(settings)

    assert manager.get_or_create() == "configured-bootstrap-token"
    assert manager.token_path.exists() is False
