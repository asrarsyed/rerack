# Architecture

## Pipeline

```
                     ┌───────────────────┐
   user query   ───▶ │   _parse_query    │  regex: description, size, max_price
                     └─────────┬─────────┘
                               ▼
                     ┌───────────────────┐
                     │  search_listings  │
                     └─────────┬─────────┘
                               │
                    empty? ────┴──── results found
                     │                        │
                     ▼                        │
             ┌───────────────┐                │
             │ retry_search  │                │
             │ (loosen size, │                │
             │  price, both) │                │
             └───────┬───────┘                │
                     │                        │
          still empty│    found               │
                     ▼      │                 │
              set error     └──────┬──────────┘
              return early         ▼
                          ┌───────────────────┐
                          │  compare_prices   │  always runs, both paths
                          └─────────┬─────────┘
                                    ▼
                          ┌───────────────────┐
                          │  suggest_outfit   │  LLM call
                          └─────────┬─────────┘
                                    ▼
                          ┌───────────────────┐
                          │  create_fit_card  │  LLM call
                          └─────────┬─────────┘
                                    ▼
                              session dict ───▶ Gradio panels
```

`agent.py` runs this as a fixed sequence, not an LLM-driven loop. Nothing decides what to call next at runtime except the two branches shown above: whether the search came back empty, and whether retry also came back empty.

## Tool Inventory

### search_listings

**File:** `tools.py`

| Parameter | Type | Description |
|---|---|---|
| `description` | `str` | Natural language item description, filler already stripped by the parser |
| `size` | `str \| None` | Size filter (e.g. `"M"`, `"XS"`), matched as a token against the listing's size split on whitespace/slash, case-insensitive |
| `max_price` | `float \| None` | Upper price bound, inclusive |
| `listings` | `list[dict] \| None` | Pre-loaded listings cache, loads from disk if `None` |

**Returns:** `list[dict]`, matched listings sorted by relevance score descending, then price ascending. Empty list if no matches.

Filters the 100-listing dataset by price and size, then scores each candidate by token overlap between the query and a composite text field (title, description, style tags, brand, category). Style tag hits count double. Zero-score items are excluded.

---

### suggest_outfit

**File:** `tools.py`

| Parameter | Type | Description |
|---|---|---|
| `item` | `dict` | The selected listing dict |
| `wardrobe` | `dict` | User's wardrobe dict with an `items` key |

**Returns:** `str`, outfit suggestion text. Never empty, falls back to a fixed message on LLM error.

Calls the Groq LLM (`llama-3.3-70b-versatile`). Two prompt branches: empty wardrobe asks for two general outfits built around the item (`temperature=0.7`); populated wardrobe asks the model to use only named pieces the user actually owns (`temperature=0.5`, to reduce hallucinated items).

---

### create_fit_card

**File:** `tools.py`

| Parameter | Type | Description |
|---|---|---|
| `outfit` | `str` | Outfit suggestion text from `suggest_outfit` |
| `item` | `dict` | The selected listing dict |
| `price_verdict` | `dict \| None` | Price comparison result from `compare_prices`, omit to skip price insight |

**Returns:** `str`, a 2-4 sentence first-person caption ending with 3-5 hashtags. Returns a descriptive error string if `outfit` is empty or whitespace, never raises.

Generates a social-media-style caption summarizing the find. If a price verdict is available and `comparable_count >= 3`, the prompt includes a verdict sentence (e.g. "This one's a steal, comparables average $22."). Uses `temperature=0.9` for expressive output.

---

### compare_prices

**File:** `tools.py`

| Parameter | Type | Description |
|---|---|---|
| `item` | `dict` | The listing to evaluate |
| `listings` | `list[dict] \| None` | Pre-loaded listings cache, loads from disk if `None` |

**Returns:** `dict` with `verdict` (`"great deal"`, `"fair"`, `"overpriced"`, `"no comparables"`, or `"unavailable"` on unexpected error), `avg_price`, `min_price`, `max_price`, `comparable_count`.

Finds comparable listings (same category plus at least one shared style tag, falling back to same category only) and benchmarks the item's price against their average. Thresholds: `<= 80%` of avg is a great deal, `<= 110%` is fair, above that is overpriced. Verdict is suppressed from the fit card if `comparable_count < 3`.

---

### retry_search

**File:** `tools.py`

| Parameter | Type | Description |
|---|---|---|
| `description` | `str` | Cleaned description from the query parser |
| `size` | `str \| None` | Original size filter |
| `max_price` | `float \| None` | Original price ceiling |

**Returns:** `dict` with `results`, `loosened` (human-readable descriptions of what was relaxed), `final_size` (always `None`), `final_max_price`.

Progressively loosens constraints until results are found, stopping at the first non-empty result: drop size, then drop size and raise price ceiling 25%, then drop size and price entirely.

---

## State Management

All intermediate state lives in a single session dict created by `_new_session()` at the start of each `run_agent()` call. Nothing is shared between calls.

| Key | Set by | Read by | Purpose |
|---|---|---|---|
| `query` | `_new_session` | `_parse_query` | Original raw user input |
| `parsed` | `_parse_query` | planning loop | Extracted description, size, max_price |
| `all_listings_cache` | `run_agent` | `search_listings`, `compare_prices` | Listings loaded once per call |
| `search_results` | primary search + retry | planning loop | Full result list, `results[0]` becomes selected item |
| `selected_item` | planning loop | `suggest_outfit`, `create_fit_card`, `compare_prices` | The listing being styled |
| `price_verdict` | `compare_prices` | `create_fit_card`, `handle_query` | Price context threaded into fit card and UI panel |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` | Passed directly as `create_fit_card`'s first argument |
| `fit_card` | `create_fit_card` | `handle_query` | Final output for panel 3 |
| `retry_attempted` | retry branch | UI note | Whether filters were loosened |
| `retry_loosened` | `retry_search` | `handle_query` | Human-readable list of what was relaxed |
| `error` | error branch | `handle_query` | If set, UI shows this and skips panels 2 and 3 |

The listings cache is the main optimization: `load_listings()` reads disk once and the result is passed as `listings=` into both `search_listings()` and `compare_prices()`. Call sites that omit `listings` still work, the tools load from disk internally.

---

## Error Handling

| Tool | Failure mode | What happens |
|---|---|---|
| `search_listings` | No keyword overlap or filter mismatch | Returns `[]`, never raises |
| `search_listings` | Empty description string | Returns `[]`, tokenizer produces empty set, all scores are 0 |
| `retry_search` | All 3 loosening steps fail | Returns `{"results": [], "loosened": [], ...}` |
| `suggest_outfit` | LLM API error | Returns fallback string, never empty |
| `create_fit_card` | Empty or whitespace `outfit` arg | Returns a fixed error string |
| `compare_prices` | Fewer than 3 comparables | Returns verdict but `comparable_count < 3`, fit card and UI suppress it |
| `compare_prices` | Empty item dict | Returns `{"verdict": "unavailable", ...}`, never raises |
| `run_agent` | Still no results after retry | Sets `session["error"]`, `handle_query` shows error in panel 1, panels 2-3 empty |

### Example Trace

Query: `"designer ballgown size XXS under $5"`

```
search_listings("designer ballgown", size="XXS", max_price=5) -> []
retry_search("designer ballgown", "XXS", 5):
  step 1: drop size -> []
  step 2: drop size, raise price to $6.25 -> []
  step 3: drop size + price -> []
  returns {"results": [], "loosened": [], ...}
agent sets error: "No listings found for 'designer ballgown' even after relaxing filters. Try different keywords."
```

The agent never crashes. The UI shows the error in the listing panel with actionable guidance.
