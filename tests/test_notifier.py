from app.ebay import Listing
from app.notifier import build_embed
from app.rules import UNDER_MARKET, Match

WATCH = {"label": "Charizard ex 199/165"}


def _listing(**overrides) -> Listing:
    base = {
        "listing_id": "v1|123456789|0",
        "title": "Charizard ex 199/165 SIR",
        "url": "https://www.ebay.com/itm/123456789",
        "price": 200.0,
        "best_offer": True,
        "item_id": "123456789",
    }
    return Listing(**{**base, **overrides})


def _actions(listing: Listing) -> str:
    embed = build_embed(listing, WATCH, Match(UNDER_MARKET, 0.9), 220.0)
    return next(field["value"] for field in embed["fields"] if field["name"] == "Actions")


def test_embed_has_buy_and_offer_links():
    value = _actions(_listing())
    assert "[Buy It Now](https://www.ebay.com/atc/binctr?item=123456789&quantity=1)" in value
    assert "[Make Offer](https://www.ebay.com/itm/123456789?boolp=1)" in value


def test_embed_omits_offer_link_without_best_offer():
    value = _actions(_listing(best_offer=False))
    assert "Make Offer" not in value
    assert "Buy It Now" in value


def test_embed_keeps_canonical_listing_url():
    listing = _listing()
    embed = build_embed(listing, WATCH, Match(UNDER_MARKET, 0.9), 220.0)
    assert embed["url"] == listing.url
    assert f"[View listing]({listing.url})" in _actions(listing)
