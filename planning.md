# FitFindr - planning.md

## Tools

List every tool your agent will use. For each tool, fill in all four fields.
You must have at least 3 tools. The three required tools are listed - add any additional tools below them.

### Tool 1: search_listings

**What it does:**
Searches the mock listings dataset for items matching a text description plus optional size and price filters. Returns a ranked list of matches sorted by keyword relevance.

**Input parameters:**
- `description` (str): Keywords describing what the user wants (e.g., "vintage graphic tee"). Extracted from the raw user query via regex parsing.
- `size` (str | None): Size string to filter by (e.g., "M", "W28"). Case-insensitive substring match. None skips size filtering.
- `max_price` (float | None): Maximum price inclusive. None skips price filtering.

**What it returns:**
A list of listing dicts sorted by relevance score (highest first), price ascending as tiebreaker. Each dict has: `id`, `title`, `description`, `category`, `style_tags` (list), `size`, `condition`, `price` (float), `colors` (list), `brand`, `platform`. Returns `[]` if nothing matches — never raises.

**What happens if it fails or returns nothing:**
Agent calls `retry_search()` with loosened constraints. If retry also returns empty, agent sets `session["error"]` and returns early without calling `suggest_outfit` or `create_fit_card`.

---

### Tool 2: suggest_outfit

**What it does:**
Given a thrifted item and the user's wardrobe, calls the Groq LLM to suggest 1-2 complete outfits. Uses two prompt branches: one for empty wardrobes (general styling advice) and one for populated wardrobes (references named wardrobe pieces).

**Input parameters:**
- `new_item` (dict): A listing dict — the item the user is considering buying.
- `wardrobe` (dict): A wardrobe dict with an `items` key containing a list of wardrobe item dicts. May be empty.

**What it returns:**
A non-empty string with outfit suggestions from the LLM. Never returns an empty string — falls back to a hardcoded message if the LLM call fails.

**What happens if it fails or returns nothing:**
If wardrobe is empty: uses the general styling branch (no reference to specific owned pieces). If LLM call fails: returns `"Unable to generate outfit suggestions. Try pairing with neutral basics."`.

---

### Tool 3: create_fit_card

**What it does:**
Calls the Groq LLM to generate a 2-4 sentence Instagram-style caption for the thrifted find, incorporating outfit context and a price verdict.

**Input parameters:**
- `outfit` (str): The outfit suggestion string from `suggest_outfit()`.
- `new_item` (dict): The listing dict for the thrifted item.
- `price_verdict` (dict | None): The dict returned by `compare_prices()`, or None to omit price context.

**What it returns:**
A 2-4 sentence string formatted as an OOTD caption. Mentions item name, price, and platform once each. Ends with 3-5 hashtags. Returns a descriptive error string (not an exception) if `outfit` is empty/whitespace.

**What happens if it fails or returns nothing:**
If `outfit` is empty: returns `"Couldn't generate a fit card — no outfit suggestion was available."`. If LLM call fails: returns a plain fallback using item title, price, and platform.

---

### Tool 4: compare_prices

**What it does:**
Given a listing item, finds comparable items in the dataset (same category + at least 1 shared style tag) and returns a price verdict indicating whether the item is a great deal, fair, or overpriced relative to the average.

**Input parameters:**
- `item` (dict): The listing dict to evaluate.
- `listings` (list[dict] | None = None): Pre-loaded listings to avoid re-reading the file. If None, calls `load_listings()` internally.

**What it returns:**
`{"avg_price": float, "min_price": float, "max_price": float, "comparable_count": int, "verdict": str}`. Verdict is one of `"great deal"`, `"fair"`, `"overpriced"`, `"no comparables"`. Verdict is suppressed in the fit card prompt if `comparable_count < 3` (too few to be meaningful).

**What happens if it fails or returns nothing:**
If no comparables exist in the same category + style tag, falls back to same-category-only. If still empty, returns `{"verdict": "no comparables", ...}`. On exception: returns `{"verdict": "unavailable", "avg_price": None, ...}`.

---

### Tool 5: retry_search

