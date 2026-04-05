"""
Whisper-based audio transcription.
Downloads model on first run (~140MB for 'base').
Runs fully offline after that.
"""
import whisper
import torch
from pathlib import Path
from config.settings import WHISPER_MODEL
from src.utils.system_check import check_ram
from rich.console import Console

console = Console()

_model = None  # Lazy load — load once, reuse


def get_model():
    global _model
    if _model is None:
        console.print(f"[blue]Loading Whisper model: {WHISPER_MODEL}[/blue]")
        console.print("(First run downloads ~140MB — subsequent runs are instant)")
        check_ram("Whisper transcription")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        console.print(f"  Running on: {device.upper()}")
        _model = whisper.load_model(WHISPER_MODEL, device=device)
        console.print(f"[green]Whisper loaded[/green]")
    return _model


def transcribe(video_path: Path) -> dict:
    """
    Transcribe audio from video file.
    Returns Whisper result dict with 'text' and 'segments' keys.
    segments: list of {start, end, text}
    """
    model = get_model()
    console.print(f"  Transcribing: {video_path.name}...")
    result = model.transcribe(
        str(video_path),
        language="en",
        task="transcribe",
        word_timestamps=True,
        verbose=False,
    )
    n_segments = len(result.get("segments", []))
    console.print(f"  [green]{n_segments} segments transcribed[/green]")
    return result


def save_transcript(transcript: dict, output_path: Path):
    """Save transcript as JSON and plain text."""
    import json
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # JSON (full, with timestamps)
    json_path = output_path.with_suffix(".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(transcript, f, indent=2, ensure_ascii=False)

    # Plain text
    txt_path = output_path.with_suffix(".txt")
    txt_path.write_text(transcript.get("text", ""), encoding="utf-8")

    console.print(f"  [green]Transcript saved[/green]")
