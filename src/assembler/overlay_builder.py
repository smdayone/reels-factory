"""
Text overlay builder for hook text and CTA.
Adds styled text overlays using MoviePy TextClip.

NOTE: Not yet implemented — planned for next session.
"""
from pathlib import Path
from rich.console import Console

console = Console()


def add_hook_overlay(video_clip, hook_text: str):
    """
    Add hook text overlay to first 3 seconds of video.

    TODO: Implement with MoviePy TextClip in next session.
    """
    console.print("  [yellow]Hook overlay not yet implemented — skipping[/yellow]")
    return video_clip


def add_cta_overlay(video_clip, cta_text: str):
    """
    Add CTA text overlay to last 3 seconds of video.

    TODO: Implement with MoviePy TextClip in next session.
    """
    console.print("  [yellow]CTA overlay not yet implemented — skipping[/yellow]")
    return video_clip
