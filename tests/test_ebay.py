from app.ebay import Listing, numeric_item_id, parse_browse_item

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


def test_numeric_item_id_from_url_and_composite_id():
    assert numeric_item_id("v1|407252277498|0", "") == "407252277498"
    assert numeric_item_id("", "https://www.ebay.com/itm/charizard-ex/407252277498?hash=x") == "407252277498"
    assert numeric_item_id("407252277498") == "407252277498"
    assert numeric_item_id("", "") is None


def test_direct_action_urls():
    listing = parse_browse_item(ITEM)
    assert listing.item_id == "123456789"
    assert listing.buy_now_url == "https://www.ebay.com/atc/binctr?item=123456789&quantity=1"
    assert listing.offer_url == "https://www.ebay.com/itm/123456789?boolp=1"


def test_offer_url_absent_without_best_offer():
    listing = parse_browse_item({**ITEM, "buyingOptions": ["FIXED_PRICE"]})
    assert listing.offer_url is None
    assert listing.buy_now_url is not None


def test_action_urls_absent_without_item_id():
    listing = Listing(listing_id="abc", title="t", url="https://www.ebay.com/itm/", price=1.0)
    assert listing.buy_now_url is None
    assert listing.offer_url is None
