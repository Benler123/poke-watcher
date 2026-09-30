import json

import httpx
from sqlalchemy import create_engine, inspect, text

from app import db, fanatics, monitor, watches
from app.ebay import Listing
from app.notifier import build_embed
from app.rules import UNDER_MARKET, Match
from tests.test_api import StubEbay

HIT = {
    "listingUuid": "28d7a5f6-8c6e-4ee6-8135-9664e93f00e0",
    "title": "2021 Pokemon Sword & Shield Evolving Skies Alt Art Umbreon VMAX #215 PSA 10",
    "currentPrice": 1800,
    "currency": "USD",
    "allowOffers": True,
    "marketplace": "FIXED",
    "status": "Live",
    "images": {"primary": {"small": "https://cdn-vault.fanaticscollect.com/small.jpg"}},
}


def test_search_query_matches_fanatics_title_style():
    assert fanatics.search_query("Umbreon VMAX (Alternate Art Secret) 215/203") == "Umbreon VMAX 215"
    assert fanatics.search_query("Charizard ex TG03/TG30 PSA 10") == "Charizard ex TG03 PSA 10"
    assert fanatics.search_query("Prismatic Evolutions Elite Trainer Box") == (
        "Prismatic Evolutions Elite Trainer Box"
    )


def test_parse_hit_maps_listing():
    listing = fanatics.parse_hit(HIT)
    assert listing is not None
    assert listing.listing_id == f"fanatics:{HIT['listingUuid']}"
    assert listing.url == f"https://www.fanaticscollect.com/buy-now/{HIT['listingUuid']}"
    assert listing.price == 1800.0
    assert listing.best_offer and listing.buy_it_now
    assert listing.source == "fanatics"
    assert not listing.shipping_known
    assert listing.buy_now_url is None and listing.offer_url is None
    assert fanatics.parse_hit({"listingUuid": "x"}) is None


def test_client_fetches_key_then_searches_live_buy_now(app_client):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "app.fanaticscollect.com":
            return httpx.Response(
                200,
                json={"data": {"collectSearchKeyV2": {"key": "k1", "validUntil": "2099-01-01T00:00:00Z"}}},
            )
        return httpx.Response(200, json={"results": [{"hits": [HIT, {"title": "no id"}]}]})

    client = fanatics.FanaticsClient(transport=httpx.MockTransport(handler))
    listings = client.search("Umbreon VMAX 215/203 PSA 10", limit=20, max_price=2000)
    client.search("Umbreon VMAX 215/203 PSA 10")

    assert [listing.title for listing in listings] == [HIT["title"]]
    key_calls = [c for c in calls if c.url.host == "app.fanaticscollect.com"]
    searches = [c for c in calls if "algolia" in c.url.host]
    assert len(key_calls) == 1  # key is reused until it expires
    assert searches[0].headers["x-algolia-api-key"] == "k1"
    body = json.loads(searches[0].content)["requests"][0]
    assert body["indexName"] == fanatics.RECENT_INDEX
    assert body["query"] == "Umbreon VMAX 215 PSA 10"
    assert 'marketplace:"FIXED"' in body["filters"] and 'status:"Live"' in body["filters"]
    assert fanatics.ENGLISH_POKEMON in body["filters"]
    assert body["numericFilters"] == ["currentPrice<=2000.00"]


def test_rejected_key_is_dropped(app_client):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "app.fanaticscollect.com":
            return httpx.Response(
                200,
                json={"data": {"collectSearchKeyV2": {"key": "k1", "validUntil": "2099-01-01T00:00:00Z"}}},
            )
        return httpx.Response(403, json={"message": "invalid key"})

    client = fanatics.FanaticsClient(transport=httpx.MockTransport(handler))
    try:
        client.search("umbreon")
    except fanatics.FanaticsError:
        pass
    else:
        raise AssertionError("expected FanaticsError")
    assert client._key is None


def _fanatics_listing(uuid: str, price: float) -> Listing:
    return fanatics.parse_hit({**HIT, "listingUuid": uuid, "currentPrice": price})


