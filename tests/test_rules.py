from app.ebay import Listing
from app.rules import OFFER_NEAR_MARKET, UNDER_MARKET, evaluate

WATCH = {
    "label": "Charizard",
    "bin_max_pct_of_market": 1.0,
    "offer_max_pct_of_market": 1.15,
    "exclude_terms": "proxy, lot",
    "min_price": None,
    "max_price": None,
}


def listing(**kwargs) -> Listing:
    base = {
        "listing_id": "1",
        "title": "Charizard ex 199/165 PSA ready",
        "url": "https://ebay.com/itm/1",
        "price": 100.0,
    }
    base.update(kwargs)
    return Listing(**base)


def test_bin_under_market_matches():
    match = evaluate(listing(price=80.0), WATCH, market_price=100.0)
    assert match is not None
    assert match.reason == UNDER_MARKET
    assert match.pct_of_market == 0.8


def test_shipping_counts_toward_total():
    assert evaluate(listing(price=98.0, shipping=5.0), WATCH, market_price=100.0) is None
    assert evaluate(listing(price=90.0, shipping=5.0), WATCH, market_price=100.0) is not None


def test_over_market_without_best_offer_is_ignored():
    assert evaluate(listing(price=110.0), WATCH, market_price=100.0) is None


def test_near_market_with_best_offer_matches():
    match = evaluate(listing(price=110.0, best_offer=True), WATCH, market_price=100.0)
    assert match is not None
    assert match.reason == OFFER_NEAR_MARKET


def test_best_offer_far_over_market_is_ignored():
    assert evaluate(listing(price=200.0, best_offer=True), WATCH, market_price=100.0) is None


def test_excluded_terms_filter_titles():
    assert evaluate(listing(price=10.0, title="Charizard proxy card"), WATCH, 100.0) is None


def test_price_bounds_filter_listings():
    watch = {**WATCH, "min_price": 20.0, "max_price": 50.0}
    assert evaluate(listing(price=5.0), watch, market_price=100.0) is None
    assert evaluate(listing(price=60.0), watch, market_price=100.0) is None
    assert evaluate(listing(price=30.0), watch, market_price=100.0) is not None


def test_auction_only_listings_are_ignored():
    assert evaluate(listing(price=10.0, buy_it_now=False), WATCH, market_price=100.0) is None