**What it does:**
Called when `search_listings()` returns no results. Progressively loosens constraints (drop size → raise price 25% → drop price entirely) and stops as soon as results are found. Informs the user what was changed.

**Input parameters:**
- `description` (str): Same description passed to the original search.
- `size` (str | None): Original size filter (will be dropped in step 1).
- `max_price` (float | None): Original price cap (may be raised or dropped).

**What it returns:**
`{"results": list[dict], "loosened": list[str], "final_size": None, "final_max_price": float | None}`. `loosened` is a list of human-readable strings shown in the UI (e.g., `"size filter removed"`, `"price ceiling raised to $38"`). Returns `{"results": [], "loosened": [], ...}` if all three loosening steps still produce nothing.

**What happens if it fails or returns nothing:**
If all retry steps return empty, agent sets `session["error"]` to `"No listings found for '{description}' even after relaxing filters. Try different keywords."` and returns early.

---

## Planning Loop

**How does your agent decide which tool to call next?**

The loop is linear with one branch point (no results after search). Steps execute in fixed order — there is no dynamic replanning:

1. Parse query with regex → extract description, size, max_price.
2. Call `search_listings()`. If results → continue. If empty → call `retry_search()`.
3. After retry: if still empty → set error, return early.
4. Select top result (`results[0]`).
5. Call `compare_prices()` — always, regardless of which search path produced the result.
6. Call `suggest_outfit()`.
7. Call `create_fit_card()` with the price verdict from step 5.
8. Return session.

The loop knows it's done when `create_fit_card()` returns (happy path) or when `session["error"]` is set (error path). No loops, no replanning, no LLM driving the control flow.

---

## State Management

**How does information from one tool get passed to the next?**

All state lives in the session dict initialized by `_new_session()`. Each tool result is written to a named key immediately after the call. Tools receive their inputs from the session dict — not from each other directly.

Key fields and their flow:

| Session key | Set by | Read by |
|---|---|---|
| `query` | `_new_session` | `_parse_query` |
| `parsed` | `_parse_query` | `search_listings`, `retry_search` |
| `all_listings_cache` | `run_agent` (once at start) | `search_listings`, `compare_prices` |
| `search_results` | `search_listings` / `retry_search` | `run_agent` (selects top result) |
| `selected_item` | `run_agent` | `compare_prices`, `suggest_outfit`, `create_fit_card` |
| `price_verdict` | `compare_prices` | `create_fit_card` |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` |
| `fit_card` | `create_fit_card` | `handle_query` (Gradio) |
| `retry_attempted` | `run_agent` | `handle_query` (UI note) |
| `retry_loosened` | `retry_search` | `handle_query` (UI note) |
| `error` | `run_agent` | `handle_query` |

Listings are loaded once at the start of `run_agent()` into `all_listings_cache` and passed to both `search_listings` and `compare_prices` — avoids re-reading the JSON file on each call.

---

## Error Handling

For each tool, describe the specific failure mode you're handling and what the agent does in response.

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| search_listings | No results match the query | Call `retry_search()` with loosened constraints |
| retry_search | All retry steps still return empty | Set `session["error"]`, return session early — skip outfit and fit card |
| compare_prices | No comparable items found (< 3) | Return `verdict: "no comparables"` — omit price insight from fit card prompt |
| suggest_outfit | Wardrobe is empty | Use general styling branch — ask LLM for outfit ideas without referencing owned pieces |
| suggest_outfit | LLM call fails | Return hardcoded fallback string, never empty |
| create_fit_card | `outfit` is empty or whitespace | Return descriptive error string, do not call LLM |
| create_fit_card | LLM call fails | Return plain fallback string with item title/price/platform |

---

## Architecture

```
User query (Gradio UI)
        │
        ▼
handle_query(user_query, wardrobe_choice)             ← app.py
        │
        ▼
run_agent(query, wardrobe)                           ← agent.py
        │
        ├─ _parse_query(query) → session["parsed"]
        │
        ├─ search_listings(description, size, max_price, listings=cache)
        │       │
        │   no results?
        │       │ yes
        │       ▼
        │   retry_search(description, size, max_price)
        │       │
        │   still empty? → set session["error"] → return session
        │
        ├─ session["selected_item"] = results[0]
        │
        ├─ compare_prices(selected_item, listings=cache) → session["price_verdict"]
        │
        ├─ suggest_outfit(selected_item, wardrobe) → session["outfit_suggestion"]
        │
        └─ create_fit_card(outfit, item, price_verdict) → session["fit_card"]
                │
                ▼
        return session
                │
                ▼
