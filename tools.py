"""
tools.py

The five Rerack tools. Each tool is a standalone function that can be
called and tested independently before being wired into the agent loop.

Complete and test each tool in this order:
    1. search_listings  — pure Python, no LLM
    2. retry_search     — calls search_listings internally
    3. compare_prices   — pure Python, no LLM
    4. suggest_outfit   — LLM call via Groq
    5. create_fit_card  — LLM call via Groq

Tools 1-3 have no LLM dependency — implement and test them first.
"""

import os
import re

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()

_MODEL = "llama-3.3-70b-versatile"
_OUTFIT_TEMP_EMPTY_WARDROBE = 0.7
_OUTFIT_TEMP_WITH_WARDROBE = 0.5
_OUTFIT_MAX_TOKENS = 400
_FITCARD_TEMP = 0.9
_FITCARD_MAX_TOKENS = 200


# ── Groq client ───────────────────────────────────────────────────────────────


def _get_groq_client() -> Groq:
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY not set. Add it to a .env file in the project root.")
    return Groq(api_key=api_key)


# ── Tokenizer (shared by Tool 1 and Tool 2) ────────────────────────────────────

_STOPWORDS = {
    "a",
    "an",
    "the",
    "in",
    "on",
    "at",
    "for",
    "of",
    "and",
    "or",
    "with",
    "it",
    "is",
    "to",
    "i",
    "me",
    "my",
    "im",
    "looking",
    "find",
    "want",
    "need",
    "get",
    "something",
}


def _tokenize(text: str) -> set[str]:
    """
    Lowercase, extract alphabetic tokens, strip stopwords and single chars.
    Used by search_listings for scoring and retry_search for description cleaning.
    """
    tokens = re.findall(r"[a-z]+", text.lower())
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 1}


# ── Tool 1: search_listings ───────────────────────────────────────────────────


