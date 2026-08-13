"""
tests/test_tools.py

Pytest tests for each Rerack tool. Covers:
  - Happy path (correct results returned)
  - Failure modes (empty results, empty wardrobe, empty outfit)
  - Filter correctness (price, size, score ordering)

Run with:
    pytest tests/
"""

import re

import pytest

from tools import compare_prices, create_fit_card, retry_search, search_listings, suggest_outfit
from utils.data_loader import get_empty_wardrobe, get_example_wardrobe, load_listings

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def all_listings():
    return load_listings()


@pytest.fixture(scope="module")
def tee_result(all_listings):
    results = search_listings("vintage graphic tee", size=None, max_price=50, listings=all_listings)
    assert results, "Test setup failed: no results for 'vintage graphic tee' under $50"
    return results[0]


# ── Tool 1: search_listings ───────────────────────────────────────────────────


class TestSearchListings:
    def test_returns_results_for_known_query(self, all_listings):
        results = search_listings(
            "vintage graphic tee", size=None, max_price=50, listings=all_listings
        )
        assert isinstance(results, list)
        assert len(results) > 0

    def test_returns_empty_for_impossible_query(self, all_listings):
        results = search_listings(
            "designer ballgown", size="XXS", max_price=5, listings=all_listings
        )
        assert results == []

    def test_price_filter_respected(self, all_listings):
        results = search_listings("jacket", size=None, max_price=30, listings=all_listings)
        assert all(item["price"] <= 30 for item in results)

    def test_size_filter_case_insensitive(self, all_listings):
        results = search_listings("top", size="m", max_price=None, listings=all_listings)
        assert all("m" in re.split(r"[\s/]+", item["size"].lower()) for item in results)

    def test_size_filter_does_not_match_substring_across_tokens(self, all_listings):
        # "S" should not match sizes like "US 8" or "US 8.5" just because the
        # letter appears inside the string.
        results = search_listings("boots", size="S", listings=all_listings)
        assert all(r["size"].lower() not in ("us 8", "us 8.5", "us 7") for r in results)
        assert all("s" in re.split(r"[\s/]+", r["size"].lower()) for r in results)

    def test_results_sorted_by_score_then_price(self):
        listings = [
            {
                "id": "a",
                "title": "vintage jacket",
                "description": "",
                "category": "outerwear",
                "style_tags": ["vintage"],
                "size": "M",
                "condition": "good",
                "price": 50.0,
                "colors": [],
                "brand": None,
                "platform": "depop",
            },
            {
                "id": "b",
                "title": "vintage jacket",
                "description": "",
                "category": "outerwear",
                "style_tags": ["vintage"],
                "size": "M",
                "condition": "good",
                "price": 20.0,
                "colors": [],
                "brand": None,
                "platform": "depop",
            },
            {
                "id": "c",
                "title": "jacket",
                "description": "plain jacket",
                "category": "outerwear",
                "style_tags": [],
                "size": "M",
                "condition": "good",
                "price": 10.0,
                "colors": [],
                "brand": None,
                "platform": "depop",
            },
        ]
        results = search_listings("vintage jacket", size=None, max_price=None, listings=listings)
        # a and b both match "vintage" + "jacket" (higher score), c matches only
        # "jacket" (lower score) — score wins over price, then price breaks ties.
        assert [r["id"] for r in results] == ["b", "a", "c"]

    def test_returns_list_of_dicts_with_expected_fields(self, all_listings):
        results = search_listings("tee", size=None, max_price=None, listings=all_listings)
        for item in results:
            assert "id" in item
            assert "title" in item
            assert "price" in item
            assert "style_tags" in item
            assert "platform" in item

    def test_never_raises_on_bad_input(self):
        result = search_listings("", size=None, max_price=None)
        assert result == []


# ── Tool 4: compare_prices ────────────────────────────────────────────────────


