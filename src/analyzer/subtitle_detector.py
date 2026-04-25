"""
Detect hardcoded subtitles or text overlays in video clips.

Strategy: extract 3 frames (25%, 50%, 75%) → Claude Vision returns
bounding boxes → FFmpeg blurs each region in-place using a proper
split/overlay chain.

Cost: ~$0.002-0.004 per clip (3 small JPEG frames via claude-haiku).
"""
import contextlib
import json
import os
import sys
import subprocess
import tempfile
import base64
from pathlib import Path

import httpx
from rich.console import Console

from config.settings import ANTHROPIC_API_KEY

console = Console()

VISION_MODEL  = "claude-haiku-4-5"
FRAME_W       = 1080
FRAME_H       = 1920
BLUR_SIGMA    = 30        # gblur strength — high enough to fully obscure text
BLUR_PADDING  = 0.03      # extra fraction added above/below each region (3% of frame)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_duration(video_path: Path) -> float | None:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "json", str(video_path),
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
            "-q:v", "2",            # higher quality → Claude sees text more clearly
            "-vf", "scale=720:-2",  # larger frame → more accurate coordinates
            "-loglevel", "error",
            str(tmp),
        ], capture_output=True)
        return tmp.read_bytes() if tmp.exists() else None
    finally:
        with contextlib.suppress(PermissionError, FileNotFoundError):
            tmp.unlink()


