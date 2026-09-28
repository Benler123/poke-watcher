import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app import monitor, tcg
from app.config import get_settings
from app.db import set_setting, transaction


def _fake_build_index(calls):
    def build(progress=None):
        calls.append(progress)
        with transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tcg_products(product_id, name, clean_name, group_id,"
                " group_name) VALUES (1, 'Pikachu', 'Pikachu', 10, 'Base Set')"
            )
        if progress is not None:
            progress.update(done=1, total=1, products=1)
        return 1

    return build


def test_index_due_when_empty_or_stale(app_client):
    assert monitor.seconds_until_index_due(24) == 0

    with transaction() as conn:
        conn.execute(
            "INSERT INTO tcg_products(product_id, name, clean_name, group_id, group_name)"
            " VALUES (1, 'Pikachu', 'Pikachu', 10, 'Base Set')"
        )
    assert monitor.seconds_until_index_due(24) == 0

    fresh = datetime.now(timezone.utc) - timedelta(hours=1)
    set_setting(monitor.INDEX_BUILT_AT_KEY, fresh.isoformat(timespec="seconds"))
    assert 22 * 3600 < monitor.seconds_until_index_due(24) <= 23 * 3600

    stale = datetime.now(timezone.utc) - timedelta(hours=25)
    set_setting(monitor.INDEX_BUILT_AT_KEY, stale.isoformat(timespec="seconds"))
    assert monitor.seconds_until_index_due(24) == 0


def test_index_forever_builds_then_sleeps_until_refresh(app_client, monkeypatch):
    calls = []
    sleeps = []
    monkeypatch.setattr(tcg, "build_index", _fake_build_index(calls))
    monkeypatch.setattr(get_settings(), "index_refresh_hours", 24)

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setattr(monitor.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(monitor.index_forever())

    assert len(calls) == 1
    assert tcg.index_size() == 1
    assert max(sleeps) > 23 * 3600
    stats = app_client.get("/api/health").json()["stats"]
    assert stats["indexed_products"] == 1
    assert stats["index_built_at"]


def test_index_forever_retries_after_failure(app_client, monkeypatch):
    def failing_build(progress=None):
        raise RuntimeError("tcgcsv down")

    sleeps = []
    monkeypatch.setattr(tcg, "build_index", failing_build)
    monkeypatch.setattr(get_settings(), "index_refresh_hours", 24)

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setattr(monitor.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(monitor.index_forever())

    assert monitor.INDEX_RETRY_SECONDS in sleeps
    assert monitor.status["indexing"]["error"] == "tcgcsv down"