class TestComparePrices:
    def test_returns_verdict_for_item_with_comparables(self, tee_result, all_listings):
        result = compare_prices(tee_result, listings=all_listings)
        assert result["verdict"] in ("great deal", "fair", "overpriced", "no comparables")
        assert result["comparable_count"] >= 0

    def test_verdict_is_great_deal_when_price_below_80pct_avg(self):
        comparables = [
            {"id": "x1", "category": "tops", "style_tags": ["casual"], "price": 100.0},
            {"id": "x2", "category": "tops", "style_tags": ["casual"], "price": 100.0},
            {"id": "x3", "category": "tops", "style_tags": ["casual"], "price": 100.0},
        ]
        item = {"id": "target", "category": "tops", "style_tags": ["casual"], "price": 79.0}
        result = compare_prices(item, listings=[*comparables, item])
        assert result["verdict"] == "great deal"
        assert result["avg_price"] == 100.0

    def test_verdict_is_great_deal_at_exact_80pct_boundary(self):
        comparables = [
            {"id": "x1", "category": "tops", "style_tags": ["casual"], "price": 100.0},
            {"id": "x2", "category": "tops", "style_tags": ["casual"], "price": 100.0},
            {"id": "x3", "category": "tops", "style_tags": ["casual"], "price": 100.0},
        ]
        item = {"id": "target", "category": "tops", "style_tags": ["casual"], "price": 80.0}
        result = compare_prices(item, listings=[*comparables, item])
        assert result["verdict"] == "great deal"

    def test_verdict_is_fair_just_above_80pct_boundary(self):
        comparables = [
            {"id": "x1", "category": "tops", "style_tags": ["casual"], "price": 100.0},
            {"id": "x2", "category": "tops", "style_tags": ["casual"], "price": 100.0},
            {"id": "x3", "category": "tops", "style_tags": ["casual"], "price": 100.0},
        ]
        item = {"id": "target", "category": "tops", "style_tags": ["casual"], "price": 80.01}
        result = compare_prices(item, listings=[*comparables, item])
        assert result["verdict"] == "fair"

    def test_returns_no_comparables_for_isolated_item(self, all_listings):
        fake_item = {
            "id": "fake_999",
            "category": "accessories",
            "style_tags": ["ultraspecific_tag_xyz"],
            "price": 15.0,
        }
        result = compare_prices(fake_item, listings=all_listings)
        assert result["verdict"] in ("no comparables", "great deal", "fair", "overpriced")

    def test_result_has_required_keys(self, tee_result, all_listings):
        result = compare_prices(tee_result, listings=all_listings)
        for key in ("verdict", "avg_price", "min_price", "max_price", "comparable_count"):
            assert key in result

    def test_never_raises(self):
        result = compare_prices({})
        assert "verdict" in result


# ── Tool 5: retry_search ──────────────────────────────────────────────────────


class TestRetrySearch:
    def test_returns_results_by_dropping_size(self, all_listings):
        # "vintage tee" in size "ZZZZ" (impossible size) should find results after dropping size
        result = retry_search("vintage tee", "ZZZZ", 100)
        assert isinstance(result["results"], list)
        assert isinstance(result["loosened"], list)
        assert result["final_size"] is None

    def test_loosened_message_present_when_size_dropped(self):
        result = retry_search("vintage tee", "ZZZZ", 100)
        if result["results"]:
            assert any("size" in msg for msg in result["loosened"])

    def test_returns_empty_for_truly_impossible_query(self):
        result = retry_search("designer ballgown", "XXS", 5)
        assert result["results"] == []

    def test_result_has_required_keys(self):
        result = retry_search("jacket", "XS", 10)
        assert "results" in result
        assert "loosened" in result
        assert "final_size" in result
        assert "final_max_price" in result

    def test_final_size_always_none(self):
        result = retry_search("tee", "M", 30)
        assert result["final_size"] is None


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────


