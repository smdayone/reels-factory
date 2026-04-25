"""
Generate problem-solution focused video scripts using Claude API.
No price mentions. No sales pitch.
Focus: buyer persona → pain point → product as solution.
"""
import re
import httpx
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL, TARGET_DURATION
from src.utils.languages import SCRIPT_LANGUAGE_INSTRUCTION
from rich.console import Console

console = Console()

SCRIPT_PROMPT = """You are an expert short-form video scriptwriter specializing in
organic TikTok and Instagram Reels content for e-commerce products.

Product: {product_name}
Category: {category}
Target duration: {duration} seconds
Buyer persona: {persona}
Key problem identified from video analysis: {problem_summary}
Target language: {language_instruction}

Write OVERLAY TEXT for each section. These words appear burned into the video frame —
they are NOT spoken aloud. Think bold, punchy captions — not sentences.

Structure:
1. HOOK: relatable problem, NO product name — max 7 words
2. PROBLEM: deepen the pain — max 8 words
3. SOLUTION: introduce the product naturally — max 8 words
4. PROOF: one concrete benefit or result — max 8 words
5. CTA: (will be overridden externally — write a placeholder)
6. CAPTION: TikTok/IG post caption — see rules below
7. EMOTION: single emotional statement for full-screen text — max 8 words, raw feeling
8. PLOT_HOOK: teaser text during creator clip — max 8 words, creates curiosity
9. PLOT_REVEAL: reveal text after creator clip — max 8 words, introduces the product moment

OVERLAY RULES (sections 1-5, 7-9):
- Each section = 1 short line, max 8 words
- NO punctuation of any kind — no em-dash, no comma, no period, no colon, no slash
- NO emoji, NO special characters, NO symbols
- NO abbreviations with symbols (write out words in full)
- Sentence case (first word capitalised only)
- Write in the target language above — cultural adaptation, not literal translation
- Never mention price, never say buy/purchase/shop/order
- No superlatives without evidence
- If a section would be too similar to another section in this same video, write SKIP
  instead of forcing a weak or redundant line. A skipped section shows no text overlay.
  Only skip if genuinely necessary — do not skip HOOK or CTA.

CAPTION RULES (section 6):
- SINGLE LINE only — no line breaks anywhere
- MAX 100 characters TOTAL including the hashtags
- End with exactly 2-3 hashtags in ALL LOWERCASE (e.g. #myprod #niche #trending)
- No uppercase letters in hashtags
- Rotate tone every video — pick ONE angle different from any recent caption:
  storytelling / rhetorical question / bold claim / relatable confession / surprising fact
- Do NOT repeat the hook verbatim, do NOT open with "I" or "We"
- Emoji allowed but counts toward the 100-char limit
- Never mention price

Output format (one line per section, nothing else):
HOOK: [text]
PROBLEM: [text]
SOLUTION: [text]
PROOF: [text]
CTA: [text]
CAPTION: [text]
EMOTION: [text]
PLOT_HOOK: [text]
PLOT_REVEAL: [text]
PERSONA_NOTE: [one line explaining why this resonates with the persona]"""


def generate_script(
    product_name: str,
    category: str,
    persona: dict,
    problem_summary: str,
    er_references: list[dict] | None = None,
    language: str = "en",
    recent_texts: "dict[str, list[str]] | None" = None,
) -> dict:
    """Generate full video script in the requested language. Returns dict with sections.

    language: ISO 639-1 code — must be a key in SUPPORTED_LANGUAGES (en, es, de, fr, it).
    er_references: list of {"caption": str, "er": float} dicts sorted by ER desc.
    Top entries are appended to the prompt as style inspiration for the CAPTION section.
    """
    if not ANTHROPIC_API_KEY:
        console.print("  [yellow]No API key — skipping script generation[/yellow]")
        return {}

    lang_instruction = SCRIPT_LANGUAGE_INSTRUCTION.get(
        language, SCRIPT_LANGUAGE_INSTRUCTION["en"]
    )

    prompt = SCRIPT_PROMPT.format(
        product_name=product_name,
        category=category,
        duration=TARGET_DURATION,
        persona=f"{persona['name']} ({persona['age_range']}) — {persona['main_pain']}",
        problem_summary=problem_summary,
        language_instruction=lang_instruction,
    )

    if er_references:
        prompt += "\n\nHIGH-PERFORMING CAPTIONS (use as style/tone inspiration for CAPTION only — do not copy verbatim):\n"
        for ref in er_references:
            prompt += f"- ER {ref.get('er', '?')}%: {ref.get('caption', '').strip()}\n"

    # Inject recently used overlay texts so Claude can actively avoid repeating them
    _AVOID_MAP = {
        "HOOK":        "hook_text",
        "PROBLEM":     "problem_text",
        "SOLUTION":    "solution_text",
        "PROOF":       "proof_text",
        "EMOTION":     "emotion_text",
        "PLOT_HOOK":   "plot_hook_text",
        "PLOT_REVEAL": "plot_reveal_text",
        "CAPTION":     "caption",
    }
    if recent_texts:
        avoid_lines = []
        for label, hist_key in _AVOID_MAP.items():
            values = recent_texts.get(hist_key, [])
            if values:
                avoid_lines.append(f"{label}: {' | '.join(values)}")
        if avoid_lines:
            prompt += (
                "\n\nAVOID REPETITION — these texts appeared in recent videos for this product. "
                "Do NOT reuse them verbatim or as close paraphrases. Find fresh angles:\n"
                + "\n".join(avoid_lines)
            )

    try:
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30,
        )
        raw = response.json()["content"][0]["text"]

        # Parse sections
        sections = {}
        for section in ["HOOK", "PROBLEM", "SOLUTION", "PROOF", "CTA", "CAPTION",
                         "EMOTION", "PLOT_HOOK", "PLOT_REVEAL", "PERSONA_NOTE"]:
            match = re.search(rf"{section}:\s*(.+?)(?=\n[A-Z_]+:|$)", raw, re.DOTALL)
            if match:
                sections[section.lower()] = match.group(1).strip()

        return sections
    except Exception as e:
        console.print(f"  [red]Script generation failed: {e}[/red]")
        return {}
