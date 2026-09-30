"""Background polling loop: search eBay and Fanatics Collect, apply rules, alert on Discord."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app import db, ebay, fanatics, grading, notifier, tcg, watches
from app.config import get_settings
from app.db import alerts as alerts_table
from app.db import get_setting, seen_listings, set_setting
from app.db import watches as watches_table
from app.rules import evaluate

log = logging.getLogger(__name__)

INDEX_BUILT_AT_KEY = "tcg_index_built_at"
INDEX_RETRY_SECONDS = 600

status: dict[str, Any] = {
    "running": False,
    "last_run_at": None,
    "last_run_checked": 0,
    "last_run_alerts": 0,
    "last_error": None,
    "indexing": None,
}


def _is_new_listing(watch_id: int, listing_id: str) -> bool:
    return db.insert_if_absent(
        seen_listings, {"watch_id": watch_id, "listing_id": listing_id}
    )


def _age_seconds(value: Any) -> float | None:
    """Seconds since a timestamp, which Postgres returns aware and SQLite naive."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - value).total_seconds()


def _record_alert(
    listing: ebay.Listing, watch: dict[str, Any], match: Any, market: float, notify: bool
) -> dict[str, Any]:
    notified = notifier.send_alert(listing, watch, match, market) if notify else False
    return db.insert_returning(
        alerts_table,
        {
            "watch_id": watch["id"],
            "listing_id": listing.listing_id,
            "source": listing.source,
            "item_id": listing.item_id,
            "title": listing.title,
            "url": listing.url,
            "image_url": listing.image_url,
            "price": listing.price,
            "shipping": listing.shipping,
            "total_price": listing.total_price,
            "currency": listing.currency,
            "best_offer": listing.best_offer,
            "market_price": market,
            "pct_of_market": match.pct_of_market,
            "reason": match.reason,
            "notified": notified,
        },
    )


def check_watch(
    watch: dict[str, Any],
    client: Any,
    notify: bool = True,
    fanatics_client: Any = None,
) -> list[dict[str, Any]]:
    market = watch.get("manual_market_price")
    if not market:
        watches.record_check(watch["id"], "no market price set — use Market price to set one")
        return []
    market = float(market)

    ceiling = market * max(
        float(watch.get("bin_max_pct_of_market") or 1.0),
        float(watch.get("offer_max_pct_of_market") or 1.15),
    )
    product_id = watch.get("product_id")
    product = tcg.get_product(int(product_id)) if product_id else None

    # (name, client, column recording that this source's first sweep is done)
    sources: list[tuple[str, Any, str]] = []
    if watch.get("search_ebay", True):
        sources.append(("eBay", client, "seeded"))
    if watch.get("search_fanatics", True) and fanatics_client is not None:
        sources.append(("Fanatics", fanatics_client, "fanatics_seeded"))

    if not sources:
        watches.record_check(watch["id"], "no marketplaces selected")
        return []

    created: list[dict[str, Any]] = []
    errors: list[str] = []
    seeded: list[str] = []
    for name, source, seeded_column in sources:
        try:
            listings = source.search(
                grading.search_query(watch),
                limit=get_settings().ebay_search_limit,
                max_price=ceiling,
                product_type=watch.get("product_type") or ebay.SINGLE,
            )
        except Exception as exc:
            if len(sources) == 1:
                raise
            log.exception("%s search failed for watch %s", name, watch["id"])
            errors.append(f"{name}: {exc}")
            continue

        # A source's first sweep only records what is already listed: those
        # listings are not new, and alerting on all of them floods Discord.
        seeding = not watch.get(seeded_column)
        for listing in listings:
            match = evaluate(listing, watch, market, product)
            if match is None:
                continue
            if not _is_new_listing(watch["id"], listing.listing_id) or seeding:
                continue
            created.append(_record_alert(listing, watch, match, market, notify))
        seeded.append(seeded_column)

    watches.record_check(watch["id"], "; ".join(errors)[:300] or None, seeded=seeded)
    return created


def run_once(notify: bool = True) -> dict[str, Any]:
    client = ebay.get_client()
    fanatics_client = fanatics.get_client()
    checked = 0
    alerts: list[dict[str, Any]] = []
    errors: list[str] = []
    for watch in watches.list_watches():
        if not watch.get("active"):
            continue
        checked += 1
        try:
            alerts.extend(
                check_watch(watch, client, notify=notify, fanatics_client=fanatics_client)
            )
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


def indexing_in_progress() -> bool:
    progress = status.get("indexing")
    return bool(progress) and not progress.get("complete")


def seconds_until_index_due(refresh_hours: int) -> float:
    if tcg.index_size() == 0:
        return 0
    age = _age_seconds(get_setting(INDEX_BUILT_AT_KEY))
    if age is None:
        return 0
    return max(0.0, refresh_hours * 3600 - age)


async def build_index_background() -> int:
    progress: dict[str, Any] = {"done": 0, "total": 0, "products": 0}
    status["indexing"] = progress
    try:
        total = await asyncio.to_thread(tcg.build_index, progress)
        if total:
            set_setting(
                INDEX_BUILT_AT_KEY, datetime.now(timezone.utc).isoformat(timespec="seconds")
            )
        progress["complete"] = True
        return total
    except Exception as exc:
        progress["error"] = str(exc)[:300]
        raise
    finally:
        progress.setdefault("complete", True)


async def index_forever() -> None:
    """Build the card index when it is empty or stale, then keep it fresh."""
    refresh_hours = get_settings().index_refresh_hours
    if refresh_hours <= 0:
        return
    while True:
        if seconds_until_index_due(refresh_hours) <= 0 and not indexing_in_progress():
            try:
                total = await build_index_background()
                log.info("card index built: %s products", total)
            except Exception:
                log.exception("card index build failed")
        delay = seconds_until_index_due(refresh_hours)
        await asyncio.sleep(delay if delay > 0 else INDEX_RETRY_SECONDS)


def stats() -> dict[str, Any]:
    def count(statement: Any) -> int:
        return int(db.scalar(statement) or 0)

    return {
        "watches": count(select(func.count()).select_from(watches_table)),
        "active_watches": count(
            select(func.count()).select_from(watches_table).where(watches_table.c.active)
        ),
        "alerts": count(select(func.count()).select_from(alerts_table)),
        "indexed_products": tcg.index_size(),
        "index_built_at": get_setting(INDEX_BUILT_AT_KEY) or None,
    }
