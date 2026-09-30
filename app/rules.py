"""Deal rules: decide whether a listing is worth an alert."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app import grading, identity
from app.ebay import Listing

UNDER_MARKET = "under_market"
OFFER_NEAR_MARKET = "offer_near_market"

REASON_LABELS = {
    UNDER_MARKET: "Buy It Now under market",
    OFFER_NEAR_MARKET: "Near market with Best Offer",
}


@dataclass
class Match:
    reason: str
    pct_of_market: float
    estimated_profit: float | None = None

    @property
    def label(self) -> str:
        return REASON_LABELS[self.reason]


def _excluded(title: str, exclude_terms: str) -> bool:
    lowered = title.lower()
    return any(term.strip() and term.strip().lower() in lowered for term in exclude_terms.split(","))


def estimated_profit(total: float, market_price: float, resale_fee_pct: float = 0.0) -> float:
    """What reselling at the market price nets after the resale fee, less the purchase."""
    return round(market_price * (1 - resale_fee_pct / 100) - total, 2)


def evaluate(
    listing: Listing,
    watch: Mapping[str, Any],
    market_price: float,
    product: Mapping[str, Any] | None = None,
    resale_fee_pct: float = 0.0,
) -> Match | None:
    """Return a Match when the listing should trigger an alert.

    ``product`` is the watch's tcgcsv row, used to confirm the listing is
    actually that card rather than something eBay's fuzzy search dragged in.
    A watch's ``min_profit`` applies to Buy It Now alerts; Best Offer alerts
    are about negotiating down, so they report profit at the asking price.
    """
    if market_price <= 0 or not listing.buy_it_now or listing.price <= 0:
        return None
    if _excluded(listing.title, watch.get("exclude_terms") or ""):
        return None
    if not grading.matches(listing.title, watch):
        return None
    if watch.get("strict_match", True) and not identity.matches(listing.title, product):
        return None

    total = listing.total_price
    min_price = watch.get("min_price")
    max_price = watch.get("max_price")
    if min_price is not None and total < float(min_price):
        return None
    if max_price is not None and total > float(max_price):
        return None

    pct = total / market_price
    bin_threshold = float(watch.get("bin_max_pct_of_market") or 1.0)
    offer_threshold = float(watch.get("offer_max_pct_of_market") or 1.15)

    profit = estimated_profit(total, market_price, resale_fee_pct)
    min_profit = watch.get("min_profit")
    clears_profit = min_profit is None or profit >= float(min_profit)

    if pct <= bin_threshold and clears_profit:
        return Match(UNDER_MARKET, pct, profit)
    if listing.best_offer and pct <= offer_threshold:
        return Match(OFFER_NEAR_MARKET, pct, profit)
    return None
