"""
FFmpeg-based clip extraction.
Cuts video segments based on scene timestamps.
"""
import subprocess
from pathlib import Path
from rich.console import Console

console = Console()


def cut_clip(
    video_path: Path,
    start: float,
    duration: float,
    output_path: Path,
) -> bool:
    """
    Extract a clip from video using FFmpeg stream copy (fast, lossless).
    Returns True on success.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", str(video_path),
        "-t", str(duration),
        "-c:v", "libx264",
        "-c:a", "aac",
        "-loglevel", "error",
        str(output_path)
    ], capture_output=True, text=True)

    if result.returncode != 0:
        console.print(f"  [red]clip cut failed: {output_path.name} — {result.stderr[:100]}[/red]")
        return False
    return True


def extract_frame(video_path: Path, timestamp: float, output_path: Path) -> bool:
    """Extract a single JPEG frame at given timestamp."""
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
