"""
app.py

Gradio interface for FitFindr. Wires handle_query() to run_agent() and maps
session results to the three output panels. Also handles style profile memory:
loading a saved wardrobe by user ID and saving new items to a profile.

Run with:
    python app.py

Then open the localhost URL shown in your terminal (usually http://localhost:7860).
"""

import gradio as gr

from agent import run_agent
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe


# ── Nord dark theme ───────────────────────────────────────────────────────────

_nord_blue = gr.themes.Color(
    c50="#ECEFF4", c100="#E5E9F0", c200="#D8DEE9", c300="#88C0D0",
    c400="#81A1C1", c500="#5E81AC", c600="#4C566A", c700="#434C5E",
    c800="#3B4252", c900="#2E3440", c950="#242933",
)
_nord_neutral = gr.themes.Color(
    c50="#ECEFF4", c100="#E5E9F0", c200="#D8DEE9", c300="#4C566A",
    c400="#434C5E", c500="#3B4252", c600="#2E3440", c700="#272C38",
    c800="#22262F", c900="#1E2128", c950="#191D24",
)

_theme = gr.themes.Base(
    primary_hue=_nord_blue,
    neutral_hue=_nord_neutral,
    font=gr.themes.GoogleFont("Inter"),
    font_mono=gr.themes.GoogleFont("JetBrains Mono"),
).set(
    body_background_fill="#2E3440",
    body_background_fill_dark="#2E3440",
    body_text_color="#D8DEE9",
    body_text_color_dark="#D8DEE9",
    block_background_fill="#3B4252",
    block_background_fill_dark="#3B4252",
    block_border_color="#4C566A",
    block_border_color_dark="#4C566A",
    block_label_text_color="#88C0D0",
    block_label_text_color_dark="#88C0D0",
    block_title_text_color="#88C0D0",
    block_title_text_color_dark="#88C0D0",
    input_background_fill="#434C5E",
    input_background_fill_dark="#434C5E",
    input_border_color="#4C566A",
    input_border_color_dark="#4C566A",
    input_placeholder_color="#81A1C1",
    input_placeholder_color_dark="#81A1C1",
    button_primary_background_fill="#5E81AC",
    button_primary_background_fill_dark="#5E81AC",
    button_primary_background_fill_hover="#81A1C1",
    button_primary_background_fill_hover_dark="#81A1C1",
    button_primary_text_color="#ECEFF4",
    button_primary_text_color_dark="#ECEFF4",
    button_secondary_background_fill="#434C5E",
    button_secondary_background_fill_dark="#434C5E",
    button_secondary_background_fill_hover="#4C566A",
    button_secondary_background_fill_hover_dark="#4C566A",
    button_secondary_text_color="#D8DEE9",
    button_secondary_text_color_dark="#D8DEE9",
    shadow_drop="none",
    shadow_drop_lg="none",
)

_CSS = """
.error-banner {
    background: #3B4252;
    border-left: 3px solid #BF616A;
    color: #BF616A !important;
    padding: 10px 14px;
    border-radius: 4px;
    margin-bottom: 4px;
}
.error-banner p { margin: 0; }
"""


# ── query handler ─────────────────────────────────────────────────────────────


def handle_query(
    user_query: str,
    wardrobe_choice: str,
) -> tuple:
    """Called by Gradio when the user clicks 'Find it' or presses Enter."""
    no_results = (gr.update(value="", visible=False), "", "", "")

    if not user_query.strip():
        return (gr.update(value="Please enter a search query.", visible=True), "", "", "")

    wardrobe = get_example_wardrobe() if wardrobe_choice == "Example wardrobe" else get_empty_wardrobe()
    session = run_agent(query=user_query, wardrobe=wardrobe)

    if session["error"]:
        return (gr.update(value=session["error"], visible=True), "", "", "")

    item = session["selected_item"]
    verdict = session.get("price_verdict") or {}
    listing_text = (
        f"{item['title']}\n"
        f"${item['price']:.2f} on {item['platform']}\n"
        f"Size: {item['size']} | Condition: {item['condition']}\n"
        f"Style: {', '.join(item['style_tags'])}\n\n"
        f"{item['description']}"
    )
    if verdict.get("verdict") and verdict.get("comparable_count", 0) >= 3:
        listing_text += (
            f"\n\nPrice verdict: {verdict['verdict']} "
            f"(avg comparable: ${verdict['avg_price']:.0f})"
        )
    if session.get("retry_loosened"):
        listing_text += "\n\nNote: " + "; ".join(session["retry_loosened"]) + "."

    return (
        gr.update(value="", visible=False),
        listing_text,
        session["outfit_suggestion"],
        session["fit_card"],
    )


def clear_outputs() -> tuple:
    return (gr.update(value="", visible=False), "", "", "")


# ── interface ─────────────────────────────────────────────────────────────────

EXAMPLE_QUERIES = [
    "vintage graphic tee under $30",
    "90s track jacket in size M",
    "flowy midi skirt under $40",
    "black combat boots size 8",
    "designer ballgown size XXS under $5",
]


def build_interface():
    with gr.Blocks(title="FitFindr") as demo:
        gr.Markdown(
            "# FitFindr\n"
            "Find secondhand pieces and get outfit ideas based on your wardrobe. "
            "Describe what you're looking for — include size and price to filter."
        )

        # ── Settings (collapsed by default) ─────────────────────────────────
        with gr.Accordion("Settings", open=False):
            wardrobe_choice = gr.Radio(
                choices=["Example wardrobe", "Empty wardrobe (new user)"],
                value="Example wardrobe",
                label="Wardrobe",
            )

        # ── Search row ───────────────────────────────────────────────────────
        with gr.Row():
            query_input = gr.Textbox(
                label="What are you looking for?",
                placeholder="e.g. vintage graphic tee under $30, size M",
                lines=2,
                scale=3,
            )
            with gr.Column(scale=1, min_width=120):
                submit_btn = gr.Button("Find it", variant="primary")
                clear_btn = gr.Button("Clear", variant="secondary")

        # ── Error banner (hidden until needed) ───────────────────────────────
        error_md = gr.Markdown(visible=False, elem_classes=["error-banner"])

        # ── Results panels ───────────────────────────────────────────────────
        with gr.Row():
            listing_output = gr.Textbox(
                label="Top listing found",
                lines=10,
                interactive=False,
            )
            outfit_output = gr.Markdown(label="Outfit idea", min_height=200)
            fitcard_output = gr.Markdown(label="Your fit card", min_height=200)

        # ── Example queries ──────────────────────────────────────────────────
        gr.Markdown("**Try a sample query:**")
        with gr.Row():
            for q in EXAMPLE_QUERIES:
                gr.Button(q, size="sm").click(
                    fn=lambda q=q: (q, gr.update(value="", visible=False), "", "", ""),
                    outputs=[query_input, error_md, listing_output, outfit_output, fitcard_output],
                )

        # ── Event wiring ─────────────────────────────────────────────────────
        all_outputs = [error_md, listing_output, outfit_output, fitcard_output]
        search_inputs = [query_input, wardrobe_choice]

        submit_btn.click(fn=handle_query, inputs=search_inputs, outputs=all_outputs, show_progress="full")
        query_input.submit(fn=handle_query, inputs=search_inputs, outputs=all_outputs, show_progress="full")
        clear_btn.click(fn=clear_outputs, outputs=all_outputs)

    return demo


if __name__ == "__main__":
    demo = build_interface()
    demo.launch(theme=_theme, css=_CSS)
