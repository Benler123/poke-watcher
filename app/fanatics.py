"""Fanatics Collect marketplace listings.

The site's own marketplace search is Algolia, queried with a short-lived
secured key that its public GraphQL API hands out to anonymous visitors. We
make the same two calls: fetch a key, then search the "Recently Added" index
for live fixed-price (Buy Now) listings.
"""

import json
import logging
import re
import time
from datetime import datetime
from typing import Any

import httpx

from app.config import get_settings
from app.ebay import Listing

log = logging.getLogger(__name__)

SOURCE = "fanatics"
GRAPHQL_URL = "https://app.fanaticscollect.com/graphql"
ALGOLIA_APP_ID = "3XT9C4X62I"
ALGOLIA_URL = f"https://{ALGOLIA_APP_ID.lower()}-dsn.algolia.net/1/indexes/*/queries"
RECENT_INDEX = "prod_item_state_v1_recent_desc"
LISTING_URL = "https://www.fanaticscollect.com/buy-now/{uuid}"
ENGLISH_POKEMON = "Trading Card Games > Pokémon (English)"
SEARCH_KEY_QUERY = "query webSearchKeyQuery { collectSearchKeyV2 { key validUntil } }"
ATTRIBUTES = [
    "listingUuid",
    "title",
    "currentPrice",
    "currency",
    "allowOffers",
    "marketplace",
    "status",
    "images.primary",
    "certifiedSeller",
    "subCategory1",
]
HEADERS = {
    "User-Agent": "poke-watcher/1.0 (+https://github.com/Benler123/poke-watcher)",
    "Origin": "https://www.fanaticscollect.com",
    "Referer": "https://www.fanaticscollect.com/",
}

_DENOMINATOR = re.compile(r"\b([A-Za-z]*\d+)/[A-Za-z]*\d+\b")
_PARENTHETICAL = re.compile(r"\([^)]*\)|\[[^\]]*\]")


class FanaticsError(RuntimeError):
    pass


def search_query(query: str) -> str:
    """Adapt an eBay-style query to Fanatics titles.

    Fanatics titles carry ``#215`` rather than ``215/203`` and rarely repeat
    TCGplayer's parenthetical variant names, and Algolia requires every word,
    so both would hide real listings. Strict identity checks run afterwards.
    """
    query = _PARENTHETICAL.sub(" ", query)
    query = _DENOMINATOR.sub(r"\1", query)
    return " ".join(query.split())


def parse_hit(hit: dict[str, Any]) -> Listing | None:
    uuid = hit.get("listingUuid")
    price = hit.get("currentPrice")
    if not uuid or not price:
        return None
    image = ((hit.get("images") or {}).get("primary") or {}).get("small")
    return Listing(
        listing_id=f"{SOURCE}:{uuid}",
        title=hit.get("title") or "",
        url=LISTING_URL.format(uuid=uuid),
        price=float(price),
        currency=hit.get("currency") or "USD",
        best_offer=bool(hit.get("allowOffers")),
        buy_it_now=hit.get("marketplace") == "FIXED",
        image_url=image,
        seller=hit.get("certifiedSeller"),
        source=SOURCE,
        shipping=None,
    )


class FanaticsClient:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport
        self._key: str | None = None
        self._key_expires_at = 0.0

    def _fetch_key(self, client: httpx.Client) -> str:
        response = client.post(GRAPHQL_URL, json={"query": SEARCH_KEY_QUERY})
        if response.status_code != 200:
            raise FanaticsError(f"Fanatics search key failed ({response.status_code})")
        payload = (response.json().get("data") or {}).get("collectSearchKeyV2") or {}
        key = payload.get("key")
        if not key:
            raise FanaticsError("Fanatics returned no search key")
        expires_at = time.time() + 300
        valid_until = payload.get("validUntil")
        if valid_until:
            try:
                expires_at = datetime.fromisoformat(valid_until.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        self._key = key
        self._key_expires_at = expires_at - 60
        return key

    def _search_key(self, client: httpx.Client) -> str:
        if self._key and time.time() < self._key_expires_at:
            return self._key
        return self._fetch_key(client)

    def search(
        self,
        query: str,
        limit: int = 50,
        max_price: float | None = None,
        product_type: str = "single",
    ) -> list[Listing]:
        request: dict[str, Any] = {
            "indexName": RECENT_INDEX,
            "query": search_query(query),
            "hitsPerPage": min(limit, 100),
            "filters": (
                '(marketplace:"FIXED") AND (status:"Live") AND '
                f'(subCategory1:"{ENGLISH_POKEMON}")'
            ),
            "attributesToRetrieve": ATTRIBUTES,
            "attributesToHighlight": [],
        }
        if max_price is not None:
            request["numericFilters"] = [f"currentPrice<={max_price:.2f}"]

        with httpx.Client(
            timeout=get_settings().request_timeout_seconds,
            headers=HEADERS,
            transport=self._transport,
        ) as client:
            key = self._search_key(client)
            response = client.post(
                ALGOLIA_URL,
                headers={"X-Algolia-API-Key": key, "X-Algolia-Application-Id": ALGOLIA_APP_ID},
                content=json.dumps({"requests": [request]}),
            )
            if response.status_code in (401, 403):
                self._key = None
                raise FanaticsError(f"Fanatics search rejected the key ({response.status_code})")
        if response.status_code != 200:
            raise FanaticsError(f"Fanatics search failed ({response.status_code})")
        results = response.json().get("results") or [{}]
        listings = [parse_hit(hit) for hit in results[0].get("hits") or []]
        return [listing for listing in listings if listing is not None]


_client: FanaticsClient | None = None


def get_client() -> FanaticsClient | None:
    """Shared client so the search key is reused across sweeps; None when disabled."""
    global _client
    if not get_settings().fanatics_enabled:
        return None
    if _client is None:
        _client = FanaticsClient()
    return _client
