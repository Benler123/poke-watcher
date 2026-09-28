from app.ebay import parse_browse_item

ITEM = {
    "itemId": "v1|123456789|0",
    "title": "Charizard ex 199/165 SIR",
    "itemWebUrl": "https://www.ebay.com/itm/123456789",
    "price": {"value": "249.99", "currency": "USD"},
    "shippingOptions": [{"shippingCost": {"value": "4.50", "currency": "USD"}}],
    "buyingOptions": ["FIXED_PRICE", "BEST_OFFER"],
    "image": {"imageUrl": "https://i.ebayimg.com/x.jpg"},
    "condition": "Ungraded",
    "seller": {"username": "cardshop"},
}


def test_parse_browse_item():
    listing = parse_browse_item(ITEM)
    assert listing.listing_id == "v1|123456789|0"
    assert listing.price == 249.99
    assert listing.shipping == 4.5
    assert listing.total_price == 254.49
    assert listing.best_offer is True
    assert listing.buy_it_now is True
    assert listing.seller == "cardshop"


def test_parse_browse_item_without_shipping_defaults_to_zero():
    listing = parse_browse_item({**ITEM, "shippingOptions": []})
    assert listing.shipping == 0.0
    assert listing.total_price == 249.99


def test_auction_listing_is_not_buy_it_now():
    listing = parse_browse_item({**ITEM, "buyingOptions": ["AUCTION"]})
    assert listing.buy_it_now is False
    assert listing.best_offer is False
