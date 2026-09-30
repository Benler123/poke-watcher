from app import tcg

SINGLE_PRODUCT = {
    "productId": 1,
    "name": "Charizard ex",
    "cleanName": "Charizard ex",
    "extendedData": [
        {"name": "Number", "value": "199/165"},
        {"name": "Rarity", "value": "Special Illustration Rare"},
    ],
}

SEALED_PRODUCT = {
    "productId": 2,
    "name": "Prismatic Evolutions Elite Trainer Box",
    "cleanName": "Prismatic Evolutions Elite Trainer Box",
    "extendedData": [],
}


def test_is_sealed_distinguishes_singles_from_sealed_product():
    assert tcg.is_sealed(SEALED_PRODUCT) is True
    assert tcg.is_sealed(SINGLE_PRODUCT) is False


def _index(monkeypatch):
    monkeypatch.setattr(tcg, "fetch_products", lambda group_id: [SINGLE_PRODUCT, SEALED_PRODUCT])
    tcg.index_group(1, "Prismatic Evolutions")


def test_search_filters_by_product_type(app_client, monkeypatch):
    _index(monkeypatch)

    sealed = tcg.search_products("prismatic", product_type=tcg.SEALED)
    singles = tcg.search_products("charizard", product_type=tcg.SINGLE)

    assert [row["product_id"] for row in sealed] == [2]
    assert [row["product_id"] for row in singles] == [1]
    assert len(tcg.search_products("prismatic")) == 2


def test_search_matches_every_word_across_name_set_and_number(app_client, monkeypatch):
    _index(monkeypatch)

    for query in ("charizard ex 199", "Charizard 199/165", "prismatic charizard", "Charizard ex - 199/165"):
        assert [row["product_id"] for row in tcg.search_products(query)] == [1], query
    assert tcg.search_products("charizard 200") == []


def test_search_endpoint_returns_only_sealed(app_client, monkeypatch):
    _index(monkeypatch)

    response = app_client.get("/api/cards/search", params={"q": "prismatic", "product_type": "sealed"})

    assert response.status_code == 200
    assert [row["name"] for row in response.json()["results"]] == [
        "Prismatic Evolutions Elite Trainer Box"
    ]


def test_sealed_watch_persists_type_and_drops_grade(app_client):
    response = app_client.post(
        "/api/watches",
        json={
            "label": "Prismatic Evolutions ETB",
            "ebay_query": "prismatic evolutions elite trainer box",
            "product_type": "sealed",
            "grade_company": "PSA",
            "grade_value": "10",
            "manual_market_price": 120.0,
        },
    )

    assert response.status_code == 201
    watch = response.json()
    assert watch["product_type"] == "sealed"
    assert watch["grade_company"] == ""
    assert watch["grade_value"] == ""


def test_sealed_watch_searches_sealed_category(app_client, monkeypatch):
    from app import ebay, monitor
    from tests.test_api import StubEbay

    app_client.post(
        "/api/watches",
        json={
            "label": "Prismatic Evolutions ETB",
            "ebay_query": "prismatic evolutions elite trainer box",
            "product_type": "sealed",
            "manual_market_price": 120.0,
        },
    )
    watch = app_client.get("/api/watches").json()[0]
    stub = StubEbay([])
    monkeypatch.setattr(ebay, "get_client", lambda: stub)

    monitor.check_watch(watch, stub)

    assert stub.queries[0][2] == "sealed"


def test_watch_defaults_to_single(app_client):
    response = app_client.post(
        "/api/watches",
        json={"label": "Charizard", "ebay_query": "charizard ex 199", "manual_market_price": 200.0},
    )
    assert response.json()["product_type"] == "single"