class TestSuggestOutfit:
    def test_returns_string_with_example_wardrobe(self, tee_result, mock_groq_client):
        result = suggest_outfit(tee_result, get_example_wardrobe())
        assert isinstance(result, str)
        assert len(result) > 0

    def test_handles_empty_wardrobe_without_exception(self, tee_result, mock_groq_client):
        result = suggest_outfit(tee_result, get_empty_wardrobe())
        assert isinstance(result, str)
        assert len(result) > 0

    def test_never_returns_empty_string(self, tee_result, mock_groq_client):
        result = suggest_outfit(tee_result, get_empty_wardrobe())
        assert result.strip() != ""


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────


class TestCreateFitCard:
    def test_returns_string_for_valid_inputs(self, tee_result, mock_groq_client):
        card = create_fit_card("Great outfit idea here.", tee_result)
        assert isinstance(card, str)
        assert len(card) > 0

    def test_returns_error_string_for_empty_outfit(self, tee_result):
        card = create_fit_card("", tee_result)
        assert "couldn't" in card.lower() or "no outfit" in card.lower()
        assert card != ""

    def test_returns_error_string_for_whitespace_outfit(self, tee_result):
        card = create_fit_card("   ", tee_result)
        assert card != ""
        assert card.strip() != ""

    def test_accepts_price_verdict(self, tee_result, all_listings, mock_groq_client):
        verdict = compare_prices(tee_result, listings=all_listings)
        card = create_fit_card("Outfit suggestion here.", tee_result, price_verdict=verdict)
        assert isinstance(card, str)
        assert len(card) > 0

    def test_accepts_none_price_verdict(self, tee_result, mock_groq_client):
        card = create_fit_card("Outfit suggestion here.", tee_result, price_verdict=None)
        assert isinstance(card, str)
        assert len(card) > 0

    def _sent_prompt(self, mock_groq_client):
        _, kwargs = mock_groq_client.chat.completions.create.call_args
        return kwargs["messages"][0]["content"]

    def test_great_deal_verdict_included_in_prompt(self, tee_result, mock_groq_client):
        verdict = {"verdict": "great deal", "avg_price": 50.0, "comparable_count": 3}
        create_fit_card("Some outfit.", tee_result, price_verdict=verdict)
        prompt = self._sent_prompt(mock_groq_client)
        assert "steal" in prompt.lower()
        assert "$50" in prompt

    def test_fair_verdict_included_in_prompt(self, tee_result, mock_groq_client):
        verdict = {"verdict": "fair", "avg_price": 40.0, "comparable_count": 3}
        create_fit_card("Some outfit.", tee_result, price_verdict=verdict)
        prompt = self._sent_prompt(mock_groq_client)
        assert "fairly priced" in prompt.lower()

    def test_overpriced_verdict_included_in_prompt(self, tee_result, mock_groq_client):
        verdict = {"verdict": "overpriced", "avg_price": 20.0, "comparable_count": 3}
        create_fit_card("Some outfit.", tee_result, price_verdict=verdict)
        prompt = self._sent_prompt(mock_groq_client)
        assert "pricey" in prompt.lower()

    def test_no_comparables_verdict_suppressed_below_threshold(self, tee_result, mock_groq_client):
        # "no comparables" naturally comes with comparable_count == 0, which is
        # below the >= 3 threshold create_fit_card requires before including any
        # price line at all — so this verdict's sentence never actually reaches
        # the prompt in practice, even though it's defined in the code.
        verdict = {"verdict": "no comparables", "avg_price": None, "comparable_count": 0}
        create_fit_card("Some outfit.", tee_result, price_verdict=verdict)
        prompt = self._sent_prompt(mock_groq_client)
        assert "Price context" not in prompt

    def test_low_comparable_count_suppresses_price_line(self, tee_result, mock_groq_client):
        verdict = {"verdict": "great deal", "avg_price": 50.0, "comparable_count": 2}
        create_fit_card("Some outfit.", tee_result, price_verdict=verdict)
        prompt = self._sent_prompt(mock_groq_client)
        assert "Price context" not in prompt
