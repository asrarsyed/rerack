# FitFindr

A secondhand clothing finder with AI-powered outfit suggestions. Describe what you're looking for in plain English — FitFindr searches mock listings, compares prices, suggests outfits from your wardrobe, and generates a shareable fit card.

## Demo Video

> **Add your video link here:**
> [Demo Video](YOUR_VIDEO_LINK_HERE)

## Images

> View images [here](images.md)

<!-- Paste a YouTube, Loom, or Google Drive link above. Remove this comment when done. -->

---

## Setup

```bash
pip install -r requirements.txt
```

Set your Groq API key (get a free key at [console.groq.com](https://console.groq.com)):

```bash
# Option A: .env file
echo "GROQ_API_KEY=your_key_here" > .env

# Option B: direnv (if you have it installed)
echo 'export GROQ_API_KEY="$(pass api-keys/groq)"' > .envrc
direnv allow
```

Run the app:

```bash
python app.py
```

Open the URL shown in the terminal (usually `http://localhost:7860`).

Run tests:

```bash
python -m pytest tests/test_tools.py -v  
```

---

## Tool Inventory

### Tool 1: `search_listings`

**File:** `tools.py`

| Parameter | Type | Description |
|---|---|---|
| `description` | `str` | Natural language item description (filler already stripped by parser) |
| `size` | `str \| None` | Size filter (e.g. `"M"`, `"XS"`) — substring match, case-insensitive |
| `max_price` | `float \| None` | Upper price bound inclusive |
| `listings` | `list[dict] \| None` | Pre-loaded listings cache; loads from disk if `None` |

**Returns:** `list[dict]` — matched listings sorted by relevance score descending, then price ascending. Empty list if no matches.

**Purpose:** Filters the 40-listing dataset by price and size, then scores each candidate by token overlap between the query and the listing's composite text field (title + description + style tags + brand + category). Style tag hits count double as a quality signal. Items scoring zero are excluded.

---

### Tool 2: `suggest_outfit`

**File:** `tools.py`

| Parameter | Type | Description |
|---|---|---|
| `item` | `dict` | The selected listing dict |
| `wardrobe` | `dict` | User's wardrobe dict with an `items` key |

**Returns:** `str` — outfit suggestion text. Never empty; falls back to `"Unable to generate outfit suggestions. Try pairing with neutral basics."` on LLM error.

**Purpose:** Calls the Groq LLM (`llama-3.3-70b-versatile`) to generate outfit ideas. Two prompt branches: if the wardrobe is empty, asks for 2 general outfits built around the item (`temperature=0.7`); if the wardrobe has items, asks the model to suggest outfits using only the named pieces the user actually owns (`temperature=0.5` to reduce hallucination of non-owned items).

---

### Tool 3: `create_fit_card`

**File:** `tools.py`

| Parameter | Type | Description |
|---|---|---|
| `outfit` | `str` | Outfit suggestion text from Tool 2 |
| `item` | `dict` | The selected listing dict |
| `price_verdict` | `dict \| None` | Price comparison result from Tool 4; omit to skip price insight |

**Returns:** `str` — 2-4 sentence first-person fit caption ending with 3-5 hashtags. Returns a descriptive error string if `outfit` is empty or whitespace — never raises.

**Purpose:** Generates a social-media-style caption summarizing the find. If a price verdict is available and `comparable_count >= 3`, the prompt includes a verdict sentence (e.g. "This one's a steal — comparables average $22."). Uses `temperature=0.9` for expressive output.

---

### Tool 4: `compare_prices`

**File:** `tools.py`

| Parameter | Type | Description |
|---|---|---|
| `item` | `dict` | The listing to evaluate |
| `listings` | `list[dict] \| None` | Pre-loaded listings cache; loads from disk if `None` |

**Returns:** `dict` with keys: `verdict` (`"great deal"`, `"fair"`, `"overpriced"`, or `"no comparables"`), `avg_price` (`float`), `min_price` (`float`), `max_price` (`float`), `comparable_count` (`int`).

**Purpose:** Finds comparable listings (same category + at least 1 shared style tag; falls back to same category only) and benchmarks the item's price against their average. Thresholds: `<= 80%` of avg → great deal, `<= 110%` → fair, `> 110%` → overpriced. Verdict is suppressed from the fit card if `comparable_count < 3` (too few data points).

---

### Tool 5: `retry_search`

**File:** `tools.py`

| Parameter | Type | Description |
|---|---|---|
| `description` | `str` | Cleaned description from the query parser |
| `size` | `str \| None` | Original size filter |
| `max_price` | `float \| None` | Original price ceiling |

**Returns:** `dict` with keys: `results` (`list[dict]`), `loosened` (`list[str]` — human-readable descriptions of what was relaxed), `final_size` (always `None`), `final_max_price` (`float \| None`).

**Purpose:** Progressively loosens constraints until results are found, stopping at the first non-empty result: (1) drop size filter, (2) drop size + raise price ceiling 25%, (3) drop size + drop price entirely. The `loosened` list surfaces in the UI so the user knows what changed.

---

## How the Planning Loop Works

The agent in `agent.py` is a linear decision loop — no LLM decides what to call next. The sequence is deterministic, with two conditional branches:

```
Parse query (regex, no LLM)
    → extract description, size, max_price

Primary search
    → if results found: continue
    → if empty: trigger retry_search (3-step constraint loosening)
        → if still empty: set error message, return early

Select top result (results[0] — highest relevance score, lowest price as tiebreaker)

compare_prices (ALWAYS — runs on both normal and retry paths)
    → verdict suppressed in fit card if comparable_count < 3

suggest_outfit
    → empty wardrobe branch vs. populated wardrobe branch

create_fit_card (receives outfit suggestion + price verdict)

Return completed session dict
```

The key decision points:

1. **Did the primary search find anything?** If not, retry before giving up. The retry loop tries three progressively looser constraint sets and stops at the first hit.
2. **Was retry also empty?** Return an error string — not an exception. The UI displays it in panel 1.
3. **How many comparables exist?** If fewer than 3, the price verdict is computed but not surfaced to the user (unreliable with a small sample).
4. **Is the wardrobe empty?** `suggest_outfit` uses a different prompt with higher temperature — general styling advice instead of wardrobe-specific pairings.

---

## State Management

All intermediate state lives in a single session dict created by `_new_session()` at the start of each `run_agent()` call. Nothing is shared between calls.

| Key | Set by | Read by | Purpose |
|---|---|---|---|
| `query` | `_new_session` | `_parse_query` | Original raw user input |
| `parsed` | `_parse_query` | planning loop | Extracted description, size, max_price |
| `all_listings_cache` | `run_agent` (step 2) | `search_listings`, `compare_prices` | Listings loaded once per call — avoids re-reading JSON |
| `search_results` | primary search + retry | planning loop | Full result list; `results[0]` becomes selected item |
| `selected_item` | planning loop | Tools 2, 3, 4 | The listing being styled |
| `price_verdict` | `compare_prices` | `create_fit_card`, `handle_query` | Price context threaded into fit card and UI panel |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` | Passed directly as Tool 3's first argument |
| `fit_card` | `create_fit_card` | `handle_query` | Final output for panel 3 |
| `retry_attempted` | retry branch | UI note | Whether filters were loosened |
| `retry_loosened` | `retry_search` | `handle_query` | Human-readable list of what was relaxed |
| `error` | error branch | `handle_query` | If set, UI shows this and skips panels 2 and 3 |

The listings cache is the most important optimization: `load_listings()` reads disk once and the result is passed as a `listings=` keyword argument into both `search_listings()` and `compare_prices()`. Existing call sites that omit `listings` still work — the tools load from disk internally.

---

## Error Handling

| Tool | Failure mode | What happens |
|---|---|---|
| `search_listings` | No keyword overlap or filter mismatch | Returns `[]` — never raises |
| `search_listings` | Empty description string | Returns `[]` — tokenizer produces empty set, all scores are 0 |
| `retry_search` | All 3 loosening steps fail | Returns `{"results": [], "loosened": [], ...}` |
| `suggest_outfit` | LLM API error | Returns fallback string, never empty |
| `create_fit_card` | Empty/whitespace `outfit` arg | Returns `"Couldn't generate a fit card — no outfit suggestion was available."` |
| `compare_prices` | Fewer than 3 comparables | Returns verdict but `comparable_count < 3`; fit card and UI suppress it |
| `compare_prices` | Empty item dict | Returns `{"verdict": "no comparables", ...}` — never raises |
| `run_agent` | Still no results after retry | Sets `session["error"]`; `handle_query` shows error in panel 1, panels 2-3 empty |

**Concrete example from testing (Milestone 5):**

Query: `"designer ballgown size XXS under $5"`

```
search_listings("designer ballgown", size="XXS", max_price=5) → []
retry_search("designer ballgown", "XXS", 5):
  step 1: drop size → []
  step 2: drop size, raise price to $6.25 → []
  step 3: drop size + price → []
  returns {"results": [], "loosened": [], ...}
agent sets error: "No listings found for 'designer ballgown' even after relaxing filters. Try different keywords."
```

The agent never crashes. The UI shows the error in the listing panel with actionable guidance.

---

## Spec Reflection

**One way the spec helped:** The spec required documenting the planning loop's conditional logic in `planning.md` before writing any code. This forced the decision about where `compare_prices` should run — the initial workflow diagram placed it only on the normal path. Writing it out made it obvious that an item found via loosened constraints needs price context *more*, not less. That correction happened at the planning stage, not during debugging.

**One way implementation diverged from the spec:** The spec described `retry_search` returning a `loosened` list with messages like `"size filter removed — showing all sizes"`. In the impossible-query test (`designer ballgown`), the `loosened` list was empty even though retry ran, because the query fails all three loosening steps and none of them produce results — there's nothing to report. The spec assumed retry would always find *something* on at least one step. The implementation handles the full-failure case correctly (empty results + empty loosened list), but the spec didn't document this path explicitly.

---

## AI Usage

**Instance 1: Planning loop architecture**

I directed the AI to evaluate a proposed 5-tool workflow diagram and identify integration risks before writing any code. Input: the workflow diagram showing compare_prices only on the normal search path, plus the tool specs. The AI identified that compare_prices should run on both the normal and retry paths — omitting it on retry produces a fit card with no price context, which is worse than the normal case. I accepted this correction and it shaped the final `run_agent()` structure.

**Instance 2: Skeleton code with directive comments**

I directed the AI to implement skeleton files for `agent.py` and `app.py` with numbered TODO comments matching the planning doc steps exactly. The AI produced accurate stubs. I overrode one decision: the AI initially left `_parse_query()` returning `query.lower().strip()` as a passthrough. I directed it to implement the full regex extraction (price pattern, size pattern, filler phrase stripping) per the spec — the stub was too shallow to be useful as a starting point for the next milestone.

---

## Project Structure

```
ai201-project02/
├── data/
│   ├── listings.json              # 40 mock secondhand listings
│   └── wardrobe_schema.json       # Wardrobe format + example wardrobe
├── utils/
│   └── data_loader.py             # load_listings, get_example_wardrobe, get_empty_wardrobe
├── tests/
│   └── test_tools.py              # 25 pytest tests covering all 5 tools
├── tools.py                       # All 5 tools
├── agent.py                       # _parse_query, run_agent planning loop
├── app.py                         # Gradio UI, handle_query
├── planning.md                    # Architecture, tool specs, state diagram
└── requirements.txt
```
