"""Database layer behaviour, exercised on whichever backend is configured."""

from pathlib import Path

from sqlalchemy import select

from app import db
from app.config import Settings


def test_sqlalchemy_url_falls_back_to_sqlite():
    settings = Settings(database_url="", database_path=Path("/tmp/x.db"))
    assert settings.sqlalchemy_url == "sqlite:////tmp/x.db"


def test_sqlalchemy_url_normalizes_postgres_schemes():
    for scheme in ("postgresql", "postgres", "postgresql+psycopg"):
        settings = Settings(database_url=f"{scheme}://user:pw@host:5432/postgres")
        assert settings.sqlalchemy_url == "postgresql+psycopg://user:pw@host:5432/postgres"


def test_settings_roundtrip(app_client):
    db.set_setting("discord_webhook_url", "https://example.invalid/hook")
    db.set_setting("discord_webhook_url", "https://example.invalid/hook2")
    assert db.get_setting("discord_webhook_url") == "https://example.invalid/hook2"
    assert db.get_setting("missing", "default") == "default"


def test_insert_if_absent_dedupes_seen_listings(app_client):
    watch = db.insert_returning(
        db.watches, {"label": "x", "ebay_query": "x", "product_type": "single"}
    )
    row = {"watch_id": watch["id"], "listing_id": "v1|1|0"}

    assert db.insert_if_absent(db.seen_listings, row) is True
    assert db.insert_if_absent(db.seen_listings, row) is False


def test_deleting_a_watch_cascades(app_client):
    watch = db.insert_returning(
        db.watches, {"label": "x", "ebay_query": "x", "product_type": "single"}
    )
    db.insert_if_absent(db.seen_listings, {"watch_id": watch["id"], "listing_id": "v1|1|0"})

    db.execute(db.watches.delete().where(db.watches.c.id == watch["id"]))

    assert db.fetch_all(select(db.seen_listings)) == []


def test_upsert_overwrites_listed_columns(app_client):
    row = {
        "product_id": 42,
        "name": "Booster Box",
        "clean_name": "Booster Box",
        "group_id": 1,
        "group_name": "Set",
        "sealed": True,
    }
    db.upsert(db.tcg_products, [row], ["name", "clean_name", "sealed"])
    db.upsert(db.tcg_products, [{**row, "name": "Booster Box v2"}], ["name"])

    stored = db.fetch_one(select(db.tcg_products).where(db.tcg_products.c.product_id == 42))
    assert stored is not None
    assert stored["name"] == "Booster Box v2"
    assert stored["sealed"] is True


def test_init_db_is_idempotent(app_client):
    db.init_db()
    db.init_db()
    assert db.fetch_all(select(db.watches)) == []
