import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import get_settings

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    ebay_query TEXT NOT NULL,
    product_id INTEGER,
    sub_type_name TEXT,
    set_name TEXT,
    tcgplayer_url TEXT,
    image_url TEXT,
    market_price REAL,
    market_price_updated_at TEXT,
    manual_market_price REAL,
    grade_company TEXT NOT NULL DEFAULT '',
    grade_value TEXT NOT NULL DEFAULT '',
    grade_price_multiplier REAL NOT NULL DEFAULT 1.0,
    bin_max_pct_of_market REAL NOT NULL DEFAULT 1.0,
    offer_max_pct_of_market REAL NOT NULL DEFAULT 1.15,
    min_price REAL,
    max_price REAL,
    exclude_terms TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_checked_at TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS seen_listings (
    watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
    listing_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (watch_id, listing_id)
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
    listing_id TEXT NOT NULL,
    item_id TEXT,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    image_url TEXT,
    price REAL NOT NULL,
    shipping REAL NOT NULL DEFAULT 0,
    total_price REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    best_offer INTEGER NOT NULL DEFAULT 0,
    market_price REAL NOT NULL,
    pct_of_market REAL NOT NULL,
    reason TEXT NOT NULL,
    notified INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tcg_products (
    product_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    clean_name TEXT NOT NULL,
    group_id INTEGER NOT NULL,
    group_name TEXT NOT NULL,
    number TEXT,
    rarity TEXT,
    url TEXT,
    image_url TEXT
);

CREATE INDEX IF NOT EXISTS idx_tcg_products_clean_name ON tcg_products(clean_name);

CREATE TABLE IF NOT EXISTS tcg_prices (
    product_id INTEGER NOT NULL,
    sub_type_name TEXT NOT NULL,
    market_price REAL,
    low_price REAL,
    mid_price REAL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (product_id, sub_type_name)
);
"""


def _connect() -> sqlite3.Connection:
    settings = get_settings()
    path: Path = settings.database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_connection() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = get_connection()
    with conn:
        yield conn


def init_db() -> None:
    conn = get_connection()
    with conn:
        conn.executescript(SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(alerts)")}
        if "item_id" not in columns:
            conn.execute("ALTER TABLE alerts ADD COLUMN item_id TEXT")
        watch_columns = {row["name"] for row in conn.execute("PRAGMA table_info(watches)")}
        for name, definition in (
            ("grade_company", "TEXT NOT NULL DEFAULT ''"),
            ("grade_value", "TEXT NOT NULL DEFAULT ''"),
            ("grade_price_multiplier", "REAL NOT NULL DEFAULT 1.0"),
        ):
            if name not in watch_columns:
                conn.execute(f"ALTER TABLE watches ADD COLUMN {name} {definition}")


def get_setting(key: str, default: str = "") -> str:
    row = get_connection().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with transaction() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
