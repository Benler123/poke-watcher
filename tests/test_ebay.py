from app.ebay import Listing, delivery_context, numeric_item_id, parse_browse_item

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


def test_parse_browse_item_without_shipping_cost_is_unquoted_not_free():
    calculated = [{"shippingCostType": "CALCULATED"}]
    for options in ([], calculated):
        listing = parse_browse_item({**ITEM, "shippingOptions": options})
        assert listing.shipping is None
        assert listing.shipping_known is False
        assert listing.total_price == 249.99


def test_parse_browse_item_free_shipping():
    listing = parse_browse_item({**ITEM, "shippingOptions": [{"shippingCost": {"value": "0.00"}}]})
    assert listing.shipping == 0.0
    assert listing.shipping_known is True


def test_delivery_context_header_is_url_encoded():
    assert delivery_context("19406") == "contextualLocation=country%3DUS%2Czip%3D19406"


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


def test_sealed_search_uses_ccg_category_and_keeps_only_sealed_leaves(monkeypatch):
    import httpx

    from app import ebay

    captured: dict[str, object] = {}

    def fake_get(self, url, params=None, headers=None):
        captured["params"] = params
        payload = {
            "itemSummaries": [
                {**ITEM, "itemId": "sealed", "categories": [{"categoryId": "261044"}]},
                {**ITEM, "itemId": "single", "categories": [{"categoryId": "183454"}]},
            ]
        }
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    client = ebay.EbayBrowseClient("id", "secret")
    monkeypatch.setattr(client, "token", lambda: "token")

    listings = client.search("prismatic evolutions elite trainer box", product_type=ebay.SEALED)

    assert captured["params"]["category_ids"] == ebay.CATEGORY_CCG
    assert "conditions:{NEW}" in captured["params"]["filter"]
    assert [listing.listing_id for listing in listings] == ["sealed"]


def test_single_search_uses_singles_category(monkeypatch):
    import httpx

    from app import ebay

    captured: dict[str, object] = {}

    def fake_get(self, url, params=None, headers=None):
        captured["params"] = params
        return httpx.Response(200, json={"itemSummaries": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", fake_get)
    client = ebay.EbayBrowseClient("id", "secret")
    monkeypatch.setattr(client, "token", lambda: "token")

    client.search("charizard ex 199")

    assert captured["params"]["category_ids"] == ebay.CATEGORY_CCG_SINGLES
