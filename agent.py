"""
agent.py

The FitFindr planning loop. Orchestrates the five tools in response to a
natural language user query, passing state between them via a session dict.

Usage:
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import re

from tools import search_listings, suggest_outfit, create_fit_card, compare_prices, retry_search
from utils.data_loader import load_listings, get_example_wardrobe, get_empty_wardrobe


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.
    All tool results and intermediate state are stored here.
    """
    return {
        # Input
        "query": query,
        "wardrobe": wardrobe,
        # Parsed query fields (populated by _parse_query)
        "parsed": {},
        # Search state
        "all_listings_cache": None,  # loaded once, shared by search + compare
        "search_results": [],
        "retry_attempted": False,
        "retry_loosened": [],        # list[str] — surfaces in Gradio listing panel
        # Selected item
        "selected_item": None,
        # Tool outputs
        "price_verdict": None,       # dict from compare_prices
        "outfit_suggestion": None,
        "fit_card": None,
        # Error (set on early termination — check this first in handle_query)
        "error": None,
    }


# ── query parser ──────────────────────────────────────────────────────────────

def _parse_query(query: str) -> dict:
    """
    Extract description, size, and max_price from a raw user query using regex.
    No LLM — fast and deterministic.

    Returns:
        {"description": str, "size": str | None, "max_price": float | None}
    """
    price_re = re.search(
        r'(?:under|below|max|less\s+than|<)\s*\$?\s*(\d+(?:\.\d+)?)'
        r'|\$(\d+(?:\.\d+)?)',
        query, re.IGNORECASE
    )
    max_price = float(next(g for g in price_re.groups() if g)) if price_re else None

    size_re = re.search(
        r'\b(?:size\s+)?(?:W\d{2}(?:\s+L\d{2})?'
        r'|US\s*\d+(?:\.\d+)?|UK\s*\d+|XXS|XS|XL|XXL|[SML])\b',
        query, re.IGNORECASE
    )
    size = size_re.group(0).strip() if size_re else None

    description = query
    if price_re:
        description = description.replace(price_re.group(0), " ")
    if size_re:
        description = description.replace(size_re.group(0), " ")

    for filler in [r"i'?m looking for", r"find me", r"looking for",
                   r"i want", r"i need", r"can you find", r"show me"]:
        description = re.sub(filler, " ", description, flags=re.IGNORECASE)

    description = re.sub(r'\s+', ' ', description).strip().lower()
    if not description:
        description = query.lower().strip()

    return {"description": description, "size": size, "max_price": max_price}


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Args:
        query:    Natural language user request.
        wardrobe: User's wardrobe dict (example or empty).

    Returns:
        Completed session dict. Always check session["error"] first — if set,
        outfit_suggestion and fit_card will be None.

    Planning loop — implement the TODO steps below in order:

        Step 1:  session = _new_session(query, wardrobe)

        Step 2:  Load listings once:
                     session["all_listings_cache"] = load_listings()
                 Pass this cache into both search_listings() and compare_prices()
                 so the JSON file is read only once per run_agent() call.

        Step 3:  Parse query:
                     session["parsed"] = _parse_query(query)
                 Unpack:
                     description = session["parsed"]["description"]
                     size        = session["parsed"]["size"]
                     max_price   = session["parsed"]["max_price"]

        Step 4:  Primary search:
                     results = search_listings(
                         description, size=size, max_price=max_price,
                         listings=session["all_listings_cache"]
                     )
                     session["search_results"] = results

        Step 5:  If results empty → retry:
                     retry = retry_search(description, size, max_price, listings=session["all_listings_cache"])
                     session["retry_attempted"] = True
                     session["retry_loosened"] = retry["loosened"]
                     results = retry["results"]
                     session["search_results"] = results

        Step 6:  If still empty → error and return early:
                     session["error"] = (
                         f"No listings found for '{description}' "
                         "even after relaxing filters. Try different keywords."
                     )
                     return session

        Step 7:  Select top result:
                     session["selected_item"] = results[0]

        Step 8:  Compare prices (ALWAYS — runs on both normal and retry path):
                     session["price_verdict"] = compare_prices(
                         session["selected_item"],
                         listings=session["all_listings_cache"]
                     )

        Step 9:  Suggest outfit:
                     session["outfit_suggestion"] = suggest_outfit(
                         session["selected_item"], wardrobe
                     )

        Step 10: Create fit card:
                     session["fit_card"] = create_fit_card(
                         session["outfit_suggestion"],
                         session["selected_item"],
                         price_verdict=session["price_verdict"],
                     )

        Step 11: return session
    """
    session = _new_session(query, wardrobe)

    session["all_listings_cache"] = load_listings()
    cache = session["all_listings_cache"]

    session["parsed"] = _parse_query(query)
    description = session["parsed"]["description"]
    size        = session["parsed"]["size"]
    max_price   = session["parsed"]["max_price"]

    results = search_listings(description, size=size, max_price=max_price, listings=cache)
    session["search_results"] = results

    if not results:
        retry = retry_search(description, size, max_price, listings=cache)
        session["retry_attempted"] = True
        session["retry_loosened"] = retry["loosened"]
        results = retry["results"]
        session["search_results"] = results

    if not results:
        session["error"] = (
            f"No listings found for '{description}' "
            "even after relaxing filters. Try different keywords."
        )
        return session

    session["selected_item"] = results[0]
    session["price_verdict"] = compare_prices(session["selected_item"], listings=cache)
    session["outfit_suggestion"] = suggest_outfit(session["selected_item"], wardrobe)
    session["fit_card"] = create_fit_card(
        session["outfit_suggestion"],
        session["selected_item"],
        price_verdict=session["price_verdict"],
    )
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Found:   {session['selected_item']['title']} — ${session['selected_item']['price']}")
        print(f"Verdict: {session['price_verdict']['verdict']}")
        print(f"\nOutfit:\n{session['outfit_suggestion']}")
        print(f"\nFit card:\n{session['fit_card']}")

    print("\n\n=== No-results path (should trigger retry then error) ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error:    {session2['error']}")
    print(f"Retried:  {session2['retry_attempted']}")
    print(f"Loosened: {session2['retry_loosened']}")

    print("\n\n=== Empty wardrobe path ===\n")
    session3 = run_agent(
        query="90s track jacket size M",
        wardrobe=get_empty_wardrobe(),
    )
    if session3["error"]:
        print(f"Error: {session3['error']}")
    else:
        print(f"Found: {session3['selected_item']['title']}")
        print(f"\nOutfit (general styling):\n{session3['outfit_suggestion'][:300]}")
