"""
Classify videos as spoken vs non-spoken.
Used to decide whether to run Whisper or Claude Vision.
"""
from pathlib import Path
from src.analyzer.scene_detector import has_audio_speech
from rich.console import Console

console = Console()


def classify_video(video_path: Path) -> str:
    """
    Returns 'spoken' or 'non-spoken'.
    """
    is_spoken = has_audio_speech(video_path)
    label = "spoken" if is_spoken else "non-spoken"
    console.print(f"  [{video_path.name}] classified as: {label}")
    return label
