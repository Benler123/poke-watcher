"""eBay listing sources.

Primary source is the official Browse API (needs an App ID / Cert ID from
developer.ebay.com). A best-effort HTML scraper is kept as a fallback for
setups without API credentials; eBay blocks most datacenter IPs, so the API
is strongly preferred.
"""

import base64
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings

log = logging.getLogger(__name__)

OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
SCRAPE_URL = "https://www.ebay.com/sch/i.html"
SCOPE = "https://api.ebay.com/oauth/api_scope"

# Direct-action URLs, matching what eBay's own item page links to.
BUY_NOW_URL = "https://www.ebay.com/atc/binctr?item={item_id}&quantity=1"
MAKE_OFFER_URL = "https://www.ebay.com/itm/{item_id}?boolp=1"

# Pokemon single cards. Narrows Browse API results away from sealed product.
CATEGORY_TRADING_CARD_SINGLES = "183454"

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class Listing:
    listing_id: str
    title: str
    url: str
    price: float
    currency: str = "USD"
    shipping: float = 0.0
    best_offer: bool = False
    buy_it_now: bool = True
    image_url: str | None = None
    condition: str | None = None
    seller: str | None = None
    item_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def total_price(self) -> float:
        return round(self.price + self.shipping, 2)

    @property
    def buy_now_url(self) -> str | None:
        """Goes straight into checkout for the Buy It Now price."""
        if not (self.item_id and self.buy_it_now):
            return None
        return BUY_NOW_URL.format(item_id=self.item_id)

    @property
    def offer_url(self) -> str | None:
        """Opens the item page with the Best Offer layer."""
        if not (self.item_id and self.best_offer):
            return None
        return MAKE_OFFER_URL.format(item_id=self.item_id)


class EbayError(RuntimeError):
    pass


class EbayBrowseClient:
    """Thin wrapper over the eBay Browse API using client-credentials OAuth."""

    def __init__(self, client_id: str, client_secret: str, marketplace: str = "EBAY_US") -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.marketplace = marketplace
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def _fetch_token(self) -> str:
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        with httpx.Client(timeout=get_settings().request_timeout_seconds) as client:
            response = client.post(
                OAUTH_URL,
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"grant_type": "client_credentials", "scope": SCOPE},
            )
        if response.status_code != 200:
            raise EbayError(f"eBay OAuth failed ({response.status_code}): {response.text[:200]}")
        payload = response.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + int(payload.get("expires_in", 7200)) - 60
        return self._token

    def token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        return self._fetch_token()

    def search(
        self,
        query: str,
        limit: int = 50,
        max_price: float | None = None,
        singles_only: bool = True,
    ) -> list[Listing]:
        filters = ["buyingOptions:{FIXED_PRICE}", "conditions:{NEW|USED}"]
        if max_price is not None:
            filters.append(f"price:[..{max_price:.2f}]")
            filters.append("priceCurrency:USD")
        params: dict[str, Any] = {
            "q": query,
            "limit": min(limit, 200),
            "sort": "newlyListed",
            "filter": ",".join(filters),
        }
        if singles_only:
            params["category_ids"] = CATEGORY_TRADING_CARD_SINGLES

        with httpx.Client(timeout=get_settings().request_timeout_seconds) as client:
            response = client.get(
                BROWSE_URL,
                params=params,
                headers={
                    "Authorization": f"Bearer {self.token()}",
                    "X-EBAY-C-MARKETPLACE-ID": self.marketplace,
                },
            )
        if response.status_code != 200:
            raise EbayError(f"Browse API error ({response.status_code}): {response.text[:300]}")
        return [parse_browse_item(item) for item in response.json().get("itemSummaries") or []]


_ITEM_ID_RE = re.compile(r"/itm/(?:[^/?]+/)?(\d{9,})")


def numeric_item_id(listing_id: str, url: str = "") -> str | None:
    """Numeric eBay item id, which the direct-action URLs need.

    Browse API ids look like ``v1|407252277498|0``; the item URL carries the
    same number.
    """
    match = _ITEM_ID_RE.search(url or "")
    if match:
        return match.group(1)
    parts = (listing_id or "").split("|")
    if len(parts) >= 2 and parts[1].isdigit():
        return parts[1]
    return listing_id if listing_id.isdigit() else None


