"""
Assemble final video from extracted clips + voice + music.
Uses MoviePy for composition, FFmpeg for final encoding.
"""
# Fix PIL.Image.ANTIALIAS removed in Pillow 10+ (breaks MoviePy 1.0.3)
import PIL.Image
if not hasattr(PIL.Image, "ANTIALIAS"):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS

import random as _random
from pathlib import Path
from datetime import datetime
from moviepy.editor import (
    VideoFileClip, AudioFileClip, ImageClip,
    CompositeVideoClip, CompositeAudioClip, concatenate_videoclips
)
from config.settings import (
    TARGET_DURATION, MIN_CLIP_DURATION, MAX_CLIP_DURATION, MUSIC_VOLUME,
    TEXT_MIN_DURATION, get_keyword_paths, MUSIC_DIR, MUSIC_SOURCE
)
from src.assembler.overlay_builder import (
    render_hook_rgba, render_benefit_rgba, render_cta_rgba,
    add_hook_overlay, add_benefit_overlay, add_cta_overlay,  # legacy kept
    FRAME_W, FRAME_H,
)
from src.utils.system_check import check_ram
from rich.console import Console

console = Console()

# Assembly order: categories used in final video, in sequence.
# "unclassified" is last — used as filler when primary categories are empty.
ASSEMBLY_ORDER = ["hook", "problem", "solution", "demo", "cta", "unboxing", "unclassified"]


def get_clips_inventory(keyword: str) -> dict[str, list[Path]]:
    """Return all available clips per category for this keyword."""
    paths = get_keyword_paths(keyword)
    return {
        cat: sorted(clip_dir.glob("*.mp4"))
        for cat, clip_dir in paths["clips"].items()
    }


def select_clips(
    keyword: str,
    variation: int = 0,
    target_duration: float = TARGET_DURATION,
) -> list[Path]:
    """
    Build a clip sequence to fill target_duration seconds.

    Each clip contributes at most MAX_CLIP_DURATION seconds to the count,
    so a 36s clip counts as 6s — forcing the system to pick many more clips
    and produce fast, dynamic cuts typical of short-form video.

    Clips are drawn from all categories in ASSEMBLY_ORDER.  Within each
    category the list is rotated by `variation` so every generated video
    starts from a different clip.  The pool is iterated until the budget
    (target_duration) is filled.
    """
    inventory = get_clips_inventory(keyword)

    # Build a seeded-random assembly order so each video starts differently
    rng = _random.Random(variation)
    start_cat = rng.choice(["hook", "problem", "solution"])
    middle = [c for c in ["hook", "problem", "solution", "demo", "unboxing"] if c != start_cat]
    rng.shuffle(middle)
    varied_order = [start_cat] + middle + ["cta", "unclassified"]

    # Build a flat ordered pool from the varied order, each category rotated
    pool: list[Path] = []
    for category in varied_order:
        clips = inventory.get(category, [])
        if not clips:
            continue
        start = variation % len(clips)
        pool.extend(clips[start:] + clips[:start])

    selected: list[Path] = []
    total = 0.0
    seen: set[str] = set()

    for clip_path in pool:
        if total >= target_duration:
            break
        key = str(clip_path)
        if key in seen:
            continue
        seen.add(key)
        try:
            clip = VideoFileClip(str(clip_path))
            raw_dur = clip.duration
            clip.close()
            if raw_dur < MIN_CLIP_DURATION:
                continue
            # Count only the trimmed portion toward the budget
            effective = min(raw_dur, MAX_CLIP_DURATION)
            selected.append(clip_path)
            total += effective
        except Exception:
            continue

    console.print(
        f"  Selected [bold]{len(selected)}[/bold] clips — "
        f"~{total:.1f}s effective  |  target: {target_duration:.0f}s  |  "
        f"max {MAX_CLIP_DURATION:.0f}s/clip"
    )
    return selected


def get_background_music(variation: int = 0) -> Path | None:
    """
    Pick a music file, cycling through available tracks by variation index.
    Source is determined by MUSIC_SOURCE in .env:
      "drive" — fetches from Google Drive folder (with local cache)
      "local" — reads from MUSIC_DIR recursively (default)
    """
    if MUSIC_SOURCE == "drive":
        from src.utils.drive_music import get_drive_music
        return get_drive_music(variation)

    # Local: scan MUSIC_DIR recursively so subfolders (genre/mood) are included
    if not MUSIC_DIR.exists():
        console.print(f"  [yellow]Music folder not found: {MUSIC_DIR}[/yellow]")
        return None

    music_files = sorted(
        list(MUSIC_DIR.rglob("*.mp3")) +
        list(MUSIC_DIR.rglob("*.wav")) +
        list(MUSIC_DIR.rglob("*.m4a"))
    )
    if not music_files:
        console.print(f"  [yellow]No music files found in {MUSIC_DIR}[/yellow]")
        return None

    # Seed * 31 gives a different random sequence from the assembly-order RNG
    chosen = _random.Random(variation * 31).choice(music_files)
    console.print(f"  Music: [cyan]{chosen.parent.name}/{chosen.name}[/cyan]")
    return chosen


