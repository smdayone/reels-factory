"""
Generate problem-solution focused video scripts using Claude API.
No price mentions. No sales pitch.
Focus: buyer persona → pain point → product as solution.
"""
import re
import httpx
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL, TARGET_DURATION
from rich.console import Console

console = Console()

SCRIPT_PROMPT = """You are an expert short-form video scriptwriter specializing in
organic TikTok and Instagram Reels content for e-commerce products.

Product: {product_name}
Category: {category}
Target duration: {duration} seconds
Buyer persona: {persona}
Key problem identified from video analysis: {problem_summary}

Write OVERLAY TEXT for each section. These words appear burned into the video frame —
they are NOT spoken aloud. Think bold, punchy captions — not sentences.

Structure:
1. HOOK: relatable problem, NO product name — max 7 words
2. PROBLEM: deepen the pain — max 8 words
3. SOLUTION: introduce the product naturally — max 8 words
4. PROOF: one concrete benefit or result — max 8 words
5. CTA: (will be overridden externally — write a placeholder)

OVERLAY RULES:
- Each section = 1 short line, max 8 words
- NO punctuation except "—" for pauses
- NO emoji
- Sentence case (first word capitalised only)
- Never mention price, never say buy/purchase/shop/order
- No superlatives without evidence

Output format (one line per section, nothing else):
HOOK: [text]
PROBLEM: [text]
SOLUTION: [text]
PROOF: [text]
CTA: [text]
PERSONA_NOTE: [one line explaining why this resonates with the persona]"""


def generate_script(
    product_name: str,
    category: str,
    persona: dict,
    problem_summary: str,
) -> dict:
    """Generate full video script. Returns dict with sections."""
    if not ANTHROPIC_API_KEY:
        console.print("  [yellow]No API key — skipping script generation[/yellow]")
        return {}

    prompt = SCRIPT_PROMPT.format(
        product_name=product_name,
        category=category,
        duration=TARGET_DURATION,
        persona=f"{persona['name']} ({persona['age_range']}) — {persona['main_pain']}",
        problem_summary=problem_summary,
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
                "max_tokens": 600,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30,
        )
        raw = response.json()["content"][0]["text"]

        # Parse sections
        sections = {}
        for section in ["HOOK", "PROBLEM", "SOLUTION", "PROOF", "CTA", "PERSONA_NOTE"]:
            match = re.search(rf"{section}:\s*(.+?)(?=\n[A-Z_]+:|$)", raw, re.DOTALL)
            if match:
                sections[section.lower()] = match.group(1).strip()

        return sections
    except Exception as e:
        console.print(f"  [red]Script generation failed: {e}[/red]")
        return {}
