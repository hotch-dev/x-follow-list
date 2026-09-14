import shutil
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    """Workspace-local temp path for restricted Windows test environments."""
    path = Path.cwd() / ".test-data" / uuid4().hex
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
