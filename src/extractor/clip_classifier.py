"""
Classify video segments into categories using transcript keywords
(for spoken videos) or Claude Vision (for non-spoken videos).
"""
import base64
from pathlib import Path
from config.settings import CLIP_KEYWORDS, ANTHROPIC_API_KEY, CLAUDE_MODEL
from rich.console import Console

console = Console()

CATEGORIES = ["hook", "problem", "solution", "demo", "unboxing", "cta", "unclassified"]


def classify_by_transcript(segment_text: str) -> str:
    """
    Classify a clip by searching for keywords in its transcript.
    Fast, free, works offline.
    """
    text_lower = segment_text.lower()
    scores = {cat: 0 for cat in CATEGORIES if cat != "unclassified"}

    for category, keywords in CLIP_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                scores[category] += 1

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "unclassified"


def classify_by_vision(frame_path: Path, product_name: str) -> str:
    """
    Classify a video frame using Claude Vision.
    Used for non-spoken videos where transcript isn't available.
    Cost: ~$0.01 per frame analyzed.
    """
    if not ANTHROPIC_API_KEY:
        return "unclassified"

    with open(frame_path, "rb") as f:
        img_b64 = base64.standard_b64encode(f.read()).decode()

    prompt = f"""You are analyzing a frame from a product marketing video for "{product_name}".

Classify this frame into exactly ONE of these categories:
- hook: Attention-grabbing opener, bold statement, surprising visual
- problem: Showing a pain point, frustration, or problem the product solves
- solution: Showing the product solving the problem, positive outcome
- demo: Step-by-step product demonstration, how-to usage
- unboxing: Opening package, first look at product
- cta: Call to action, directing viewer to take next step
- unclassified: None of the above clearly apply

Reply with ONLY the category name, nothing else."""

    try:
        import httpx
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 20,
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": img_b64,
                            }
                        },
                        {"type": "text", "text": prompt}
                    ]
                }]
            },
            timeout=30,
        )
        result = response.json()["content"][0]["text"].strip().lower()
        return result if result in CATEGORIES else "unclassified"
    except Exception as e:
        console.print(f"  [yellow]Vision classification failed: {e}[/yellow]")
        return "unclassified"
