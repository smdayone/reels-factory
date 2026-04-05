"""
Assemble final video from extracted clips + voice + music.
Uses MoviePy for composition, FFmpeg for final encoding.
"""
import random
from pathlib import Path
from datetime import datetime
from moviepy.editor import (
    VideoFileClip, AudioFileClip, CompositeVideoClip,
    CompositeAudioClip, concatenate_videoclips
)
from config.settings import (
    TARGET_DURATION, MIN_CLIP_DURATION,
    get_keyword_paths, MUSIC_DIR
)
from src.utils.system_check import check_ram
from rich.console import Console

console = Console()

# Assembly order: which clip categories to use and in what order
ASSEMBLY_ORDER = ["hook", "problem", "solution", "demo", "cta"]


def select_clips(keyword: str, max_total_duration: int = TARGET_DURATION) -> list[Path]:
    """
    Select clips from category folders following ASSEMBLY_ORDER.
    Tries to fill TARGET_DURATION seconds.
    """
    paths = get_keyword_paths(keyword)
    selected = []
    total = 0

    for category in ASSEMBLY_ORDER:
        clip_dir = paths["clips"][category]
        available = sorted(clip_dir.glob("*.mp4"))
        if not available:
            continue

        # Pick best clip (shortest that's above MIN_CLIP_DURATION)
        for clip_path in available:
            try:
                clip = VideoFileClip(str(clip_path))
                duration = clip.duration
                clip.close()
                if duration >= MIN_CLIP_DURATION and total + duration <= max_total_duration:
                    selected.append(clip_path)
                    total += duration
                    break
            except Exception:
                continue

        if total >= max_total_duration:
            break

    console.print(f"  Selected {len(selected)} clips — total: {total:.1f}s")
    return selected


def get_background_music() -> Path | None:
    """Pick a random royalty-free music file from music/ folder."""
    music_files = list(MUSIC_DIR.glob("*.mp3")) + list(MUSIC_DIR.glob("*.wav"))
    if not music_files:
        console.print("  [yellow]No music files in music/ folder — assembling without music[/yellow]")
        return None
    return random.choice(music_files)


def assemble_video(
    keyword: str,
    clip_paths: list[Path],
    voice_path: Path | None = None,
    script: dict | None = None,
) -> Path | None:
    """
    Assemble final video.
    Returns path to output file.
    """
    if not check_ram("video assembly"):
        return None

    if not clip_paths:
        console.print("  [red]No clips to assemble[/red]")
        return None

    paths = get_keyword_paths(keyword)
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = paths["output"] / date_str
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "final.mp4"

    console.print(f"  Assembling {len(clip_paths)} clips...")

    try:
        # Load and resize clips to 9:16 (1080x1920)
        clips = []
        for cp in clip_paths:
            clip = VideoFileClip(str(cp))
            # Resize to vertical format
            clip = clip.resize(height=1920)
            # Crop to 1080 width (center crop)
            if clip.w > 1080:
                x1 = (clip.w - 1080) // 2
                clip = clip.crop(x1=x1, width=1080)
            clips.append(clip)

        final_video = concatenate_videoclips(clips, method="compose")

        # Add voice if available
        audio_tracks = []
        if voice_path and voice_path.exists():
            voice = AudioFileClip(str(voice_path))
            voice = voice.volumex(1.0)
            audio_tracks.append(voice)

        # Add background music at low volume
        music_path = get_background_music()
        if music_path:
            music = AudioFileClip(str(music_path))
            music = music.subclip(0, min(music.duration, final_video.duration))
            music = music.volumex(0.15)  # 15% volume — background only
            audio_tracks.append(music)

        if audio_tracks:
            final_audio = CompositeAudioClip(audio_tracks)
            final_video = final_video.set_audio(final_audio)

        # Export
        console.print(f"  Exporting to: {output_path}")
        console.print(f"  [yellow]Export may take 1-3 minutes...[/yellow]")
        final_video.write_videofile(
            str(output_path),
            fps=30,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=str(paths["temp"] / "temp_audio.m4a"),
            remove_temp=True,
            verbose=False,
            logger=None,
        )

        # Cleanup
        for clip in clips:
            clip.close()
        final_video.close()

        console.print(f"  [green]Video assembled: {output_path}[/green]")
        return output_path

    except Exception as e:
        console.print(f"  [red]Assembly failed: {e}[/red]")
        return None
