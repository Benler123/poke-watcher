import json

import httpx
from sqlalchemy import create_engine, inspect, text

from app import db, fanatics, monitor, watches
from app.db import set_setting
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
            "marketplace": "fanatics",
            **overrides,
        }
    )


def test_fanatics_watch_alerts_once_with_profit(app_client, monkeypatch):
    monkeypatch.setattr(monitor.notifier, "send_alert", lambda *args: True)
    stub = StubEbay([_fanatics_listing("f1", 1500.0)])
    watch = _watch()

    alerts = monitor.check_watch(watch, stub, notify=False)
    assert monitor.check_watch(watches.get_watch(watch["id"]), stub, notify=False) == []

    assert [(a["listing_id"], a["source"]) for a in alerts] == [("fanatics:f1", "fanatics")]
    # 2000 resale less the default 6% seller fee, minus the 1500 purchase.
    assert alerts[0]["estimated_profit"] == 380.0


def test_min_profit_filters_buy_now_alerts(app_client):
    set_setting(monitor.RESALE_FEE_KEY, "0")
    stub = StubEbay([])
    watch = _watch(min_profit=300.0)
    monitor.check_watch(watch, stub, notify=False)
    stub.listings.extend([_fanatics_listing("cheap", 1650.0), _fanatics_listing("thin", 1800.0)])
    alerts = monitor.check_watch(watches.get_watch(watch["id"]), stub, notify=False)
    # "thin" clears the price threshold but only nets $200; allowOffers makes it an offer alert.
    assert [(a["listing_id"], a["reason"]) for a in alerts] == [
        ("fanatics:cheap", "under_market"),
        ("fanatics:thin", "offer_near_market"),
    ]
    assert [a["estimated_profit"] for a in alerts] == [350.0, 200.0]


def test_sweeps_are_split_by_marketplace(app_client, monkeypatch):
    ebay_stub = StubEbay([])
    fanatics_stub = StubEbay([])
    monkeypatch.setattr(
        monitor, "client_for", lambda name: fanatics_stub if name == "fanatics" else ebay_stub
    )
    _watch(label="fanatics one")
    _watch(label="ebay one", marketplace="ebay")

    assert monitor.run_once(notify=False, marketplace="fanatics")["checked"] == 1
    assert len(fanatics_stub.queries) == 1 and not ebay_stub.queries
    assert set(monitor.status["sweeps"]) >= {"fanatics"}

    assert monitor.run_once(notify=False)["checked"] == 2
    assert len(fanatics_stub.queries) == 2 and len(ebay_stub.queries) == 1


def test_disabled_fanatics_is_reported_on_the_watch(app_client):
    watch = _watch()
    assert monitor.check_watch(watch, None, notify=False) == []
    assert watches.get_watch(watch["id"])["last_error"] == (
        "Fanatics Collect is switched off on this server"
    )


def test_poll_intervals_and_fee_are_configurable(app_client):
    settings = app_client.get("/api/settings").json()
    assert settings["ebay_poll_interval_seconds"] == 300
    assert settings["fanatics_poll_interval_seconds"] == 60
    assert settings["resale_fee_pct"] == 6.0

    response = app_client.put(
        "/api/settings",
        json={"fanatics_poll_interval_seconds": 20, "resale_fee_pct": 0},
    )
    assert response.status_code == 200
    assert monitor.poll_interval("fanatics") == 20
    assert monitor.poll_interval("ebay") == 300
    assert monitor.resale_fee_pct() == 0.0
    assert app_client.get("/api/health").json()["poll_intervals"] == {"ebay": 300, "fanatics": 20}
    assert app_client.put("/api/settings", json={"fanatics_poll_interval_seconds": 5}).status_code == 422


def test_watch_api_keeps_marketplace(app_client):
    response = app_client.post(
        "/api/watches",
        json={
            "label": "Umbreon",
            "ebay_query": "umbreon vmax 215",
            "manual_market_price": 2000,
            "marketplace": "fanatics",
            "min_profit": 250,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["marketplace"] == "fanatics" and body["min_profit"] == 250
    default = app_client.post(
        "/api/watches",
        json={"label": "x", "ebay_query": "x", "manual_market_price": 1},
    ).json()
    assert default["marketplace"] == "ebay"


def test_fanatics_embed_links_to_listing():
    listing = _fanatics_listing("f1", 1500.0)
    embed = build_embed(listing, {"label": "Umbreon"}, Match(UNDER_MARKET, 0.75, 380.0), 2000.0)
    fields = {field["name"]: field["value"] for field in embed["fields"]}
    assert fields["Est. profit"] == "+$380.00"
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


def test_ebay_watch_alerts_carry_no_profit(app_client):
    stub = StubEbay([])
    watch = _watch(marketplace="ebay")
    monitor.check_watch(watch, stub, notify=False)
    stub.listings.append(_fanatics_listing("e1", 1500.0))
    alerts = monitor.check_watch(watches.get_watch(watch["id"]), stub, notify=False)
    assert [a["estimated_profit"] for a in alerts] == [None]


def test_alerts_and_runs_filter_by_marketplace(app_client, monkeypatch):
    monkeypatch.setattr(monitor, "client_for", lambda name: StubEbay([]))
    for name in ("ebay", "fanatics"):
        watch = _watch(label=name, marketplace=name)
        stub = StubEbay([])
        monitor.check_watch(watch, stub, notify=False)
        stub.listings.append(_fanatics_listing(name, 1500.0))
        monitor.check_watch(watches.get_watch(watch["id"]), stub, notify=False)

    flips = app_client.get("/api/alerts", params={"marketplace": "fanatics"}).json()
    assert [a["watch_label"] for a in flips] == ["fanatics"]
    assert len(app_client.get("/api/alerts").json()) == 2
    run = app_client.post("/api/monitor/run", params={"marketplace": "ebay"}).json()
    assert run["checked"] == 1
