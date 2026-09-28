"""TCGplayer market prices via the free tcgcsv.com daily dumps.

TCGplayer has no public price API. tcgcsv.com mirrors TCGplayer's category /
group / product / price data as plain JSON, updated daily.
"""

import logging
from typing import Any

import httpx

from app.config import get_settings
from app.db import get_connection, transaction

log = logging.getLogger(__name__)

BASE_URL = "https://tcgcsv.com/tcgplayer"
POKEMON_CATEGORY_ID = 3


def _client() -> httpx.Client:
    # tcgcsv rejects default library user agents with a 401.
    return httpx.Client(
        timeout=get_settings().request_timeout_seconds,
        headers={"User-Agent": "poke-watcher/1.0 (+https://github.com/Benler123/poke-watcher)"},
    )


def _extended(product: dict[str, Any], name: str) -> str | None:
    for entry in product.get("extendedData") or []:
        if entry.get("name") == name:
            return entry.get("value")
    return None


def fetch_groups() -> list[dict[str, Any]]:
    with _client() as client:
        response = client.get(f"{BASE_URL}/{POKEMON_CATEGORY_ID}/groups")
        response.raise_for_status()
        return response.json()["results"]


def fetch_products(group_id: int) -> list[dict[str, Any]]:
    with _client() as client:
        response = client.get(f"{BASE_URL}/{POKEMON_CATEGORY_ID}/{group_id}/products")
        response.raise_for_status()
        return response.json()["results"]


def fetch_prices(group_id: int) -> list[dict[str, Any]]:
    with _client() as client:
        response = client.get(f"{BASE_URL}/{POKEMON_CATEGORY_ID}/{group_id}/prices")
        response.raise_for_status()
        return response.json()["results"]


def index_group(group_id: int, group_name: str) -> int:
    products = fetch_products(group_id)
    rows = [
        (
            product["productId"],
            product["name"],
            product.get("cleanName") or product["name"],
            group_id,
            group_name,
            _extended(product, "Number"),
            _extended(product, "Rarity"),
            product.get("url"),
            product.get("imageUrl"),
        )
        for product in products
    ]
    with transaction() as conn:
        conn.executemany(
            "INSERT INTO tcg_products(product_id, name, clean_name, group_id, group_name,"
            " number, rarity, url, image_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(product_id) DO UPDATE SET name=excluded.name,"
            " clean_name=excluded.clean_name, group_id=excluded.group_id,"
            " group_name=excluded.group_name, number=excluded.number,"
            " rarity=excluded.rarity, url=excluded.url, image_url=excluded.image_url",
            rows,
        )
    return len(rows)


def build_index(progress: dict[str, Any] | None = None) -> int:
    """Index every Pokemon set's products. Takes a few minutes on a cold start."""
    groups = fetch_groups()
    total = 0
    for position, group in enumerate(groups, start=1):
        try:
            total += index_group(group["groupId"], group["name"])
        except httpx.HTTPError as exc:
            log.warning("failed to index group %s: %s", group["groupId"], exc)
        if progress is not None:
            progress.update(done=position, total=len(groups), products=total)
    return total


def index_size() -> int:
    row = get_connection().execute("SELECT COUNT(*) AS n FROM tcg_products").fetchone()
    return int(row["n"])


def search_products(query: str, limit: int = 25) -> list[dict[str, Any]]:
    like = f"%{query.strip().lower()}%"
    rows = get_connection().execute(
        "SELECT * FROM tcg_products WHERE lower(clean_name) LIKE ?"
        " OR lower(group_name || ' ' || clean_name) LIKE ?"
        " ORDER BY length(clean_name) LIMIT ?",
        (like, like, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def refresh_price(product_id: int) -> dict[str, float | None]:
    """Fetch and cache the latest TCGplayer prices for a product's sub types."""
    row = get_connection().execute(
        "SELECT group_id FROM tcg_products WHERE product_id = ?", (product_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"product {product_id} is not in the local index")

    prices = {
        entry["subTypeName"]: entry
        for entry in fetch_prices(row["group_id"])
        if entry["productId"] == product_id
    }
    with transaction() as conn:
        conn.executemany(
            "INSERT INTO tcg_prices(product_id, sub_type_name, market_price, low_price,"
            " mid_price, updated_at) VALUES (?, ?, ?, ?, ?, datetime('now'))"
            " ON CONFLICT(product_id, sub_type_name) DO UPDATE SET"
            " market_price=excluded.market_price, low_price=excluded.low_price,"
            " mid_price=excluded.mid_price, updated_at=excluded.updated_at",
            [
                (
                    product_id,
                    sub_type,
                    entry.get("marketPrice"),
                    entry.get("lowPrice"),
                    entry.get("midPrice"),
                )
                for sub_type, entry in prices.items()
            ],
        )
    return {sub_type: entry.get("marketPrice") for sub_type, entry in prices.items()}


def market_price(product_id: int, sub_type_name: str | None = None) -> float | None:
    prices = refresh_price(product_id)
    if not prices:
        return None
    if sub_type_name and prices.get(sub_type_name) is not None:
        return prices[sub_type_name]
    for preferred in ("Holofoil", "Normal", "1st Edition Holofoil", "Unlimited Holofoil"):
        if prices.get(preferred) is not None:
            return prices[preferred]
    values = [value for value in prices.values() if value is not None]
    return values[0] if values else None
