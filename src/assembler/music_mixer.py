"""
Background music mixer.
Mixes royalty-free music from music/ folder at low volume.
"""
import random
from pathlib import Path
from config.settings import MUSIC_DIR
from rich.console import Console

console = Console()

MUSIC_VOLUME = 0.15  # 15% — background only, not to overpower voice


def get_music_track(duration: float):
    """
    Load a random music track from music/ folder, trimmed to duration.
    Returns AudioFileClip or None.
    """
    from moviepy.editor import AudioFileClip

    music_files = (
        list(MUSIC_DIR.rglob("*.mp3")) +
        list(MUSIC_DIR.rglob("*.wav")) +
        list(MUSIC_DIR.rglob("*.m4a"))
    )
    if not music_files:
        console.print("  [yellow]No music in music/ folder — skipping background music[/yellow]")
        console.print("  Tip: Add royalty-free MP3s from pixabay.com/music")
        return None

    track_path = random.choice(music_files)
    console.print(f"  Music track: {track_path.name}")

    music = AudioFileClip(str(track_path))
    if music.duration < duration:
        # Loop if track is shorter than video
        from moviepy.audio.fx.audio_loop import audio_loop
        music = audio_loop(music, duration=duration)
    else:
        music = music.subclip(0, duration)

    return music.volumex(MUSIC_VOLUME)
