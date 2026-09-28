import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def app_client(monkeypatch):
    """A TestClient backed by a throwaway SQLite database and a stub eBay source."""
    from fastapi.testclient import TestClient

    tmp_dir = tempfile.mkdtemp(prefix="poke-watcher-test-")
    os.environ["DATABASE_PATH"] = str(Path(tmp_dir) / "test.db")
    os.environ["INDEX_REFRESH_HOURS"] = "0"

    from app import db as db_module
    from app.config import get_settings

    get_settings.cache_clear()
    db_module._local = type(db_module._local)()

    from app.main import app

    with TestClient(app) as client:
        yield client

    get_settings.cache_clear()
    os.environ.pop("DATABASE_PATH", None)
    os.environ.pop("INDEX_REFRESH_HOURS", None)