def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
    listings: list[dict] | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for
                     (e.g., "vintage graphic tee").
        size:        Size string to filter by, or None to skip size filtering.
                     Matched as a case-insensitive token against the listing's
                     size split on whitespace/slash — "M" matches "S/M" but not
                     "US 8".
        max_price:   Maximum price (inclusive), or None to skip price filtering.
        listings:    Pre-loaded listings list. Pass session["all_listings_cache"]
                     to avoid re-reading the file. Falls back to load_listings().

    Returns:
        A list of matching listing dicts sorted by relevance score (desc),
        then price (asc) as tiebreaker. Returns [] if nothing matches — never raises.

    Scoring:
        Build a composite corpus per listing:
            title + description + style_tags (joined) + brand + category
        score = tokens in (desc_tokens ∩ corpus_tokens)
              + tokens in (desc_tokens ∩ style_tokens)   ← style tags count double
        Drop listings with score == 0.

    """
    try:
        all_listings = listings if listings is not None else load_listings()

        candidates = all_listings
        if max_price is not None:
            candidates = [lst for lst in candidates if lst["price"] <= max_price]
        if size is not None:
            candidates = [
                lst
                for lst in candidates
                if size.lower() in re.split(r"[\s/]+", lst["size"].lower())
            ]

        desc_tokens = _tokenize(description)
        if not desc_tokens:
            return []

        scored = []
        for listing in candidates:
            corpus = (
                listing["title"]
                + " "
                + listing["description"]
                + " "
                + " ".join(listing["style_tags"])
                + " "
                + (listing["brand"] or "")
                + " "
                + listing["category"]
            )
            corpus_tokens = _tokenize(corpus)
            style_tokens = {t for tag in listing["style_tags"] for t in _tokenize(tag)}
            score = len(desc_tokens & corpus_tokens) + len(desc_tokens & style_tokens)
            if score > 0:
                scored.append((score, listing["price"], listing))

        scored.sort(key=lambda x: (-x[0], x[1]))
        return [item for _, _, item in scored]
    except Exception:
        return []


# ── Tool 4: suggest_outfit ────────────────────────────────────────────────────


def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering buying).
        wardrobe: A wardrobe dict with an 'items' key. May be empty.

    Returns:
        A non-empty string with outfit suggestions. Never returns "".

    Two prompt branches based on whether wardrobe["items"] is empty:

    EMPTY WARDROBE PROMPT (temperature=0.7, max_tokens=400):
        You are a personal stylist specializing in secondhand fashion.
        A user found this thrifted piece:
        - Name: {title}
        - Category: {category}
        - Style: {style_tags joined by ", "}
        - Colors: {colors joined by ", "}
        - Condition: {condition}
        They don't have a wardrobe on file. Suggest 2 complete outfits they could
        build around this piece. For each outfit: list 3-4 specific items to pair
        with it, describe the vibe in 1 sentence, mention one occasion it works for.
        Be specific. Avoid generic advice.

    POPULATED WARDROBE PROMPT (temperature=0.5, max_tokens=400):
        You are a personal stylist specializing in secondhand fashion.
        A user found this thrifted piece:
        - Name: {title}
        - Category: {category}
        - Style: {style_tags joined by ", "}
        - Colors: {colors joined by ", "}
        Their wardrobe includes:
        {formatted wardrobe — see format below}
        Suggest 1-2 complete outfits using the new piece PLUS specific items from
        their wardrobe. Reference wardrobe items by name. Describe why the
        combination works. Only use pieces from the wardrobe list. Do not invent items.

    Wardrobe formatting (build inline, do not extract to a separate function):
        for item in wardrobe["items"]:
            tags = ", ".join(item["style_tags"])
            colors = ", ".join(item["colors"])
            note = f" ({item['notes']})" if item.get("notes") else ""
            lines.append(f"- {item['name']} [{colors}] [{tags}]{note}")
        wardrobe_text = "\n".join(lines)

    LLM model: "llama-3.3-70b-versatile"
    """
    if not wardrobe.get("items"):
        prompt = (
            "You are a personal stylist specializing in secondhand fashion.\n"
            "A user found this thrifted piece:\n"
            f"- Name: {new_item['title']}\n"
            f"- Category: {new_item['category']}\n"
            f"- Style: {', '.join(new_item['style_tags'])}\n"
            f"- Colors: {', '.join(new_item['colors'])}\n"
            f"- Condition: {new_item['condition']}\n\n"
            "They don't have a wardrobe on file yet. Suggest 2 complete outfits they "
            "could build around this piece. For each outfit: list 3-4 specific items "
            "to pair with it, describe the vibe in 1 sentence, and mention one occasion "
            "it works for. Be specific. Avoid generic advice."
        )
        temperature = _OUTFIT_TEMP_EMPTY_WARDROBE
    else:
        lines = []
        for w_item in wardrobe["items"]:
            tags = ", ".join(w_item["style_tags"])
            colors = ", ".join(w_item["colors"])
            note = f" ({w_item['notes']})" if w_item.get("notes") else ""
            lines.append(f"- {w_item['name']} [{colors}] [{tags}]{note}")
        wardrobe_text = "\n".join(lines)

        prompt = (
            "You are a personal stylist specializing in secondhand fashion.\n"
            "A user found this thrifted piece:\n"
            f"- Name: {new_item['title']}\n"
            f"- Category: {new_item['category']}\n"
            f"- Style: {', '.join(new_item['style_tags'])}\n"
            f"- Colors: {', '.join(new_item['colors'])}\n\n"
            "Their wardrobe includes:\n"
            f"{wardrobe_text}\n\n"
            "Suggest 1-2 complete outfits using the new piece PLUS specific items from "
            "their wardrobe. Reference wardrobe items by name. Describe why the "
            "combination works (color, vibe, silhouette). Only use pieces from the "
            "wardrobe list above. Do not invent items they don't own."
        )
        temperature = _OUTFIT_TEMP_WITH_WARDROBE

    try:
        client = _get_groq_client()
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=_MODEL,
            temperature=temperature,
            max_tokens=_OUTFIT_MAX_TOKENS,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return "Unable to generate outfit suggestions. Try pairing with neutral basics."


# ── Tool 5: create_fit_card ───────────────────────────────────────────────────