handle_query → (listing_text, outfit_suggestion, fit_card)  → Gradio panels
```

State / session dict flows vertically through every step. Error path branches off after `retry_search` fails.

---

## AI Tool Plan

**Milestone 3 - Individual tool implementations:**

For each tool, give Claude the tool's spec section from this file (What it does, inputs, returns, failure mode) plus the relevant data schema (listing fields, wardrobe item fields). Ask it to implement the function body only — not the full file. Test each in isolation with 3 queries before trusting it.

- `search_listings`: Give spec + listing field list + `_tokenize` helper design. Verify with `search_listings("vintage graphic tee", size="M", max_price=30)`, `search_listings("leather jacket")`, `search_listings("nonexistent xyzzy")`.
- `suggest_outfit`: Give spec + both prompt templates from plan. Verify with example wardrobe (populated branch) and empty wardrobe (general branch).
- `create_fit_card`: Give spec + verdict sentence map + caption format rules. Verify caption length, hashtag presence, and that price verdict appears when provided.
- `compare_prices`: Give spec + verdict thresholds + comparable selection logic. Verify with a listing that has style-tag matches and one that doesn't.
- `retry_search`: Give spec + loosening order. Verify with `retry_search("designer ballgown", "XXS", 5)` — should drop size, then raise price, until it finds something or returns empty.

**Milestone 4 - Planning loop and state management:**

Give Claude this entire planning.md plus the completed tool implementations. Ask it to implement `run_agent()` in `agent.py` following the architecture diagram above. Verify with `python agent.py` — check both the happy path and the no-results path.

---

## A Complete Interaction (Step by Step)

**Example user query:** "I'm looking for a vintage graphic tee under $30. I mostly wear baggy jeans and chunky sneakers. What's out there and how would I style it?"

**Step 1:** `_parse_query()` extracts `description="vintage graphic tee"`, `size=None`, `max_price=30.0`. Filler ("I'm looking for") is stripped. Size not mentioned so remains None.

**Step 2:** `search_listings("vintage graphic tee", size=None, max_price=30.0)` loads all 40 listings, filters to `price <= 30`, tokenizes each, scores by keyword overlap (title/description/style_tags). Returns e.g. 4 matching listings sorted by score. Stored in `session["search_results"]`.

**Step 3:** Results non-empty. `session["selected_item"] = results[0]` (top match, e.g. "Vintage Band Tee — $22, Depop").

**Step 4:** `compare_prices(selected_item, listings=cache)` finds comparables in "tops" category with overlapping style tags (vintage, graphic). Computes avg $27. Item at $22 → `verdict: "great deal"`. Stored in `session["price_verdict"]`.

**Step 5:** `suggest_outfit(selected_item, wardrobe)` — wardrobe has 10 items. LLM prompt lists wardrobe items by name, asks for 1-2 outfits using the band tee + named wardrobe pieces. Returns e.g. "Pair with your baggy straight-leg jeans and white low-top sneakers for a classic 90s streetwear look...". Stored in `session["outfit_suggestion"]`.

**Step 6:** `create_fit_card(outfit_suggestion, selected_item, price_verdict)` — LLM prompt includes item details, the outfit suggestion, and "This one's a steal — comparables average $27." Returns a 3-sentence Instagram caption with hashtags. Stored in `session["fit_card"]`.

**Step 7:** `run_agent()` returns session. `handle_query()` maps session to 3 Gradio panels.

**Final output to user:**

- Panel 1 (Top listing): "Vintage Band Tee — $22.00 on Depop | Size: M | Condition: excellent | Style: vintage, graphic | Price verdict: great deal (avg comparable: $27)"
- Panel 2 (Outfit idea): Full LLM outfit suggestion referencing owned wardrobe items by name.
- Panel 3 (Fit card): Instagram-style caption ending with hashtags.
