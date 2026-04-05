"""
Demucs voice/music separator.
Model: htdemucs (lightest — recommended for CPU).
Separates 'vocals' and 'no_vocals' stems.
Downloads model on first run (~80MB).
"""
import subprocess
from pathlib import Path
from config.settings import DEMUCS_MODEL
from src.utils.system_check import check_ram
from rich.console import Console

console = Console()


def separate_voice(video_path: Path, output_dir: Path) -> dict[str, Path] | None:
    """
    Runs Demucs on video file.
    Returns paths to: vocals.wav, no_vocals.wav
    Processing time: ~2-5 min per video on CPU — normal, expected.
    """
    if not check_ram("Demucs voice separation"):
        return None

    output_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"  Separating voice from: {video_path.name}")
    console.print(f"  [yellow]This takes 2-5 minutes on CPU. Do not close the window.[/yellow]")

    cmd = [
        "python", "-m", "demucs",
        "--name", DEMUCS_MODEL,
        "--out", str(output_dir),
        "--two-stems", "vocals",  # only split vocals vs no_vocals
        str(video_path)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        console.print(f"  [red]Demucs error: {result.stderr[:200]}[/red]")
        return None

    # Demucs saves to: output_dir/htdemucs/[filename]/vocals.wav
    stem_dir = output_dir / DEMUCS_MODEL / video_path.stem
    vocals_path = stem_dir / "vocals.wav"
    no_vocals_path = stem_dir / "no_vocals.wav"

    if not vocals_path.exists():
        console.print(f"  [red]Output files not found in {stem_dir}[/red]")
        return None

    console.print(f"  [green]Voice separated successfully[/green]")
    return {
        "vocals": vocals_path,
        "no_vocals": no_vocals_path,
    }
