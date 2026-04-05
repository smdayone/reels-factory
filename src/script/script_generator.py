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

Write a video script that follows this structure:
1. HOOK (3-5s): A relatable statement about the problem — NO product mention yet
2. PROBLEM (5-8s): Deepen the pain point — make the viewer say "that's me"
3. SOLUTION (8-15s): Introduce the product as the natural answer — show, don't sell
4. PROOF (5-8s): One specific benefit or result — concrete, believable
5. CTA (3-5s): Soft call to action — "comment X if you want to know more" or "save this"

STRICT RULES:
- Never mention price
- Never say "buy", "purchase", "shop", "order", "affordable", "cheap"
- Never use superlatives like "best", "amazing", "incredible" without evidence
- Write in natural spoken English — how a real person talks, not an ad
- The hook must be a problem statement, not a product statement
- Maximum {duration} seconds when spoken at normal pace (~2.5 words/second)

Output format:
HOOK: [script text]
PROBLEM: [script text]
SOLUTION: [script text]
PROOF: [script text]
CTA: [script text]
PERSONA_NOTE: [one line explaining why this script resonates with this persona]"""


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
