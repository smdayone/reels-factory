"""
faster-whisper transcription (drop-in replacement for openai-whisper).
- No separate torch install required
- Works on Python 3.14
- Faster than openai-whisper on CPU (~4x)
- Downloads model on first run (~145MB for 'base')
"""
from pathlib import Path
from config.settings import WHISPER_MODEL
from src.utils.system_check import check_ram
from rich.console import Console

console = Console()

_model = None  # Lazy load — load once, reuse


def get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        console.print(f"[blue]Loading Whisper model: {WHISPER_MODEL}[/blue]")
        console.print("(First run downloads ~145MB — subsequent runs are instant)")
        check_ram("Whisper transcription")
        _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        console.print("[green]Whisper loaded[/green]")
    return _model


def transcribe(video_path: Path) -> dict:
    """
    Transcribe audio from video file.
    Returns dict with 'text' and 'segments' keys — same shape as openai-whisper.
    segments: list of {start, end, text}
    """
    model = get_model()
    console.print(f"  Transcribing: {video_path.name}...")

    segments_iter, info = model.transcribe(
        str(video_path),
        language="en",
        word_timestamps=True,
    )

    segments = []
    full_text_parts = []
    for seg in segments_iter:
        segments.append({
            "start": seg.start,
            "end":   seg.end,
            "text":  seg.text.strip(),
        })
        full_text_parts.append(seg.text.strip())

    result = {
        "text": " ".join(full_text_parts),
        "segments": segments,
        "language": info.language,
    }

    console.print(f"  [green]{len(segments)} segments transcribed[/green]")
    return result


def save_transcript(transcript: dict, output_path: Path):
    """Save transcript as JSON and plain text."""
    import json
    output_path.parent.mkdir(parents=True, exist_ok=True)

    json_path = output_path.with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(transcript, f, indent=2, ensure_ascii=False)

    txt_path = output_path.with_suffix(".txt")
    txt_path.write_text(transcript.get("text", ""), encoding="utf-8")

    console.print("  [green]Transcript saved[/green]")
