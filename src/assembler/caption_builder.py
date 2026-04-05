"""
Auto-caption builder from Whisper transcript segments.
Burns captions onto video using MoviePy TextClip.

NOTE: Not yet implemented — planned for next session.
Will use Whisper word-level timestamps for accurate sync.
"""
from pathlib import Path
from rich.console import Console

console = Console()


def build_captions(transcript: dict, video_duration: float) -> list[dict]:
    """
    Convert Whisper segments into caption entries.
    Returns list of {start, end, text} dicts.
    """
    captions = []
    for seg in transcript.get("segments", []):
        if seg["end"] > video_duration:
            break
        captions.append({
            "start": seg["start"],
            "end":   seg["end"],
            "text":  seg["text"].strip(),
        })
    return captions


def burn_captions(video_clip, captions: list[dict]):
    """
    Burn captions onto a MoviePy VideoClip.
    Returns new clip with captions.

    TODO: Implement with MoviePy TextClip in next session.
    """
    console.print("  [yellow]Caption burn-in not yet implemented — skipping[/yellow]")
    return video_clip