def create_fit_card(
    outfit: str,
    new_item: dict,
    price_verdict: dict | None = None,
) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:        The outfit suggestion string from suggest_outfit().
        new_item:      The listing dict for the thrifted item.
        price_verdict: Dict from compare_prices(), or None. Weaves a price
                       insight sentence into the caption prompt when provided.

    Returns:
        A 2–4 sentence Instagram-style caption ending with 3-5 hashtags.
        Returns a descriptive error string (not an exception) if outfit is empty.

    Price verdict → prompt sentence (suppress entirely if comparable_count < 3):
        "great deal"     → "This one's a steal — comparables average ${avg:.0f}."
        "fair"           → "Fairly priced — comparables average ${avg:.0f}."
        "overpriced"     → "A bit pricey vs similar listings (avg ${avg:.0f})."
        "no comparables" → "Hard to compare — might be a rare find."
        None or missing  → omit price line from prompt entirely

    Caption requirements (include in prompt):
        - 2-4 sentences, first person
        - Mention item name, price, and platform once each, naturally
        - Capture the specific outfit vibe (not generic "I love thrifting!")
        - No em-dashes, no bullet points
        - End with 3-5 relevant hashtags on a new line

    LLM call: model="llama-3.3-70b-versatile", temperature=0.9, max_tokens=200
    """
    if not outfit or not outfit.strip():
        return "Couldn't generate a fit card — no outfit suggestion was available."

    verdict_sentence = ""
    if price_verdict and price_verdict.get("comparable_count", 0) >= 3:
        avg = price_verdict.get("avg_price")
        v = price_verdict.get("verdict", "")
        if v == "great deal" and avg:
            verdict_sentence = f"This one's a steal — comparables average ${avg:.0f}."
        elif v == "fair" and avg:
            verdict_sentence = f"Fairly priced — comparables average ${avg:.0f}."
        elif v == "overpriced" and avg:
            verdict_sentence = f"A bit pricey vs similar listings (avg ${avg:.0f})."
        elif v == "no comparables":
            verdict_sentence = "Hard to compare — might be a rare find."

    price_line = f"Price context: {verdict_sentence}\n" if verdict_sentence else ""
    prompt = (
        "You are writing an Instagram caption for a thrift outfit post. "
        "It should sound like a real person, not a brand.\n\n"
        f"The item: {new_item['title']} from {new_item['platform']}, "
        f"priced at ${new_item['price']:.2f}.\n"
        f"{price_line}"
        f"The outfit: {outfit[:300]}\n\n"
        "Write a caption that:\n"
        "- Is 2-4 sentences long, first person\n"
        "- Mentions the item name, price, and platform once each, naturally\n"
        "- Captures the specific outfit vibe (not generic 'I love thrifting!')\n"
        "- Uses no em-dashes and no bullet points\n"
        "- Ends with 3-5 relevant hashtags on a new line"
    )

    try:
        client = _get_groq_client()
        response = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model=_MODEL,
            temperature=_FITCARD_TEMP,
            max_tokens=_FITCARD_MAX_TOKENS,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return (
            f"Found: {new_item['title']} on {new_item['platform']} "
            f"for ${new_item['price']:.2f}. {outfit[:100]}..."
        )


# ── Tool 3: compare_prices ────────────────────────────────────────────────────


def compare_prices(
    item: dict,
    listings: list[dict] | None = None,
) -> dict:
    """
    Estimate whether an item's price is fair based on comparable listings.

    Args:
        item:     The listing dict to evaluate (session["selected_item"]).
        listings: Pre-loaded listings. Pass session["all_listings_cache"] to
                  avoid re-reading the file. Falls back to load_listings().

    Returns:
        {
            "avg_price":        float | None,
            "min_price":        float | None,
            "max_price":        float | None,
            "comparable_count": int,
            "verdict":          str,
        }

    Comparable selection:
        PRIMARY:  same category AND ≥1 shared style_tag, exclude self by id.
        FALLBACK: same category only (if primary yields 0 results).
        If fallback also empty → return {"verdict": "no comparables", "avg_price": None,
                                          "min_price": None, "max_price": None,
                                          "comparable_count": 0}

    Verdict thresholds (item["price"] vs avg comparable price):
        price <= avg * 0.80  →  "great deal"
        price <= avg * 1.10  →  "fair"
        price >  avg * 1.10  →  "overpriced"

    A fifth verdict, "unavailable", is returned if an unexpected error occurs
    (e.g. a malformed item dict missing expected keys) — never raises.
    """
    try:
        all_listings = listings if listings is not None else load_listings()
        others = [lst for lst in all_listings if lst["id"] != item["id"]]
        item_tags = set(item["style_tags"])

        comparables = [
            lst
            for lst in others
            if lst["category"] == item["category"] and len(set(lst["style_tags"]) & item_tags) >= 1
        ]
        if not comparables:
            comparables = [lst for lst in others if lst["category"] == item["category"]]
        if not comparables:
            return {
                "verdict": "no comparables",
                "avg_price": None,
                "min_price": None,
                "max_price": None,
                "comparable_count": 0,
            }

        prices = [lst["price"] for lst in comparables]
        avg = round(sum(prices) / len(prices), 2)
        price = item["price"]

        if price <= avg * 0.80:
            verdict = "great deal"
        elif price <= avg * 1.10:
            verdict = "fair"
        else:
            verdict = "overpriced"

        return {
            "avg_price": avg,
            "min_price": round(min(prices), 2),
            "max_price": round(max(prices), 2),
            "comparable_count": len(comparables),
            "verdict": verdict,
        }
    except Exception:
        return {
            "verdict": "unavailable",
            "avg_price": None,
            "min_price": None,
            "max_price": None,
            "comparable_count": 0,
        }


# ── Tool 2: retry_search ──────────────────────────────────────────────────────


def retry_search(
    description: str,
    size: str | None,
    max_price: float | None,
    listings: list[dict] | None = None,
) -> dict:
    """
    Called when search_listings() returns no results. Progressively loosens
    constraints and retries until results are found or all options exhausted.

    Args:
        description: Same description passed to the original search.
        size:        Original size filter (dropped in step 1, always).
        max_price:   Original price cap (may be raised or dropped).
        listings:    Pre-loaded listings list. Pass session["all_listings_cache"]
                     to avoid re-reading the file on each retry step.

    Returns:
        {
            "results":         list[dict],
            "loosened":        list[str],    # human-readable changes made
            "final_size":      None,         # always None — size always dropped
            "final_max_price": float | None,
        }

    Loosening order (stop at first non-empty result):
        Step 1: Drop size only.
                loosened = ["size filter removed — showing all sizes"]
        Step 2: Drop size + raise price 25% (round to nearest dollar).
                loosened adds: f"price ceiling raised to ${raised:.0f}"
        Step 3: Drop size + drop price entirely.
                loosened adds: "price filter removed"

    If all three steps empty → return {"results": [], "loosened": [], ...}

    """

    def _ret(results, loosened, final_max_price):
        return {
            "results": results,
            "loosened": loosened,
            "final_size": None,
            "final_max_price": final_max_price,
        }

    # Step 1: drop size only
    results = search_listings(description, size=None, max_price=max_price, listings=listings)
    if results:
        return _ret(results, ["size filter removed — showing all sizes"], max_price)

    # Step 2: drop size + raise price 25%
    if max_price is not None:
        raised = round(max_price * 1.25)
        results = search_listings(description, size=None, max_price=raised, listings=listings)
        if results:
            return _ret(
                results,
                [
                    "size filter removed — showing all sizes",
                    f"price ceiling raised to ${raised:.0f}",
                ],
                raised,
            )

    # Step 3: drop size + drop price entirely
    loosened = ["size filter removed — showing all sizes"]
    if max_price is not None:
        loosened.append("price filter removed")
    results = search_listings(description, size=None, max_price=None, listings=listings)
    if results:
        return _ret(results, loosened, None)

    return _ret([], [], None)


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Tool 1: search_listings ===")
    results = search_listings("vintage graphic tee", size="M", max_price=30)
    print(f"Found {len(results)} results")
    if results:
        print(f"Top: {results[0]['title']} — ${results[0]['price']}")

    print("\n=== Tool 3: compare_prices ===")
    verdict = {}
    if results:
        verdict = compare_prices(results[0])
        print(
            f"Verdict: {verdict['verdict']} "
            f"(avg ${verdict['avg_price']}, n={verdict['comparable_count']})"
        )

    print("\n=== Tool 2: retry_search (no-results case) ===")
    retry = retry_search("designer ballgown", "XXS", 5)
    print(f"Retry found {len(retry['results'])} results. Loosened: {retry['loosened']}")

    print("\n=== Tool 4: suggest_outfit ===")
    from utils.data_loader import get_example_wardrobe

    suggestion = ""
    if results:
        suggestion = suggest_outfit(results[0], get_example_wardrobe())
        print(suggestion[:200] + "..." if len(suggestion) > 200 else suggestion)

    print("\n=== Tool 5: create_fit_card ===")
    if results and suggestion:
        card = create_fit_card(suggestion, results[0], price_verdict=verdict or None)
        print(card)
