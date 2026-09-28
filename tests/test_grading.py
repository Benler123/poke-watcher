from app import grading
from app.ebay import Listing
from app.rules import evaluate

PSA10 = {"grade_company": "PSA", "grade_value": "10"}


def listing(title: str) -> Listing:
    return Listing(listing_id="1", title=title, url="https://ebay.com/itm/1", price=50.0)


def test_search_query_appends_grade():
    watch = {"ebay_query": "charizard ex 199", **PSA10}
    assert grading.search_query(watch) == "charizard ex 199 PSA 10"


def test_search_query_untouched_for_raw_and_ungraded_watches():
    assert grading.search_query({"ebay_query": "pikachu 58", "grade_company": "RAW"}) == "pikachu 58"
    assert grading.search_query({"ebay_query": "pikachu 58"}) == "pikachu 58"


def test_matches_accepts_grade_spelling_variants():
    for title in ("Charizard PSA 10 Gem Mint", "charizard psa10", "Charizard PSA-10"):
        assert grading.matches(title, PSA10)


def test_matches_rejects_other_grades_and_raw():
    assert not grading.matches("Charizard PSA 9", PSA10)
    assert not grading.matches("Charizard BGS 10", PSA10)
    assert not grading.matches("Charizard ex 199/165 near mint", PSA10)


def test_half_grades_are_supported():
    watch = {"grade_company": "BGS", "grade_value": "9.5"}
    assert grading.matches("Charizard BGS 9.5 Gem Mint", watch)
    assert not grading.matches("Charizard BGS 9", watch)


def test_company_only_watch_matches_any_grade_from_that_company():
    watch = {"grade_company": "CGC"}
    assert grading.matches("Charizard CGC 8.5", watch)
    assert not grading.matches("Charizard PSA 10", watch)


def test_raw_watch_rejects_slabs():
    watch = {"grade_company": "RAW"}
    assert grading.matches("Charizard ex 199/165 near mint", watch)
    assert not grading.matches("Charizard ex PSA 10", watch)
    assert not grading.matches("Charizard ex graded slab", watch)


def test_normalizers_drop_junk():
    assert grading.normalize_company("psa") == "PSA"
    assert grading.normalize_company("unknown") == ""
    assert grading.normalize_value("10.0") == "10"
    assert grading.normalize_value("9.5") == "9.5"
    assert grading.normalize_value("11") == ""
    assert grading.normalize_value("mint") == ""


def test_rules_filter_out_wrong_grade():
    watch = {**PSA10, "bin_max_pct_of_market": 1.0, "offer_max_pct_of_market": 1.15}
    assert evaluate(listing("Charizard PSA 9"), watch, market_price=100.0) is None
    assert evaluate(listing("Charizard PSA 10"), watch, market_price=100.0) is not None
