from __future__ import annotations

from pathlib import Path

from alembic.config import Config

from x_follow_list.config import Settings

BASELINE_REVISION = "0001_a02_baseline"
HEAD_REVISION = "0008_a11_account_versions"


def alembic_config(settings: Settings) -> Config:
    repository_root = Path(__file__).resolve().parents[3]
    config = Config(str(repository_root / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    return config
