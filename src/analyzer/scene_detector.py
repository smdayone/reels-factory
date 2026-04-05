"""
FFmpeg-based scene cut detector.
Finds natural cut points in a video without AI.
Works on both spoken and non-spoken videos.
"""
import subprocess
import json
import re
from pathlib import Path
from config.settings import SCENE_THRESHOLD
from rich.console import Console

console = Console()


def detect_scenes(video_path: Path, threshold: float = SCENE_THRESHOLD) -> list[float]:
    """
    Returns list of timestamps (seconds) where scene cuts occur.
    Uses FFmpeg select filter — no AI cost.
    """
    cmd = [
        "ffmpeg",
        "-i", str(video_path),
        "-vf", f"select='gt(scene,{threshold})',metadata=print",
        "-an",
        "-f", "null",
        "-"
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stderr

    # Parse timestamps from ffmpeg output
    timestamps = [0.0]  # always start from beginning
    pattern = re.compile(r"pts_time:([\d.]+)")
    for match in pattern.finditer(output):
        ts = float(match.group(1))
        timestamps.append(ts)

    # Get video duration
    duration = get_video_duration(video_path)
    if duration:
        timestamps.append(duration)

    timestamps = sorted(set(timestamps))
    console.print(f"  Found {len(timestamps)-1} scenes in {video_path.name}")
    return timestamps


def get_video_duration(video_path: Path) -> float | None:
    """Get video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "json",
        str(video_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except Exception:
        return None


def has_audio_speech(video_path: Path) -> bool:
    """
    Quick check: does this video contain speech?
    Uses audio volume analysis — if mostly silent or music, returns False.
    """
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vn", "-af", "silencedetect=n=-30dB:d=0.5",
        "-f", "null", "-"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    # Heuristic: if less than 30% of video is non-silent, likely no speech
    silence_matches = re.findall(r"silence_duration: ([\d.]+)", result.stderr)
    duration = get_video_duration(video_path)
    if not duration or not silence_matches:
        return True  # assume speech if we can't detect
    total_silence = sum(float(s) for s in silence_matches)
    speech_ratio = 1 - (total_silence / duration)
    return speech_ratio > 0.3
