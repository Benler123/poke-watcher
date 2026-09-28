"""CRUD for watched cards and sealed products."""

from typing import Any

from sqlalchemy import func, select

from app import db
from app.db import alerts, watches

FIELDS = (
    "label",
    "ebay_query",
    "product_type",
    "product_id",
    "set_name",
    "tcgplayer_url",
    "image_url",
    "manual_market_price",
    "grade_company",
    "grade_value",
    "bin_max_pct_of_market",
    "offer_max_pct_of_market",
    "min_price",
    "max_price",
    "exclude_terms",
    "strict_match",
    "active",
)


def list_watches() -> list[dict[str, Any]]:
    alert_count = (
        select(func.count())
        .select_from(alerts)
        .where(alerts.c.watch_id == watches.c.id)
        .scalar_subquery()
        .label("alert_count")
    )
    statement = select(watches, alert_count).order_by(watches.c.created_at.desc())
    return db.fetch_all(statement)


def get_watch(watch_id: int) -> dict[str, Any] | None:
    return db.fetch_one(watches.select().where(watches.c.id == watch_id))


def create_watch(data: dict[str, Any]) -> dict[str, Any]:
    values = {field: data.get(field) for field in FIELDS if field in data}
    return db.insert_returning(watches, values)


def update_watch(watch_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    values = {field: data[field] for field in FIELDS if field in data}
    if values:
        db.execute(watches.update().where(watches.c.id == watch_id).values(**values))
    return get_watch(watch_id)


def delete_watch(watch_id: int) -> None:
    db.execute(watches.delete().where(watches.c.id == watch_id))


def record_market_price(watch_id: int, price: float | None) -> None:
    db.execute(
        watches.update()
        .where(watches.c.id == watch_id)
        .values(market_price=price, market_price_updated_at=func.now())
    )


def record_check(watch_id: int, error: str | None = None) -> None:
    db.execute(
        watches.update()
        .where(watches.c.id == watch_id)
        .values(last_checked_at=func.now(), last_error=error)
    )


def list_alerts(limit: int = 100, watch_id: int | None = None) -> list[dict[str, Any]]:
    statement = (
        select(alerts, watches.c.label.label("watch_label"))
        .join(watches, watches.c.id == alerts.c.watch_id)
        .order_by(alerts.c.created_at.desc(), alerts.c.id.desc())
        .limit(limit)
    )
    if watch_id is not None:
        statement = statement.where(alerts.c.watch_id == watch_id)
    return db.fetch_all(statement)