def parse_browse_item(item: dict[str, Any]) -> Listing:
    price = float(item.get("price", {}).get("value", 0) or 0)
    currency = item.get("price", {}).get("currency", "USD")
    shipping = 0.0
    for option in item.get("shippingOptions") or []:
        cost = option.get("shippingCost", {}).get("value")
        if cost is not None:
            shipping = float(cost)
            break
    buying_options = item.get("buyingOptions") or []
    return Listing(
        listing_id=item.get("itemId", ""),
        title=item.get("title", ""),
        url=item.get("itemWebUrl", ""),
        price=price,
        currency=currency,
        shipping=shipping,
        best_offer="BEST_OFFER" in buying_options,
        buy_it_now="FIXED_PRICE" in buying_options,
        image_url=(item.get("image") or {}).get("imageUrl"),
        condition=item.get("condition"),
        seller=(item.get("seller") or {}).get("username"),
        item_id=numeric_item_id(item.get("itemId", ""), item.get("itemWebUrl", "")),
    )


_PRICE_RE = re.compile(r"[\d,]+\.\d{2}")


def _parse_price(text: str | None) -> float | None:
    if not text:
        return None
    match = _PRICE_RE.search(text.replace("$", ""))
    return float(match.group().replace(",", "")) if match else None


def parse_search_html(html: str) -> list[Listing]:
    """Parse an eBay search results page. Best effort; markup changes often."""
    soup = BeautifulSoup(html, "html.parser")
    listings: list[Listing] = []
    for node in soup.select("li.s-item, li.s-card"):
        link = node.select_one("a.s-item__link, a.su-link")
        title_node = node.select_one(".s-item__title, .su-styled-text.primary")
        price_node = node.select_one(".s-item__price, .s-card__price")
        if not link or not title_node or not price_node:
            continue
        href = link.get("href", "")
        match = re.search(r"/itm/(\d+)", href)
        if not match:
            continue
        price = _parse_price(price_node.get_text())
        if price is None:
            continue
        text = node.get_text(" ", strip=True).lower()
        shipping_node = node.select_one(".s-item__shipping, .s-card__attribute-row")
        shipping = _parse_price(shipping_node.get_text() if shipping_node else None) or 0.0
        image = node.select_one("img")
        listings.append(
            Listing(
                listing_id=match.group(1),
                title=title_node.get_text(strip=True),
                url=href.split("?")[0],
                price=price,
                shipping=shipping,
                best_offer="best offer" in text or "or best offer" in text,
                buy_it_now="buy it now" in text or "best offer" in text,
                image_url=image.get("src") if image else None,
                item_id=match.group(1),
            )
        )
    return listings


class EbayScrapeClient:
    """Fallback source that parses public search result pages."""

    def search(
        self,
        query: str,
        limit: int = 50,
        max_price: float | None = None,
        singles_only: bool = True,
    ) -> list[Listing]:
        params: dict[str, Any] = {
            "_nkw": query,
            "_sop": 10,  # newly listed
            "LH_BIN": 1,
            "_ipg": min(limit, 60),
        }
        if max_price is not None:
            params["_udhi"] = f"{max_price:.2f}"
        with httpx.Client(
            timeout=get_settings().request_timeout_seconds,
            headers=BROWSER_HEADERS,
            follow_redirects=True,
        ) as client:
            response = client.get(SCRAPE_URL, params=params)
        if response.status_code != 200:
            raise EbayError(
                f"eBay blocked the scrape request ({response.status_code}). "
                "Configure EBAY_CLIENT_ID / EBAY_CLIENT_SECRET to use the Browse API."
            )
        return parse_search_html(response.text)[:limit]


def get_client() -> EbayBrowseClient | EbayScrapeClient:
    settings = get_settings()
    if settings.ebay_configured:
        return EbayBrowseClient(
            settings.ebay_client_id, settings.ebay_client_secret, settings.ebay_marketplace
        )
    log.warning("eBay API credentials missing; falling back to HTML scraping")
    return EbayScrapeClient()
