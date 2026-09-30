import os
import tempfile
from pathlib import Path

import pytest

# Keep TestClient startups from building the card index against tcgcsv.com.
os.environ["INDEX_REFRESH_HOURS"] = "0"
# Keep checks from searching the live Fanatics Collect marketplace.
os.environ["FANATICS_ENABLED"] = "false"


@pytest.fixture
def app_client(monkeypatch):
    """A TestClient backed by a throwaway database and a stub eBay source.

    Uses ``TEST_DATABASE_URL`` when set so the Postgres path can be exercised,
    and a temporary SQLite file otherwise.
    """
    from fastapi.testclient import TestClient

    tmp_dir = tempfile.mkdtemp(prefix="poke-watcher-test-")
    os.environ["DATABASE_PATH"] = str(Path(tmp_dir) / "test.db")
    os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "")

    from app import db as db_module
    from app.config import get_settings

    get_settings.cache_clear()
    db_module.reset_engine()
    if os.environ["DATABASE_URL"]:
        db_module.metadata.drop_all(db_module.get_engine())

    from app.main import app

    with TestClient(app) as client:
        yield client

    get_settings.cache_clear()
    db_module.reset_engine()
    os.environ.pop("DATABASE_PATH", None)
    os.environ.pop("DATABASE_URL", None)
