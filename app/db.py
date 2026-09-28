"""Database access.

Runs on Postgres when ``DATABASE_URL`` is set (Supabase in production) and on a
local SQLite file otherwise, so tests and local runs need no server. Everything
goes through SQLAlchemy Core so the same statements work on both.
"""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    event,
    func,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.sql import Executable

from app.config import get_settings

metadata = MetaData()

watches = Table(
    "watches",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("label", Text, nullable=False),
    Column("ebay_query", Text, nullable=False),
    Column("product_type", Text, nullable=False, server_default="single"),
    Column("product_id", Integer),
    Column("set_name", Text),
    Column("tcgplayer_url", Text),
    Column("image_url", Text),
    Column("market_price", Float),
    Column("market_price_updated_at", DateTime(timezone=True)),
    Column("manual_market_price", Float),
    Column("grade_company", Text, nullable=False, server_default=""),
    Column("grade_value", Text, nullable=False, server_default=""),
    Column("bin_max_pct_of_market", Float, nullable=False, server_default="1.0"),
    Column("offer_max_pct_of_market", Float, nullable=False, server_default="1.15"),
    Column("min_price", Float),
    Column("max_price", Float),
    Column("exclude_terms", Text, nullable=False, server_default=""),
    Column("strict_match", Boolean, nullable=False, server_default=text("true")),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_checked_at", DateTime(timezone=True)),
    Column("last_error", Text),
)

seen_listings = Table(
    "seen_listings",
    metadata,
    Column("watch_id", Integer, ForeignKey("watches.id", ondelete="CASCADE"), primary_key=True),
    Column("listing_id", Text, primary_key=True),
    Column("first_seen_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

alerts = Table(
    "alerts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("watch_id", Integer, ForeignKey("watches.id", ondelete="CASCADE"), nullable=False),
    Column("listing_id", Text, nullable=False),
    Column("item_id", Text),
    Column("title", Text, nullable=False),
    Column("url", Text, nullable=False),
    Column("image_url", Text),
    Column("price", Float, nullable=False),
    Column("shipping", Float, nullable=False, server_default="0"),
    Column("total_price", Float, nullable=False),
    Column("currency", Text, nullable=False, server_default="USD"),
    Column("best_offer", Boolean, nullable=False, server_default=text("false")),
    Column("market_price", Float, nullable=False),
    Column("pct_of_market", Float, nullable=False),
    Column("reason", Text, nullable=False),
    Column("notified", Boolean, nullable=False, server_default=text("false")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

settings_table = Table(
    "settings",
    metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),
)

tcg_products = Table(
    "tcg_products",
    metadata,
    Column("product_id", Integer, primary_key=True, autoincrement=False),
    Column("name", Text, nullable=False),
    Column("clean_name", Text, nullable=False),
    Column("group_id", Integer, nullable=False),
    Column("group_name", Text, nullable=False),
    Column("number", Text),
    Column("rarity", Text),
    Column("url", Text),
    Column("image_url", Text),
    Column("sealed", Boolean, nullable=False, server_default=text("false")),
    Index("idx_tcg_products_clean_name", "clean_name"),
)

@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    url = settings.sqlalchemy_url
    kwargs: dict[str, Any] = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # Supabase's pooler runs pgbouncer in transaction mode, which cannot
        # serve server-side prepared statements.
        kwargs["connect_args"] = {"prepare_threshold": None}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


def reset_engine() -> None:
    """Drop the cached engine; used by tests that swap databases."""
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()


def is_postgres() -> bool:
    return get_engine().dialect.name == "postgresql"


@contextmanager
def transaction() -> Iterator[Connection]:
    with get_engine().begin() as conn:
        yield conn


def fetch_all(statement: Executable, conn: Connection | None = None) -> list[dict[str, Any]]:
    if conn is not None:
        return [dict(row) for row in conn.execute(statement).mappings()]
    with get_engine().connect() as connection:
        return [dict(row) for row in connection.execute(statement).mappings()]


def fetch_one(statement: Executable, conn: Connection | None = None) -> dict[str, Any] | None:
    rows = fetch_all(statement, conn)
    return rows[0] if rows else None


def scalar(statement: Executable) -> Any:
    with get_engine().connect() as conn:
        return conn.scalar(statement)


def execute(statement: Executable) -> None:
    with transaction() as conn:
        conn.execute(statement)


def _insert(table: Table) -> Any:
    return pg_insert(table) if is_postgres() else sqlite_insert(table)


def upsert(table: Table, rows: Sequence[Mapping[str, Any]], update_columns: Sequence[str]) -> None:
    """Insert rows, overwriting ``update_columns`` on primary key conflicts."""
    if not rows:
        return
    statement = _insert(table).values(list(rows))
    statement = statement.on_conflict_do_update(
        index_elements=[column.name for column in table.primary_key],
        set_={name: getattr(statement.excluded, name) for name in update_columns},
    )
    execute(statement)


def insert_if_absent(table: Table, values: Mapping[str, Any]) -> bool:
    """Insert a row unless its primary key already exists. True if inserted."""
    # RETURNING rather than rowcount: psycopg reports -1 for ON CONFLICT inserts.
    first_key = next(iter(table.primary_key.columns))
    statement = _insert(table).values(**values).on_conflict_do_nothing().returning(first_key)
    with transaction() as conn:
        return conn.execute(statement).first() is not None


def insert_returning(table: Table, values: Mapping[str, Any]) -> dict[str, Any]:
    with transaction() as conn:
        row = conn.execute(table.insert().values(**values).returning(*table.c)).mappings().one()
        return dict(row)


def init_db() -> None:
    engine = get_engine()
    metadata.create_all(engine)
    _add_missing_columns(engine)


def _add_missing_columns(engine: Engine) -> None:
    """Bring a database created by an older release up to the current schema."""
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in metadata.sorted_tables:
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} "
                ddl += column.type.compile(engine.dialect)
                if column.server_default is not None:
                    ddl += f" DEFAULT {column.server_default.arg}"  # type: ignore[union-attr]
                conn.execute(text(ddl))


def get_setting(key: str, default: str = "") -> str:
    row = fetch_one(
        settings_table.select().where(settings_table.c.key == key)
    )
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    upsert(settings_table, [{"key": key, "value": value}], ["value"])
