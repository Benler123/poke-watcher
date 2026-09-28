"""CRUD for watched cards."""

from typing import Any

from app.db import get_connection, transaction

FIELDS = (
    "label",
    "ebay_query",
    "product_id",
    "sub_type_name",
    "set_name",
    "tcgplayer_url",
    "image_url",
    "manual_market_price",
    "bin_max_pct_of_market",
    "offer_max_pct_of_market",
    "min_price",
    "max_price",
    "exclude_terms",
    "active",
)


def list_watches() -> list[dict[str, Any]]:
    rows = get_connection().execute(
        "SELECT w.*, (SELECT COUNT(*) FROM alerts a WHERE a.watch_id = w.id) AS alert_count"
        " FROM watches w ORDER BY w.created_at DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def get_watch(watch_id: int) -> dict[str, Any] | None:
    row = get_connection().execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone()
    return dict(row) if row else None


def create_watch(data: dict[str, Any]) -> dict[str, Any]:
    values = {field: data.get(field) for field in FIELDS if field in data}
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    with transaction() as conn:
        cursor = conn.execute(
            f"INSERT INTO watches({columns}) VALUES ({placeholders})", tuple(values.values())
        )
    created = get_watch(int(cursor.lastrowid))
    assert created is not None
    return created


def update_watch(watch_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    values = {field: data[field] for field in FIELDS if field in data}
    if values:
        assignments = ", ".join(f"{field} = ?" for field in values)
        with transaction() as conn:
            conn.execute(
                f"UPDATE watches SET {assignments} WHERE id = ?",
                (*values.values(), watch_id),
            )
    return get_watch(watch_id)


def delete_watch(watch_id: int) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))


def record_market_price(watch_id: int, price: float | None) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE watches SET market_price = ?, market_price_updated_at = datetime('now')"
            " WHERE id = ?",
            (price, watch_id),
        )


def record_check(watch_id: int, error: str | None = None) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE watches SET last_checked_at = datetime('now'), last_error = ? WHERE id = ?",
            (error, watch_id),
        )


def list_alerts(limit: int = 100, watch_id: int | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT a.*, w.label AS watch_label FROM alerts a"
        " JOIN watches w ON w.id = a.watch_id"
    )
    params: list[Any] = []
    if watch_id is not None:
        sql += " WHERE a.watch_id = ?"
        params.append(watch_id)
    sql += " ORDER BY a.created_at DESC, a.id DESC LIMIT ?"
    params.append(limit)
    return [dict(row) for row in get_connection().execute(sql, params).fetchall()]
