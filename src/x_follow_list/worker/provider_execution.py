from __future__ import annotations

import json
from typing import Any

from sqlalchemy.engine import RowMapping

from x_follow_list.browser.contracts import ProviderConfig


def provider_config_from_row(row: RowMapping) -> ProviderConfig:
    raw_config: Any = row["config_json"]
    if isinstance(raw_config, str):
        try:
            raw_config = json.loads(raw_config)
        except json.JSONDecodeError:
            raise ValueError("provider config contains invalid JSON") from None
    if not isinstance(raw_config, dict):
        raise TypeError("provider config must be a JSON object")
    return ProviderConfig(
        str(row["provider_code"]),
        int(row["config_version"]),
        raw_config,
        secret_ref=row["secret_ref"],
    )