def _watch(**overrides):
    return watches.create_watch(
        {
            "label": "Umbreon VMAX",
            "ebay_query": "umbreon vmax 215/203",
            "manual_market_price": 2000.0,
            "strict_match": False,
            **overrides,
        }
    )


def test_each_source_seeds_its_first_sweep(app_client, monkeypatch):
    monkeypatch.setattr(monitor.notifier, "send_alert", lambda *args: True)
    ebay_stub = StubEbay([Listing("e1", "Umbreon VMAX 215/203", "https://ebay.com/itm/1", 1500.0)])
    fanatics_stub = StubEbay([_fanatics_listing("f1", 1500.0)])

    # An existing watch that already seeded eBay, now gaining Fanatics.
    watch = _watch()
    watches.record_check(watch["id"], seeded=["seeded"])
    watch = watches.get_watch(watch["id"])
    ebay_stub.listings.append(Listing("e2", "Umbreon VMAX 215/203", "https://ebay.com/itm/2", 1500.0))

    alerts = monitor.check_watch(watch, ebay_stub, notify=False, fanatics_client=fanatics_stub)
    assert [a["listing_id"] for a in alerts] == ["e1", "e2"]  # eBay is live, Fanatics seeds
    watch = watches.get_watch(watch["id"])
    assert watch["fanatics_seeded"]

    fanatics_stub.listings.append(_fanatics_listing("f2", 1500.0))
    alerts = monitor.check_watch(watch, ebay_stub, notify=False, fanatics_client=fanatics_stub)
    assert [(a["listing_id"], a["source"]) for a in alerts] == [("fanatics:f2", "fanatics")]


def test_fanatics_failure_does_not_block_ebay(app_client):
    class Broken:
        def search(self, *args, **kwargs):
            raise fanatics.FanaticsError("down")

    watch = _watch()
    ebay_stub = StubEbay([Listing("e1", "Umbreon VMAX 215/203", "https://ebay.com/itm/1", 1500.0)])
    monitor.check_watch(watch, ebay_stub, notify=False, fanatics_client=Broken())

    row = watches.get_watch(watch["id"])
    assert row["seeded"] and not row["fanatics_seeded"]
    assert "Fanatics: down" in row["last_error"]


def test_sources_can_be_switched_off(app_client):
    class NoSearch:
        def search(self, *args, **kwargs):
            raise AssertionError("source is disabled")

    fanatics_stub = StubEbay([])
    watch = _watch(search_ebay=False)
    monitor.check_watch(watch, NoSearch(), notify=False, fanatics_client=fanatics_stub)
    assert fanatics_stub.queries

    none = _watch(search_ebay=False, search_fanatics=False)
    monitor.check_watch(none, NoSearch(), notify=False, fanatics_client=NoSearch())
    assert watches.get_watch(none["id"])["last_error"] == "no marketplaces selected"


def test_fanatics_embed_links_to_listing():
    listing = _fanatics_listing("f1", 1500.0)
    embed = build_embed(listing, {"label": "Umbreon"}, Match(UNDER_MARKET, 0.75), 2000.0)
    fields = {field["name"]: field["value"] for field in embed["fields"]}
    assert fields["Actions"] == f"[Buy / Make Offer on Fanatics Collect]({listing.url})"
    assert fields["Shipping"] == "Not included"
    assert "Fanatics Collect" in embed["description"]


def test_migration_adds_text_columns_with_defaults(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    db.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE alerts DROP COLUMN source"))
        conn.execute(
            text(
                "INSERT INTO alerts (watch_id, listing_id, title, url, price, total_price, "
                "market_price, pct_of_market, reason) "
                "VALUES (1, 'v1|1|0', 'old', 'https://ebay.com/itm/1', 1, 1, 1, 1, 'under_market')"
            )
        )
    db._add_missing_columns(engine)

    assert "source" in {c["name"] for c in inspect(engine).get_columns("alerts")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT source FROM alerts")).scalar() == "ebay"
