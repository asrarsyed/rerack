"""
tests/test_agent.py

Tests for the Rerack planning loop: session initialization, query parsing,
and the full run_agent() control flow (happy path, retry path, failure path).
"""

from unittest.mock import patch

from agent import _new_session, _parse_query, run_agent
from utils.data_loader import get_empty_wardrobe, get_example_wardrobe, load_listings

# ── _new_session ──────────────────────────────────────────────────────────────


class TestNewSession:
    def test_has_all_expected_keys_with_initial_values(self):
        session = _new_session("some query", {"items": []})
        assert session["query"] == "some query"
        assert session["wardrobe"] == {"items": []}
        assert session["parsed"] == {}
        assert session["all_listings_cache"] is None
        assert session["search_results"] == []
        assert session["retry_attempted"] is False
        assert session["retry_loosened"] == []
        assert session["selected_item"] is None
        assert session["price_verdict"] is None
        assert session["outfit_suggestion"] is None
        assert session["fit_card"] is None
        assert session["error"] is None

    def test_stores_query_and_wardrobe_unmodified(self):
        wardrobe = {"items": [{"name": "jeans"}]}
        session = _new_session("vintage tee", wardrobe)
        assert session["query"] == "vintage tee"
        assert session["wardrobe"] is wardrobe


# ── _parse_query ──────────────────────────────────────────────────────────────


class TestParseQuery:
    def test_extracts_price_with_under_keyword(self):
        parsed = _parse_query("vintage tee under $30")
        assert parsed["max_price"] == 30.0
        assert parsed["size"] is None

    def test_extracts_price_with_dollar_only(self):
        parsed = _parse_query("tee $25")
        assert parsed["max_price"] == 25.0

    def test_extracts_price_with_below(self):
        parsed = _parse_query("tee below $40")
        assert parsed["max_price"] == 40.0

    def test_extracts_price_with_less_than(self):
        parsed = _parse_query("tee less than $40")
        assert parsed["max_price"] == 40.0

    def test_extracts_letter_size(self):
        parsed = _parse_query("graphic tee M")
        assert parsed["size"] == "M"

    def test_extracts_us_shoe_size(self):
        parsed = _parse_query("US 8 sneakers")
        assert parsed["size"] == "US 8"

    def test_extracts_waist_inseam_size(self):
        parsed = _parse_query("W32 L30 jeans")
        assert parsed["size"] == "W32 L30"

    def test_no_price_or_size_returns_none_for_both(self):
        parsed = _parse_query("vintage graphic tee")
        assert parsed["size"] is None
        assert parsed["max_price"] is None
        assert parsed["description"] == "vintage graphic tee"

    def test_description_lowercased_and_whitespace_collapsed(self):
        parsed = _parse_query("Vintage   Graphic   TEE")
        assert parsed["description"] == "vintage graphic tee"

    def test_strips_filler_phrase_looking_for(self):
        parsed = _parse_query("looking for a graphic tee")
        assert "looking for" not in parsed["description"]
        assert "graphic tee" in parsed["description"]

    def test_query_that_is_entirely_size_falls_back_to_original(self):
        # "size M" is consumed entirely by the size regex, leaving an empty
        # description — the fallback re-uses the lowercased original query
        # rather than returning an empty string.
        parsed = _parse_query("size M")
        assert parsed["size"] == "size M"
        assert parsed["description"] == "size m"

    def test_contraction_im_is_misparsed_as_size_m(self):
        # Known parser quirk: the bare-letter size alternative ([SML]) matches
        # the trailing "m" in "i'm", so queries starting with "I'm looking for"
        # incorrectly populate size="m" instead of leaving it None. Documented
        # here as a known limitation, not a passing contract.
        parsed = _parse_query("i'm looking for a graphic tee")
        assert parsed["size"] == "m"


# ── run_agent: happy path ─────────────────────────────────────────────────────


class TestRunAgentHappyPath:
    def test_populates_all_fields_with_example_wardrobe(self, mock_groq_client):
        session = run_agent(
            query="vintage graphic tee under $30",
            wardrobe=get_example_wardrobe(),
        )
        assert session["error"] is None
        assert session["selected_item"] is not None
        assert session["price_verdict"] is not None
        assert isinstance(session["outfit_suggestion"], str)
        assert len(session["outfit_suggestion"]) > 0
        assert isinstance(session["fit_card"], str)
        assert len(session["fit_card"]) > 0
        assert session["retry_attempted"] is False

    def test_populates_all_fields_with_empty_wardrobe(self, mock_groq_client):
        session = run_agent(
            query="90s track jacket size M",
            wardrobe=get_empty_wardrobe(),
        )
        assert session["error"] is None
        assert isinstance(session["outfit_suggestion"], str)
        assert len(session["outfit_suggestion"]) > 0


# ── run_agent: retry path ─────────────────────────────────────────────────────


class TestRunAgentRetryPath:
    def test_retry_sets_flags_and_still_finds_results(self, mock_groq_client):
        # "XXS" is a size the regex recognizes, but no listing in the dataset
        # is sized XXS — so the primary search comes back empty and retry
        # (dropping size) succeeds.
        session = run_agent(
            query="vintage tee size XXS",
            wardrobe=get_example_wardrobe(),
        )
        assert session["error"] is None
        assert session["retry_attempted"] is True
        assert len(session["retry_loosened"]) > 0
        assert session["selected_item"] is not None


# ── run_agent: failure path ───────────────────────────────────────────────────


class TestRunAgentFailurePath:
    def test_full_failure_sets_error_and_leaves_downstream_fields_none(self, mock_groq_client):
        session = run_agent(
            query="designer ballgown size XXS under $5",
            wardrobe=get_example_wardrobe(),
        )
        assert session["error"] is not None
        assert "designer ballgown" in session["error"]
        assert session["selected_item"] is None
        assert session["price_verdict"] is None
        assert session["outfit_suggestion"] is None
        assert session["fit_card"] is None

    def test_failure_path_never_calls_groq(self, mock_groq_client):
        run_agent(
            query="designer ballgown size XXS under $5",
            wardrobe=get_example_wardrobe(),
        )
        assert mock_groq_client.chat.completions.create.call_count == 0


# ── run_agent: listings cache ─────────────────────────────────────────────────


class TestRunAgentCaching:
    def test_all_listings_cache_populated_and_loaded_once(self, mock_groq_client):
        real_listings = load_listings()
        with patch("agent.load_listings", return_value=real_listings) as mock_load:
            session = run_agent(
                query="vintage graphic tee under $30",
                wardrobe=get_example_wardrobe(),
            )
            assert session["all_listings_cache"] == real_listings
            assert mock_load.call_count == 1
