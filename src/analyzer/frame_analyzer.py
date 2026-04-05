"""
Claude Vision frame analyzer.
Used for non-spoken videos where transcript isn't available.
Extracts frames and sends to Claude API for description.
"""
import base64
import subprocess
from pathlib import Path
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL
from rich.console import Console

console = Console()


def extract_frame(video_path: Path, timestamp: float, output_path: Path) -> bool:
    """Extract a single frame from video at given timestamp."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([
        "ffmpeg", "-y",
        "-ss", str(timestamp),
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "2",
        "-loglevel", "error",
        str(output_path)
    ], capture_output=True)
    return result.returncode == 0 and output_path.exists()


def describe_frame(frame_path: Path, product_name: str) -> str:
    """
    Send a frame to Claude Vision and get a description.
    Used to understand non-spoken video content.
    Cost: ~$0.01 per frame.
    """
    if not ANTHROPIC_API_KEY:
        return ""

    try:
        import httpx
        with open(frame_path, "rb") as f:
            img_b64 = base64.standard_b64encode(f.read()).decode()

        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 150,
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
                        {
                            "type": "text",
                            "text": f'Describe what is happening in this frame from a product video for "{product_name}". '
                                    f'Focus on: what is shown, what action is taking place, any text visible. '
                                    f'Be concise (2-3 sentences).'
                        }
                    ]
                }]
            },
            timeout=30,
        )
        return response.json()["content"][0]["text"].strip()
    except Exception as e:
        console.print(f"  [yellow]Frame description failed: {e}[/yellow]")
        return ""
