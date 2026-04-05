"""
Identify buyer persona from product name and video transcript context.
"""
import json
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL
from rich.console import Console

console = Console()

_DEFAULT_PERSONA = {
    "name": "General Consumer",
    "age_range": "25-40",
    "main_pain": "looking for a better solution",
    "language": "frustrated, overwhelmed, tired",
    "platform": "TikTok"
}


def identify_persona(product_name: str, clip_transcripts: list[str]) -> dict:
    """
    Identify buyer persona from product name and extracted clip content.
    Returns persona dict.
    """
    if not ANTHROPIC_API_KEY:
        return _DEFAULT_PERSONA

    combined_text = " ".join(clip_transcripts[:5])  # use first 5 transcripts

    prompt = f"""Based on this product "{product_name}" and these video transcript excerpts:
{combined_text[:1000]}

Identify the most likely buyer persona in this format (JSON only, no other text):
{{
  "name": "persona name (e.g. Busy Mom, Remote Worker, Fitness Enthusiast)",
  "age_range": "e.g. 25-35",
  "main_pain": "the single biggest frustration this persona has that this product addresses",
  "language": "2-3 words they would actually use to describe their problem",
  "platform": "TikTok or Instagram — which platform this persona uses more"
}}"""

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
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30,
        )
        text = response.json()["content"][0]["text"].strip()
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        console.print(f"  [yellow]Persona identification failed: {e}[/yellow]")
        return _DEFAULT_PERSONA
