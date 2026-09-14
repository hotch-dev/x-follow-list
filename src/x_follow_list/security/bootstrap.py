from __future__ import annotations

import os
import secrets

from x_follow_list.config import Settings


class BootstrapTokenManager:
    def __init__(self, settings: Settings) -> None:
        self._configured_token = settings.bootstrap_token
        self.token_path = settings.data_dir / "bootstrap-token"
        self._completion_path = settings.data_dir / ".bootstrap-complete"

    @property
    def is_consumed(self) -> bool:
        return self._completion_path.exists()

    def get_or_create(self) -> str:
        if self.is_consumed:
            raise RuntimeError("bootstrap token has already been consumed")
        if self._configured_token is not None:
            return self._configured_token.get_secret_value()
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(32)
        try:
            descriptor = os.open(
                self.token_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return self.token_path.read_text(encoding="utf-8").strip()
        with os.fdopen(descriptor, "w", encoding="utf-8") as token_file:
            token_file.write(token)
        return token

    def consume(self) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                self._completion_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        self.token_path.unlink(missing_ok=True)
