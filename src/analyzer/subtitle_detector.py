"""
Detect hardcoded subtitles or text overlays in video clips.

Strategy: extract 3 frames (25%, 50%, 75% of clip duration) and ask
Claude Vision whether each frame contains burned-in text.

If ANY frame contains text → the clip is discarded.
Clips without a clean frame check are kept (fail-open to avoid losing content).

Cost: ~$0.002-0.004 per clip (3 small JPEG frames via claude-haiku).
"""
import json
import subprocess
import tempfile
import base64
from pathlib import Path

import httpx
from rich.console import Console

from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL

console = Console()

# Use the cheapest model for this binary YES/NO task
VISION_MODEL = "claude-haiku-4-5"


def _get_duration(video_path: Path) -> float | None:
    """Get clip duration via ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "json",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except Exception:
        return None


def _extract_frame(video_path: Path, timestamp: float) -> bytes | None:
    """Extract one JPEG frame at timestamp, return raw bytes."""
    tmp = Path(tempfile.mktemp(suffix=".jpg"))
    subprocess.run([
        "ffmpeg", "-y",
        "-ss", str(timestamp),
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "4",          # quality 4 = small file, good enough for text detection
        "-vf", "scale=480:-2", # downscale to save API tokens
        "-loglevel", "error",
        str(tmp),
    ], capture_output=True)
    if tmp.exists():
        data = tmp.read_bytes()
        tmp.unlink(missing_ok=True)
        return data
    return None


def has_hardcoded_text(clip_path: Path) -> bool:
    """
    Returns True if the clip contains hardcoded subtitles or text overlays.
    Returns False if clean or if the check could not be completed.

    What counts as hardcoded text:
    - Subtitles or captions at the bottom of the frame
    - Brand name / product name burned into the video
    - Price tags, promotional text, countdown timers
    - Social media handles (@username, #hashtag overlays)
    - Any text that is part of the video image itself (not a UI element)
    """
    if not ANTHROPIC_API_KEY:
        console.print("  [dim]Subtitle check skipped — no ANTHROPIC_API_KEY[/dim]")
        return False

    duration = _get_duration(clip_path)
    if not duration or duration < 0.5:
        return False

    # Sample at 25%, 50%, 75% of the clip
    timestamps = [duration * pct for pct in (0.25, 0.50, 0.75)]

    content = []
    frames_added = 0
    for ts in timestamps:
        frame_bytes = _extract_frame(clip_path, ts)
        if frame_bytes:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(frame_bytes).decode(),
                },
            })
            frames_added += 1

    if frames_added == 0:
        return False

    content.append({
        "type": "text",
        "text": (
            "Look at these video frames.\n"
            "Does the video contain ANY hardcoded text burned into the image?\n"
            "This includes: subtitles, captions, brand names, price tags, "
            "social handles, hashtag overlays, countdown timers, or any other "
            "text that is part of the video itself.\n\n"
            "Ignore: UI elements, watermarks in the very corner (small logos are OK).\n\n"
            "Reply with ONLY: YES or NO"
        ),
    })

    try:
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": VISION_MODEL,
                "max_tokens": 5,
                "messages": [{"role": "user", "content": content}],
            },
            timeout=30,
        )
        answer = response.json()["content"][0]["text"].strip().upper()
        return answer.startswith("YES")

    except Exception as e:
        console.print(f"  [yellow]Subtitle check error: {e}[/yellow]")
        return False  # fail-open: keep the clip if check fails
