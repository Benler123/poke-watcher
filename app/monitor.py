"""Background polling loop: search eBay, apply rules, alert on Discord."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app import ebay, notifier, tcg, watches
from app.config import get_settings
from app.db import get_connection, transaction
from app.rules import evaluate

log = logging.getLogger(__name__)

status: dict[str, Any] = {
    "running": False,
    "last_run_at": None,
    "last_run_checked": 0,
    "last_run_alerts": 0,
    "last_error": None,
    "indexing": None,
}


def _is_new_listing(watch_id: int, listing_id: str) -> bool:
    with transaction() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO seen_listings(watch_id, listing_id) VALUES (?, ?)",
            (watch_id, listing_id),
        )
    return cursor.rowcount > 0


def resolve_market_price(watch: dict[str, Any], force: bool = False) -> float | None:
    if watch.get("manual_market_price"):
        return float(watch["manual_market_price"])
    product_id = watch.get("product_id")
    if not product_id:
        return watch.get("market_price")

    fresh_hours = get_settings().price_refresh_hours
    updated_at = watch.get("market_price_updated_at")
    if not force and updated_at and watch.get("market_price"):
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(updated_at).replace(
                tzinfo=timezone.utc
            )
            if age.total_seconds() < fresh_hours * 3600:
                return float(watch["market_price"])
        except ValueError:
            pass

    price = tcg.market_price(int(product_id), watch.get("sub_type_name"))
    watches.record_market_price(watch["id"], price)
    return price


def check_watch(watch: dict[str, Any], client: Any, notify: bool = True) -> list[dict[str, Any]]:
    market = resolve_market_price(watch)
    if not market:
        watches.record_check(watch["id"], "no market price available")
        return []

    ceiling = market * max(
        float(watch.get("bin_max_pct_of_market") or 1.0),
        float(watch.get("offer_max_pct_of_market") or 1.15),
    )
    listings = client.search(
        watch["ebay_query"], limit=get_settings().ebay_search_limit, max_price=ceiling
    )

    created: list[dict[str, Any]] = []
    for listing in listings:
        match = evaluate(listing, watch, market)
        if match is None:
            continue
        if not _is_new_listing(watch["id"], listing.listing_id):
            continue
        notified = notifier.send_alert(listing, watch, match, market) if notify else False
        with transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO alerts(watch_id, listing_id, title, url, image_url, price,"
                " shipping, total_price, currency, best_offer, market_price, pct_of_market,"
                " reason, notified) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    watch["id"],
                    listing.listing_id,
                    listing.title,
                    listing.url,
                    listing.image_url,
                    listing.price,
                    listing.shipping,
                    listing.total_price,
                    listing.currency,
                    int(listing.best_offer),
                    market,
                    match.pct_of_market,
                    match.reason,
                    int(notified),
                ),
            )
            row = conn.execute("SELECT * FROM alerts WHERE id = ?", (cursor.lastrowid,)).fetchone()
        created.append(dict(row))

    watches.record_check(watch["id"], None)
    return created


def run_once(notify: bool = True) -> dict[str, Any]:
    client = ebay.get_client()
    checked = 0
    alerts: list[dict[str, Any]] = []
    errors: list[str] = []
    for watch in watches.list_watches():
        if not watch.get("active"):
            continue
        checked += 1
        try:
            alerts.extend(check_watch(watch, client, notify=notify))
        except Exception as exc:
            log.exception("watch %s failed", watch["id"])
            errors.append(f"{watch['label']}: {exc}")
            watches.record_check(watch["id"], str(exc)[:300])

    status.update(
        last_run_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        last_run_checked=checked,
        last_run_alerts=len(alerts),
        last_error="; ".join(errors) if errors else None,
    )
    return {"checked": checked, "alerts": alerts, "errors": errors}


async def poll_forever() -> None:
    settings = get_settings()
    status["running"] = True
    try:
        while True:
            try:
                await asyncio.to_thread(run_once)
            except Exception:
                log.exception("monitor cycle failed")
            await asyncio.sleep(settings.poll_interval_seconds)
    finally:
        status["running"] = False


async def build_index_background() -> int:
    progress: dict[str, Any] = {"done": 0, "total": 0, "products": 0}
    status["indexing"] = progress
    try:
        total = await asyncio.to_thread(tcg.build_index, progress)
        progress["complete"] = True
        return total
    finally:
        progress.setdefault("complete", True)


def stats() -> dict[str, Any]:
    conn = get_connection()
    return {
        "watches": conn.execute("SELECT COUNT(*) AS n FROM watches").fetchone()["n"],
        "active_watches": conn.execute(
            "SELECT COUNT(*) AS n FROM watches WHERE active = 1"
        ).fetchone()["n"],
        "alerts": conn.execute("SELECT COUNT(*) AS n FROM alerts").fetchone()["n"],
        "indexed_products": tcg.index_size(),
    }
