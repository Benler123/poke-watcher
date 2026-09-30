"""Background polling loops: sweep eBay and Fanatics Collect watches, alert on Discord."""

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
    "sweeps": {},
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


EBAY = "ebay"
FANATICS = fanatics.SOURCE
MARKETPLACES = (EBAY, FANATICS)
MARKETPLACE_NAMES = {EBAY: "eBay", FANATICS: "Fanatics Collect"}
MIN_POLL_SECONDS = 15
RESALE_FEE_KEY = "resale_fee_pct"


def poll_interval(marketplace: str) -> int:
    """Seconds between sweeps of one marketplace's watches (Settings, else env default)."""
    settings = get_settings()
    default = (
        settings.fanatics_poll_interval_seconds
        if marketplace == FANATICS
        else settings.poll_interval_seconds
    )
    try:
        value = int(get_setting(f"poll_interval_{marketplace}") or default)
    except ValueError:
        value = default
    return max(MIN_POLL_SECONDS, value)


def resale_fee_pct() -> float:
    try:
        value = float(get_setting(RESALE_FEE_KEY) or get_settings().resale_fee_pct)
    except ValueError:
        value = get_settings().resale_fee_pct
    return min(max(value, 0.0), 100.0)


def client_for(marketplace: str) -> Any:
    """Listing source for a marketplace; None when it is switched off."""
    if marketplace == FANATICS:
        return fanatics.get_client()
    return ebay.get_client()


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
            "estimated_profit": match.estimated_profit,
            "reason": match.reason,
            "notified": notified,
        },
    )


def check_watch(watch: dict[str, Any], client: Any, notify: bool = True) -> list[dict[str, Any]]:
    """Search the watch's marketplace with ``client`` and alert on new deals."""
    # A watch's first sweep only records what is already listed: those listings are
    # not new, and alerting on all of them floods Discord.
    seeding = not watch.get("seeded")
    market = watch.get("manual_market_price")
    if not market:
        watches.record_check(watch["id"], "no market price set — use Market price to set one")
        return []
    market = float(market)
    if client is None:
        name = MARKETPLACE_NAMES.get(watch.get("marketplace") or EBAY, "marketplace")
        watches.record_check(watch["id"], f"{name} is switched off on this server")
        return []

    ceiling = market * max(
        float(watch.get("bin_max_pct_of_market") or 1.0),
        float(watch.get("offer_max_pct_of_market") or 1.15),
    )
    listings = client.search(
        grading.search_query(watch),
        limit=get_settings().ebay_search_limit,
        max_price=ceiling,
        product_type=watch.get("product_type") or ebay.SINGLE,
    )

    product_id = watch.get("product_id")
    product = tcg.get_product(int(product_id)) if product_id else None
    # Profit estimates belong to the Fanatics flipper; eBay watches compare to market only.
    flipping = (watch.get("marketplace") or EBAY) == FANATICS
    fee = resale_fee_pct()

    created: list[dict[str, Any]] = []
    for listing in listings:
        match = evaluate(listing, watch, market, product, fee)
        if match is None:
            continue
        if not flipping:
            match.estimated_profit = None
        if not _is_new_listing(watch["id"], listing.listing_id) or seeding:
            continue
        created.append(_record_alert(listing, watch, match, market, notify))

    watches.record_check(watch["id"], None, seeded=True)
    return created


def _sweep(marketplace: str, notify: bool) -> dict[str, Any]:
    client: Any = None
    client_loaded = False
    checked = 0
    alerts: list[dict[str, Any]] = []
    errors: list[str] = []
    for watch in watches.list_watches():
        if not watch.get("active") or (watch.get("marketplace") or EBAY) != marketplace:
            continue
        checked += 1
        try:
            if not client_loaded:
                client, client_loaded = client_for(marketplace), True
            alerts.extend(check_watch(watch, client, notify=notify))
        except Exception as exc:
            log.exception("watch %s failed", watch["id"])
            errors.append(f"{watch['label']}: {exc}")
            watches.record_check(watch["id"], str(exc)[:300])

    status["sweeps"][marketplace] = {
        "last_run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "checked": checked,
        "alerts": len(alerts),
        "error": "; ".join(errors) if errors else None,
    }
    return {"checked": checked, "alerts": alerts, "errors": errors}


def run_once(notify: bool = True, marketplace: str | None = None) -> dict[str, Any]:
    """Sweep one marketplace's watches, or every marketplace when none is given."""
    checked = 0
    alerts: list[dict[str, Any]] = []
    errors: list[str] = []
    for name in [marketplace] if marketplace else MARKETPLACES:
        result = _sweep(name, notify)
        checked += result["checked"]
        alerts.extend(result["alerts"])
        errors.extend(result["errors"])

    status.update(
        last_run_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        last_run_checked=checked,
        last_run_alerts=len(alerts),
        last_error="; ".join(errors) if errors else None,
    )
    return {"checked": checked, "alerts": alerts, "errors": errors}


async def poll_forever(marketplace: str) -> None:
    """Sweep one marketplace on its own interval, so Fanatics can run faster than eBay."""
    status["running"] = True
    try:
        while True:
            try:
                await asyncio.to_thread(run_once, True, marketplace)
            except Exception:
                log.exception("%s monitor cycle failed", marketplace)
            await asyncio.sleep(poll_interval(marketplace))
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
