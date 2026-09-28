"""TCGplayer product index (card search) via the free tcgcsv.com daily dumps.

TCGplayer has no public price API. tcgcsv.com mirrors TCGplayer's category /
group / product / price data as plain JSON, updated daily.
"""

import logging
from typing import Any

import httpx
from sqlalchemy import func, select

from app import db
from app.config import get_settings
from app.db import tcg_products

log = logging.getLogger(__name__)

BASE_URL = "https://tcgcsv.com/tcgplayer"
POKEMON_CATEGORY_ID = 3

SINGLE = "single"
SEALED = "sealed"


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


def is_sealed(product: dict[str, Any]) -> bool:
    """Whether a tcgcsv product is sealed product rather than a single card.

    Singles carry a collector number and rarity in ``extendedData``; booster
    boxes, ETBs, tins and the like carry neither.
    """
    return not _extended(product, "Number") and not _extended(product, "Rarity")


def index_group(group_id: int, group_name: str) -> int:
    products = fetch_products(group_id)
    rows = [
        {
            "product_id": product["productId"],
            "name": product["name"],
            "clean_name": product.get("cleanName") or product["name"],
            "group_id": group_id,
            "group_name": group_name,
            "number": _extended(product, "Number"),
            "rarity": _extended(product, "Rarity"),
            "url": product.get("url"),
            "image_url": product.get("imageUrl"),
            "sealed": is_sealed(product),
        }
        for product in products
    ]
    db.upsert(
        tcg_products,
        rows,
        ["name", "clean_name", "group_id", "group_name", "number", "rarity", "url",
         "image_url", "sealed"],
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
    return int(db.scalar(select(func.count()).select_from(tcg_products)) or 0)


def search_products(
    query: str, limit: int = 25, product_type: str | None = None
) -> list[dict[str, Any]]:
    like = f"%{query.strip().lower()}%"
    name = func.lower(tcg_products.c.clean_name)
    full_name = func.lower(tcg_products.c.group_name + " " + tcg_products.c.clean_name)
    statement = (
        select(tcg_products)
        .where(name.like(like) | full_name.like(like))
        .order_by(func.length(tcg_products.c.clean_name))
        .limit(limit)
    )
    if product_type in (SINGLE, SEALED):
        statement = statement.where(tcg_products.c.sealed.is_(product_type == SEALED))
    return db.fetch_all(statement)


def get_product(product_id: int) -> dict[str, Any] | None:
    return db.fetch_one(select(tcg_products).where(tcg_products.c.product_id == product_id))