def assemble_video(
    keyword: str,
    clip_paths: list[Path],
    voice_path: Path | None = None,
    variation: int = 0,
    target_duration: float = TARGET_DURATION,
    script: dict = {},
) -> Path | None:
    """
    Assemble final vertical video (1080x1920, 30fps).
    Returns path to output file, or None on failure.

    variation:        picks music track and overlay-position seed.
    target_duration:  hard cap — video is trimmed to this length if clips overshoot.
    script:           if provided, adds text overlays with minimum TEXT_MIN_DURATION
                      screen time. Benefit positions are varied per clip (safe mobile
                      range 25%-55%) so text never overlaps captions/descriptions.
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
        # ── Step A: load, trim, resize (no overlays yet) ──────────────────────
        clips = []
        clip_durations: list[float] = []

        for cp in clip_paths:
            clip = VideoFileClip(str(cp))
            if clip.duration > MAX_CLIP_DURATION:
                clip = clip.subclip(0, MAX_CLIP_DURATION)
            clip = clip.resize(height=1920)
            if clip.w > 1080:
                x1 = (clip.w - 1080) // 2
                clip = clip.crop(x1=x1, width=1080)
            clip_durations.append(clip.duration)
            clips.append(clip)

        # ── Step B: concatenate + trim to target ──────────────────────────────
        final_video = concatenate_videoclips(clips, method="compose")
        if final_video.duration > target_duration:
            final_video = final_video.subclip(0, target_duration)

        # ── Step C: add text overlays as timed ImageClip layers ───────────────
        if script:
            # Seeded RNG for benefit Y positions (different seed from assembly + music)
            rng = _random.Random(variation + 7)
            benefit_keys = ["problem", "solution", "proof"]
            n = len(clips)
            overlay_layers = [final_video]

            # Sequential placement: a new text only starts after the previous one ends.
            # This prevents any overlap when short clips (< TEXT_MIN_DURATION) are used.
            last_text_end = 0.0

            # Compute cumulative start time for each clip
            t = 0.0
            for idx, (clip, dur) in enumerate(zip(clips, clip_durations)):
                t_start = t
                t += dur

                # Respect the hard cap: skip clips that start after trim point
                if t_start >= final_video.duration:
                    break

                # Skip this clip's text if the previous text is still on screen
                if t_start < last_text_end:
                    continue

                # Text duration: at least TEXT_MIN_DURATION, capped by remaining video
                text_dur = max(TEXT_MIN_DURATION, dur)
                text_dur = min(text_dur, final_video.duration - t_start)
                if text_dur <= 0:
                    continue

                # Pick the right RGBA canvas
                rgba = None
                if idx == 0:
                    rgba = render_hook_rgba(script.get("hook", ""))
                elif idx == n - 1:
                    rgba = render_cta_rgba(script.get("cta", ""))
                else:
                    # Vary vertical position per benefit (safe mobile range: 25%-55%)
                    y_frac = rng.uniform(0.25, 0.55)
                    key = benefit_keys[(idx - 1) % len(benefit_keys)]
                    rgba = render_benefit_rgba(script.get(key, ""), y_frac)

                if rgba is not None:
                    layer = (
                        ImageClip(rgba, ismask=False)
                        .set_start(t_start)
                        .set_duration(text_dur)
                    )
                    overlay_layers.append(layer)
                    last_text_end = t_start + text_dur

            if len(overlay_layers) > 1:
                final_video = CompositeVideoClip(overlay_layers, size=(FRAME_W, FRAME_H))

        # ── Step D: audio — voice track + background music ────────────────────
        audio_tracks = []
        if voice_path and voice_path.exists():
            voice = AudioFileClip(str(voice_path))
            voice = voice.volumex(1.0)
            audio_tracks.append(voice)

        music_path = get_background_music(variation)
        if music_path:
            music = AudioFileClip(str(music_path))
            music = music.subclip(0, min(music.duration, final_video.duration))
            music = music.volumex(MUSIC_VOLUME)
            audio_tracks.append(music)

        if audio_tracks:
            final_audio = CompositeAudioClip(audio_tracks)
            final_video = final_video.set_audio(final_audio)

        # ── Step E: export ─────────────────────────────────────────────────────
        console.print(f"  Exporting → {output_path}")
        console.print("  [yellow]Export takes 1-3 min — please wait...[/yellow]")
        final_video.write_videofile(
            str(output_path),
            fps=30,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=str(paths["temp"] / f"temp_audio_{variation}.m4a"),
            remove_temp=True,
            verbose=False,
            logger=None,
        )

        for clip in clips:
            clip.close()
        final_video.close()

        console.print(f"  [green]Done → {output_path}[/green]")
        return output_path

    except Exception as e:
        console.print(f"  [red]Assembly failed: {e}[/red]")
        return None
