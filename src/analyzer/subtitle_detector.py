"""
Detect hardcoded subtitles or text overlays in video clips.

Strategy: extract 3 frames (25%, 50%, 75% of clip duration) and ask
Claude Vision whether each frame contains burned-in text AND where.

If text is found → apply FFmpeg blur to detected regions (instead of discarding).
Clips without a clean frame check are kept (fail-open to avoid losing content).

Cost: ~$0.002-0.004 per clip (3 small JPEG frames via claude-haiku).
"""
import contextlib
import json
import os
import subprocess
import tempfile
import base64
from pathlib import Path

import httpx
from rich.console import Console

from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL

console = Console()

VISION_MODEL = "claude-haiku-4-5"

# Video frame dimensions (9:16 1080p)
FRAME_W = 1080
FRAME_H = 1920


def _get_duration(video_path: Path) -> float | None:
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
    fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    tmp = Path(tmp_path)
    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-ss", str(timestamp),
            "-i", str(video_path),
            "-frames:v", "1",
            "-q:v", "4",
            "-vf", "scale=480:-2",
            "-loglevel", "error",
            str(tmp),
        ], capture_output=True)
        if tmp.exists():
            return tmp.read_bytes()
        return None
    finally:
        with contextlib.suppress(PermissionError, FileNotFoundError):
            tmp.unlink()


def _ask_claude_regions(frames: list[bytes]) -> dict:
    """
    Ask Claude Vision where text is located in the frames.
    Returns dict: {"has_text": bool, "regions": [{"y_start": float, "y_end": float}, ...]}
    y_start and y_end are fractions of frame height (0.0 = top, 1.0 = bottom).
    """
    content = []
    for frame_bytes in frames:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.standard_b64encode(frame_bytes).decode(),
            },
        })

    content.append({
        "type": "text",
        "text": (
            "Look at these video frames carefully.\n"
            "Does the video contain hardcoded text burned into the image?\n"
            "This includes: subtitles, captions, brand names, price tags, "
            "social handles (@username), hashtag overlays, countdown timers, "
            "or any text that is part of the video image itself.\n\n"
            "Ignore: tiny watermarks in the very corner (small logos < 5% of frame).\n\n"
            "Reply ONLY with valid JSON, no other text:\n"
            "- If NO text: {\"has_text\": false, \"regions\": []}\n"
            "- If text found: {\"has_text\": true, \"regions\": ["
            "{\"y_start\": 0.75, \"y_end\": 1.0, \"label\": \"subtitles\"}]}\n\n"
            "y_start and y_end are fractions of the frame height (0.0=top, 1.0=bottom).\n"
            "List ALL distinct text regions found. Be precise."
        ),
    })

    response = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": VISION_MODEL,
            "max_tokens": 200,
            "messages": [{"role": "user", "content": content}],
        },
        timeout=30,
    )
    raw = response.json()["content"][0]["text"].strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    return json.loads(raw)


def _blur_regions(clip_path: Path, regions: list[dict]) -> bool:
    """
    Apply FFmpeg Gaussian blur to each text region in-place.
    regions: list of {"y_start": float, "y_end": float}
    Returns True on success, False on failure.
    """
    if not regions:
        return True

    tmp_path = clip_path.parent / f"_blur_tmp_{clip_path.name}"

    # Build FFmpeg filter chain: for each region, crop → blur → overlay
    # Example for one region: [0:v]crop=W:H:0:Y[c];[c]boxblur=20:1[b];[0:v][b]overlay=0:Y[out]
    filter_parts = []
    prev_label = "0:v"

    for idx, region in enumerate(regions):
        y_px = int(region["y_start"] * FRAME_H)
        h_px = int((region["y_end"] - region["y_start"]) * FRAME_H)
        h_px = max(h_px, 2)  # avoid zero-height crop

        crop_label  = f"crop{idx}"
        blur_label  = f"blur{idx}"
        out_label   = f"out{idx}" if idx < len(regions) - 1 else "finalout"

        filter_parts.append(f"[{prev_label}]crop={FRAME_W}:{h_px}:0:{y_px}[{crop_label}]")
        filter_parts.append(f"[{crop_label}]boxblur=20:1[{blur_label}]")
        filter_parts.append(f"[{prev_label}][{blur_label}]overlay=0:{y_px}[{out_label}]")
        prev_label = out_label

    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(clip_path),
        "-filter_complex", filter_complex,
        "-map", f"[{prev_label}]",
        "-map", "0:a?",
        "-c:v", "h264_videotoolbox" if os.sys.platform == "darwin" else "libx264",
        "-b:v", "5M",
        "-c:a", "copy",
        "-loglevel", "error",
        str(tmp_path),
    ]

    result = subprocess.run(cmd, capture_output=True)
    if result.returncode == 0 and tmp_path.exists():
        clip_path.unlink(missing_ok=True)
        tmp_path.rename(clip_path)
        return True
    else:
        tmp_path.unlink(missing_ok=True)
        return False


def check_and_blur_text(clip_path: Path) -> tuple[bool, bool]:
    """
    Check for hardcoded text. If found, blur the regions in-place.

    Returns (had_text: bool, blur_ok: bool):
      - had_text=False → clip is clean, no action taken
      - had_text=True, blur_ok=True  → text found and blurred successfully
      - had_text=True, blur_ok=False → text found but blur failed (clip kept as-is)
    """
    if not ANTHROPIC_API_KEY:
        console.print("  [dim]Subtitle check skipped — no ANTHROPIC_API_KEY[/dim]")
        return False, False

    duration = _get_duration(clip_path)
    if not duration or duration < 0.5:
        return False, False

    timestamps = [duration * pct for pct in (0.25, 0.50, 0.75)]

    frames = []
    for ts in timestamps:
        frame_bytes = _extract_frame(clip_path, ts)
        if frame_bytes:
            frames.append(frame_bytes)

    if not frames:
        return False, False

    try:
        result = _ask_claude_regions(frames)
    except Exception as e:
        console.print(f"  [yellow]Subtitle check error: {e}[/yellow]")
        return False, False

    if not result.get("has_text"):
        return False, False

    regions = result.get("regions", [])
    labels  = [r.get("label", "text") for r in regions]
    console.print(
        f"  [yellow]Text detected:[/yellow] {', '.join(labels)} — blurring regions..."
    )

    if not regions:
        # Claude said has_text=true but gave no regions — fall back to blurring bottom 25%
        regions = [{"y_start": 0.75, "y_end": 1.0}]

    blur_ok = _blur_regions(clip_path, regions)
    if blur_ok:
        console.print(f"  [green]Blurred successfully[/green] → {clip_path.name}")
    else:
        console.print(f"  [red]Blur failed[/red] — keeping clip as-is")

    return True, blur_ok


# ---------------------------------------------------------------------------
# Legacy alias — kept for backwards compatibility
# ---------------------------------------------------------------------------
def has_hardcoded_text(clip_path: Path) -> bool:
    """Legacy function — now blurs instead of discarding. Always returns False."""
    check_and_blur_text(clip_path)
    return False
