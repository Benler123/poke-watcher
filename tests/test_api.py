from app import monitor
from app.ebay import Listing


class StubEbay:
    def __init__(self, listings):
        self.listings = listings
        self.queries = []

    def search(self, query, limit=50, max_price=None, product_type="single"):
        self.queries.append((query, max_price, product_type))
        return self.listings


def test_watch_lifecycle_and_alerting(app_client, monkeypatch):
    created = app_client.post(
        "/api/watches",
        json={
            "label": "Charizard ex SIR",
            "ebay_query": "charizard ex 199/165",
            "manual_market_price": 250.0,
            "bin_max_pct_of_market": 1.0,
            "offer_max_pct_of_market": 1.15,
        },
    )
    assert created.status_code == 201
    watch = created.json()
    assert watch["market_price"] == 250.0

    assert [w["id"] for w in app_client.get("/api/watches").json()] == [watch["id"]]

    stub = StubEbay(
        [
            Listing("1", "Charizard ex 199/165", "https://ebay.com/itm/1", 200.0),
            Listing("2", "Charizard ex 199/165 offers", "https://ebay.com/itm/2", 270.0, best_offer=True),
            Listing("3", "Charizard ex 199/165 overpriced", "https://ebay.com/itm/3", 400.0),
        ]
    )
    monkeypatch.setattr(monitor.ebay, "get_client", lambda: stub)
    monkeypatch.setattr(monitor.notifier, "send_alert", lambda *args: True)

    # First check only seeds what is already listed.
    assert app_client.post(f"/api/watches/{watch['id']}/check").json()["alerts"] == []

    stub.listings += [
        Listing("4", "Charizard ex 199/165 fresh", "https://ebay.com/itm/4", 200.0),
        Listing("5", "Charizard ex 199/165 fresh offers", "https://ebay.com/itm/5", 270.0, best_offer=True),
    ]
    result = app_client.post(f"/api/watches/{watch['id']}/check").json()
    reasons = sorted(alert["reason"] for alert in result["alerts"])
    assert reasons == ["offer_near_market", "under_market"]

    # Already-seen listings do not alert twice.
    assert app_client.post(f"/api/watches/{watch['id']}/check").json()["alerts"] == []
    assert len(app_client.get("/api/alerts").json()) == 2

    patched = app_client.patch(f"/api/watches/{watch['id']}", json={"active": False}).json()
    assert patched["active"] == 0

    assert app_client.delete(f"/api/watches/{watch['id']}").status_code == 204
    assert app_client.get("/api/watches").json() == []


def test_graded_watch_searches_and_filters_by_grade(app_client, monkeypatch):
    watch = app_client.post(
        "/api/watches",
        json={
            "label": "Charizard VMAX PSA 10",
            "ebay_query": "charizard vmax 020/189",
            "manual_market_price": 200.0,
            "grade_company": "psa",
            "grade_value": "10.0",
            "grade_price_multiplier": 4.0,
        },
    ).json()
    assert (watch["grade_company"], watch["grade_value"]) == ("PSA", "10")

    stub = StubEbay([])
    monkeypatch.setattr(monitor.ebay, "get_client", lambda: stub)
    monkeypatch.setattr(monitor.notifier, "send_alert", lambda *args: True)
    app_client.post(f"/api/watches/{watch['id']}/check")

    stub.listings = [
        Listing("1", "Charizard VMAX 020/189 PSA 10", "https://ebay.com/itm/1", 150.0),
        Listing("2", "Charizard VMAX 020/189 PSA 9", "https://ebay.com/itm/2", 150.0),
        Listing("3", "Charizard VMAX 020/189 raw NM", "https://ebay.com/itm/3", 150.0),
    ]

    alerts = app_client.post(f"/api/watches/{watch['id']}/check").json()["alerts"]
    assert [alert["listing_id"] for alert in alerts] == ["1"]
    assert stub.queries[0][0] == "charizard vmax 020/189 PSA 10"


def test_first_check_seeds_without_alerting(app_client, monkeypatch):
    """Listings that predate a watch are recorded, not alerted on."""
    watch = app_client.post(
        "/api/watches",
        json={
            "label": "Umbreon VMAX",
            "ebay_query": "umbreon vmax 215/203",
            "manual_market_price": 400.0,
        },
    ).json()

    stub = StubEbay([Listing("1", "Umbreon VMAX 215/203", "https://ebay.com/itm/1", 300.0)])
    monkeypatch.setattr(monitor.ebay, "get_client", lambda: stub)
    sent: list[str] = []

    def record(listing, *_args):
        sent.append(listing.listing_id)
        return True

    monkeypatch.setattr(monitor.notifier, "send_alert", record)

    assert app_client.post(f"/api/watches/{watch['id']}/check").json()["alerts"] == []
    assert sent == []

    stub.listings += [Listing("2", "Umbreon VMAX 215/203 new", "https://ebay.com/itm/2", 300.0)]
    alerts = app_client.post(f"/api/watches/{watch['id']}/check").json()["alerts"]

    assert [alert["listing_id"] for alert in alerts] == ["2"]
    assert sent == ["2"]


def test_settings_roundtrip(app_client):
    url = "https://discord.com/api/webhooks/123/abc"
    app_client.put("/api/settings", json={"discord_webhook_url": url})
    settings = app_client.get("/api/settings").json()
    assert settings["discord_webhook_url"] == url
    assert settings["discord_webhook_set"] is True


def test_health_reports_sources(app_client):
    health = app_client.get("/api/health").json()
    assert health["ok"] is True
    assert health["ebay_source"] in {"browse_api", "html_scrape"}
    assert health["stats"]["watches"] == 0