def _ask_claude(frames: list[bytes]) -> dict:
    """
    Single API call with all 3 frames. Conservative prompt — only flags
    prominent, clearly readable overlaid text. Returns {"has_text": bool, "regions": [...]}
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
            "Check these 3 video frames for hardcoded text overlaid on the video.\n\n"
            "ONLY flag text that meets ALL of these criteria:\n"
            "  1. Clearly readable words or numbers (not patterns, graphics or shapes)\n"
            "  2. Prominently sized — text height > 3% of frame height\n"
            "  3. Digitally overlaid on the video: subtitles, captions, social handle "
            "(@user), brand name burned in, price tag, countdown timer\n\n"
            "DO NOT flag:\n"
            "  - Text on physical objects being filmed (labels, boxes, signs in background)\n"
            "  - Tiny corner watermarks or logos < 3% of frame height\n"
            "  - Patterns, shapes or graphics that resemble text\n"
            "  - Blurry or illegible text\n\n"
            "Only answer YES if text is clearly present in the majority of frames. "
            "When in doubt, answer NO.\n\n"
            "Reply ONLY with valid JSON — no explanation, no markdown:\n"
            '{"has_text": false, "regions": []}\n'
            "or\n"
            '{"has_text": true, "regions": [{"y_start": 0.83, "y_end": 0.92, "label": "subtitles"}]}'
        ),
    })

    resp = httpx.post(
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
    raw = resp.json()["content"][0]["text"].strip()
    if "```" in raw:
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else parts[0]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def _merge_regions(regions: list[dict]) -> list[dict]:
    """
    Merge overlapping or adjacent regions and add padding.
    Also clamps values to [0.0, 1.0].
    """
    if not regions:
        return []

    # Add padding and clamp
    padded = []
    for r in regions:
        padded.append({
            "y_start": max(0.0, r["y_start"] - BLUR_PADDING),
            "y_end":   min(1.0, r["y_end"]   + BLUR_PADDING),
        })

    # Sort by y_start
    padded.sort(key=lambda r: r["y_start"])

    # Merge overlapping
    merged = [padded[0]]
    for r in padded[1:]:
        last = merged[-1]
        if r["y_start"] <= last["y_end"]:
            last["y_end"] = max(last["y_end"], r["y_end"])
        else:
            merged.append(r)

    return merged


def _blur_regions(clip_path: Path, regions: list[dict]) -> bool:
    """
    Apply strong Gaussian blur to each region in-place using FFmpeg.
    Uses a proper split/crop/blur/overlay chain — no label reuse bugs.
    """
    if not regions:
        return True

    tmp_path = clip_path.parent / f"_blurtmp_{clip_path.stem}.mp4"
    n = len(regions)

    # Build filter_complex with explicit split at every step
    # Single region:
    #   [0:v]split[base][src];[src]crop=W:H:0:Y[c];[c]gblur=sigma=S[b];[base][b]overlay=0:Y[out]
    # Two regions:
    #   [0:v]split[b0][s0];[s0]crop...[c0];[c0]gblur[bl0];[b0][bl0]overlay[step0];
    #   [step0]split[b1][s1];[s1]crop...[c1];[c1]gblur[bl1];[b1][bl1]overlay[out]

    parts = []
    current_in = "0:v"

    for idx, region in enumerate(regions):
        y_px = int(region["y_start"] * FRAME_H)
        h_px = max(int((region["y_end"] - region["y_start"]) * FRAME_H), 4)
        is_last = (idx == n - 1)
        out_label = "finalout" if is_last else f"step{idx}"

        base_lbl = f"base{idx}"
        src_lbl  = f"src{idx}"
        crop_lbl = f"crop{idx}"
        blur_lbl = f"blur{idx}"

        parts.append(f"[{current_in}]split[{base_lbl}][{src_lbl}]")
        parts.append(f"[{src_lbl}]crop={FRAME_W}:{h_px}:0:{y_px}[{crop_lbl}]")
        parts.append(f"[{crop_lbl}]gblur=sigma={BLUR_SIGMA}[{blur_lbl}]")
        parts.append(f"[{base_lbl}][{blur_lbl}]overlay=0:{y_px}[{out_label}]")

        current_in = out_label

    filter_complex = ";".join(parts)

    encoder = ["h264_videotoolbox"] if sys.platform == "darwin" else ["libx264"]
    cmd = [
        "ffmpeg", "-y",
        "-i", str(clip_path),
        "-filter_complex", filter_complex,
        "-map", "[finalout]",
        "-map", "0:a?",
        "-c:v", *encoder,
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

    # Log stderr for debugging
    if result.stderr:
        console.print(f"  [dim]FFmpeg blur error: {result.stderr.decode(errors='replace')[-300:]}[/dim]")
    tmp_path.unlink(missing_ok=True)
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_and_blur_text(clip_path: Path) -> tuple[bool, bool]:
    """
    Check for hardcoded text. If found, blur the regions in-place.

    Returns (had_text: bool, blur_ok: bool):
      had_text=False             → clip is clean
      had_text=True, blur_ok=True  → blurred successfully
      had_text=True, blur_ok=False → blur failed, clip kept as-is
    """
    if not ANTHROPIC_API_KEY:
        console.print("  [dim]Subtitle check skipped — no ANTHROPIC_API_KEY[/dim]")
        return False, False

    duration = _get_duration(clip_path)
    if not duration or duration < 0.5:
        return False, False

    frames = []
    for pct in (0.25, 0.50, 0.75):
        fb = _extract_frame(clip_path, duration * pct)
        if fb:
            frames.append(fb)

    if not frames:
        return False, False

    try:
        result = _ask_claude(frames)
    except Exception as e:
        console.print(f"  [yellow]Subtitle check error: {e}[/yellow]")
        return False, False

    if not result.get("has_text"):
        return False, False

    raw_regions = result.get("regions", [])
    labels = [r.get("label", "text") for r in raw_regions]

    # Fallback: if Claude said has_text but gave no coordinates, blur bottom 25%
    if not raw_regions:
        raw_regions = [{"y_start": 0.75, "y_end": 1.0}]
        labels = ["text (no coords)"]

    regions = _merge_regions(raw_regions)

    region_desc = "  ".join(
        f"{r.get('label', 'text')} [{r['y_start']:.0%}–{r['y_end']:.0%}]"
        for r in result.get("regions", raw_regions)
    )
    console.print(f"  [yellow]Text detected:[/yellow] {region_desc}")
    console.print(f"  Blurring {len(regions)} region(s) (sigma={BLUR_SIGMA}, padding±{BLUR_PADDING:.0%})...")

    blur_ok = _blur_regions(clip_path, regions)
    if blur_ok:
        console.print(f"  [green]✓ Blurred[/green] {clip_path.name}")
    else:
        console.print(f"  [red]✗ Blur failed[/red] — clip kept as-is")

    return True, blur_ok
