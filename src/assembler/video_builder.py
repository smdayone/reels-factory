"""
Assemble final video from extracted clips + voice + music.
Uses MoviePy for composition, FFmpeg for final encoding.
"""
# Fix PIL.Image.ANTIALIAS removed in Pillow 10+ (breaks MoviePy 1.0.3)
import PIL.Image
if not hasattr(PIL.Image, "ANTIALIAS"):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS

import random as _random
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from moviepy.editor import (
    VideoFileClip, AudioFileClip, ImageClip,
    CompositeVideoClip, CompositeAudioClip, concatenate_videoclips
)
from config.settings import (
    TARGET_DURATION, MIN_CLIP_DURATION, MAX_CLIP_DURATION, MUSIC_VOLUME,
    TEXT_MIN_DURATION, get_keyword_paths, MUSIC_DIR, MUSIC_SOURCE,
    HOOK_TRANSITIONS_DIR, CREATORS_DIR, HD_FILTER_ENABLED,
)
from src.assembler.overlay_builder import (
    render_hook_rgba, render_benefit_rgba, render_cta_rgba, render_emotion_rgba,
    add_hook_overlay, add_benefit_overlay, add_cta_overlay,  # legacy kept
    FRAME_W, FRAME_H,
)
from src.utils.system_check import check_ram
from rich.console import Console

console = Console()

# Assembly order: categories used in final video, in sequence.
# "ai" (manually loaded AI-generated clips) has high priority after hook.
# "unclassified" is last — used as filler when primary categories are empty.
ASSEMBLY_ORDER = ["hook", "ai", "problem", "solution", "demo", "cta", "unboxing", "unclassified"]


def _get_random_video(
    directory: Path,
    variation: int = 0,
    history: "AssetHistory | None" = None,
    asset_type: str = "hook_clip",
) -> "Path | None":
    """
    Pick a random .mp4 from directory, avoiding recently used ones (history).
    Seeded shuffle ensures deterministic ordering; iterates until a fresh pick is found.
    Falls back to first candidate if all have been used recently.
    """
    files = sorted(directory.rglob("*.mp4"))
    if not files:
        console.print(f"  [yellow]No videos in {directory}[/yellow]")
        return None

    rng = _random.Random(variation * 17)
    shuffled = files[:]
    rng.shuffle(shuffled)

    for candidate in shuffled:
        if history is None or not history.is_recent(asset_type, candidate.name):
            if history:
                history.add(asset_type, candidate.name)
            return candidate

    # All recently used — return first anyway (can't block generation)
    return shuffled[0]


# Type alias for history — avoids circular import (imported lazily in callers)
AssetHistory = "object"  # real type: src.utils.asset_history.AssetHistory


def _encode_args() -> list[str]:
    """Return FFmpeg video encode flags for the current platform.
    macOS  → h264_videotoolbox (Apple Silicon hardware encoder, ~5× faster)
    others → libx264 CRF 18 slow (software, high quality)
    """
    if sys.platform == "darwin":
        return ["-c:v", "h264_videotoolbox", "-b:v", "5M"]
    return ["-c:v", "libx264", "-b:v", "5M", "-preset", "fast"]


def apply_hd_filter(output_path: Path) -> Path:
    """
    Post-generation HD sharpening pass via FFmpeg.
    Applies: unsharp mask + slight contrast/saturation boost.
    Replaces the original file in-place (final.mp4 → final_hd.mp4 → final.mp4).
    Uses hardware VideoToolbox encoder on macOS, libx264 elsewhere.
    Returns the (unchanged) output_path.
    """
    hd_path = output_path.parent / "final_hd.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(output_path),
        "-vf", "unsharp=5:5:1.0:5:5:0.0,eq=contrast=1.05:saturation=1.1",
        *_encode_args(),
        "-c:a", "copy",
        "-loglevel", "error",
        str(hd_path),
    ]
    console.print("  Applying HD filter (sharpening + contrast)...")
    result = subprocess.run(cmd, check=False)
    if result.returncode == 0 and hd_path.exists():
        try:
            output_path.unlink(missing_ok=True)
            hd_path.rename(output_path)
            console.print("  [green]HD filter applied[/green]")
        except Exception as e:
            console.print(f"  [yellow]HD filter rename failed: {e}[/yellow]")
    else:
        console.print("  [yellow]HD filter failed — using original video[/yellow]")
        hd_path.unlink(missing_ok=True)
    return output_path


def _load_and_resize(clip_path: Path, max_dur: "float | None" = None) -> "VideoFileClip":
    """Load a clip, optionally trim it, and resize to 1080x1920."""
    clip = VideoFileClip(str(clip_path))
    if max_dur and clip.duration > max_dur:
        clip = clip.subclip(0, max_dur)
    clip = clip.resize(height=1920)
    if clip.w > 1080:
        x1 = (clip.w - 1080) // 2
        clip = clip.crop(x1=x1, width=1080)
    return clip


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
    # "ai" is high-priority — always placed right after the opening clip (matches ASSEMBLY_ORDER).
    # If clips/ai/ is empty or missing, inventory.get("ai") returns [] and the slot is silently skipped.
    varied_order = [start_cat, "ai"] + middle + ["cta", "unclassified"]

    # Build a flat ordered pool from the varied order, each category rotated.
    # "ai" clips are capped at 2 per video so they never dominate the mix —
    # they serve as hook-support or CTA companion, not as the entire video.
    _AI_MAX_CLIPS = 2
    pool: list[Path] = []
    for category in varied_order:
        clips = inventory.get(category, [])
        if not clips:
            continue
        start = variation % len(clips)
        rotated = clips[start:] + clips[:start]
        if category == "ai":
            rotated = rotated[:_AI_MAX_CLIPS]
        pool.extend(rotated)

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


def get_background_music(
    variation: int = 0,
    keyword: str = "",
    format_name: str = "benefits",
    history: "AssetHistory | None" = None,
) -> "Path | None":
    """
    Pick a music file, context-aware by format and keyword.

    Mood matching: if D:\\Music for Shorts has subfolders named after moods
    (e.g. "energetic", "emotional", "dramatic"), the correct subfolder is
    preferred for each format.  Falls back to the full library when no
    mood-matching subfolder is found.

    Seed is derived from keyword + format_name + variation so that:
      - different keywords pick from different regions of the library
      - different formats naturally favour different tracks
      - different variation indices never repeat the same track in a batch
    """
    if MUSIC_SOURCE == "drive":
        from src.utils.drive_music import get_drive_music
        return get_drive_music(variation)

    if not MUSIC_DIR.exists():
        console.print(f"  [yellow]Music folder not found: {MUSIC_DIR}[/yellow]")
        return None

    # Mood keywords per format — matched against subdir names (case-insensitive)
    _FORMAT_MOODS: dict[str, list[str]] = {
        "benefits":        ["upbeat", "energetic", "happy", "positive", "fun", "bright"],
        "emotion":         ["emotional", "calm", "soft", "slow", "sad", "deep", "ambient"],
        "hook_transition": ["dynamic", "energetic", "hype", "trap", "powerful", "intense"],
        "plot_twist":      ["dramatic", "suspense", "cinematic", "intense", "dark", "mystery"],
    }
    moods = _FORMAT_MOODS.get(format_name, [])

    def _collect(directory: Path) -> list[Path]:
        return sorted(
            list(directory.rglob("*.mp3")) +
            list(directory.rglob("*.wav")) +
            list(directory.rglob("*.m4a"))
        )

    all_files = _collect(MUSIC_DIR)
    if not all_files:
        console.print(f"  [yellow]No music files found in {MUSIC_DIR}[/yellow]")
        return None

    # Try mood-matching subdirs first
    mood_files: list[Path] = []
    if moods and MUSIC_DIR.exists():
        for subdir in MUSIC_DIR.iterdir():
            if subdir.is_dir():
                name_lower = subdir.name.lower()
                if any(m in name_lower for m in moods):
                    mood_files.extend(_collect(subdir))

    candidates = mood_files if mood_files else all_files
    src_label = "mood-match" if mood_files else "full library"

    # Hash-based seed: keyword + format + variation → wide spread, no clustering
    seed = abs(hash(f"{keyword}_{format_name}_{variation}")) % (2 ** 31)
    rng = _random.Random(seed)
    shuffled = candidates[:]
    rng.shuffle(shuffled)

    # Iterate until we find a track not used recently (history-aware)
    for track in shuffled:
        if history is None or not history.is_recent("music", track.name):
            if history:
                history.add("music", track.name)
            console.print(f"  Music [{src_label}]: [cyan]{track.parent.name}/{track.name}[/cyan]")
            return track

    # All recently used — fall back to first shuffled track
    chosen = shuffled[0]
    console.print(f"  Music [{src_label}]: [cyan]{chosen.parent.name}/{chosen.name}[/cyan]")
    return chosen


def _write_video(final_video, output_path: Path, paths: dict, variation: int) -> None:
    """Write final video to disk then optionally apply HD sharpening filter.
    Uses h264_videotoolbox on macOS (Apple Silicon hardware encoder) for speed.
    Falls back to libx264 on Windows / Linux.
    """
    console.print(f"  Exporting → {output_path}")
    console.print("  [yellow]Export takes 1-3 min — please wait...[/yellow]")
    codec = "h264_videotoolbox" if sys.platform == "darwin" else "libx264"
    final_video.write_videofile(
        str(output_path),
        fps=30,
        codec=codec,
        audio_codec="aac",
        temp_audiofile=str(paths["temp"] / f"temp_audio_{variation}.m4a"),
        remove_temp=True,
        verbose=False,
        logger=None,
    )
    if HD_FILTER_ENABLED:
        apply_hd_filter(output_path)


def assemble_benefits(
    keyword: str,
    clip_paths: list[Path],
    voice_path: "Path | None" = None,
    variation: int = 0,
    target_duration: float = TARGET_DURATION,
    script: dict = {},
    output_dir: "Path | None" = None,
    history: "AssetHistory | None" = None,
    language: str = "en",
) -> "Path | None":
    """
    Assemble Benefits format: hook stroke text + benefit texts per clip + CTA stroke text.
    Hook y_frac is randomised per video (0.30–0.50, seed variation+13).
    output_dir: if provided, use it instead of creating a new dated dir.
    language: ISO 639-1 code — used for output subfolder and filename.
    """
    if not check_ram("video assembly"):
        return None

    if not clip_paths:
        console.print("  [red]No clips to assemble[/red]")
        return None

    paths = get_keyword_paths(keyword)
    if output_dir is None:
        date_str   = datetime.now().strftime("%Y%m%d_%H%M%S")
        date_label = datetime.now().strftime("%Y-%m-%d")
        output_dir = paths["output"] / language / f"{date_str}_{variation:02d}"
        output_path = output_dir / f"{date_label}_benefits_{language}.mp4"
    else:
        # Called internally (e.g. as product section inside hook_transition):
        # use a stable name so the caller can find the file reliably.
        output_path = output_dir / "final.mp4"
    output_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"  Assembling {len(clip_paths)} clips  [dim](Benefits)[/dim]")

    try:
        # ── Step A: load, trim, resize ────────────────────────────────────────
        clips = []
        clip_durations: list[float] = []
        for cp in clip_paths:
            clip = _load_and_resize(cp, max_dur=MAX_CLIP_DURATION)
            clip_durations.append(clip.duration)
            clips.append(clip)

        # ── Step B: concatenate + trim to target ──────────────────────────────
        final_video = concatenate_videoclips(clips, method="compose")
        if final_video.duration > target_duration:
            final_video = final_video.subclip(0, target_duration)

        # ── Step C: text overlays ─────────────────────────────────────────────
        if script:
            rng = _random.Random(variation + 7)     # benefit y positions
            hook_y = _random.Random(variation + 13).uniform(0.20, 0.50)  # hook position
            benefit_keys = ["problem", "solution", "proof"]
            n = len(clips)
            overlay_layers = [final_video]
            last_text_end = 0.0

            # Safe vertical range for benefit texts: 0.12 (top) → 0.68 (bottom)
            # Avoids status bar area and the CTA zone (0.72+).
            # Spread rule: each text must be ≥ 0.18 from the previous one.
            _Y_MIN, _Y_MAX, _Y_MIN_SPREAD = 0.12, 0.68, 0.18
            _prev_y: float | None = None

            def _next_benefit_y() -> float:
                nonlocal _prev_y
                for _ in range(20):  # max attempts
                    y = rng.uniform(_Y_MIN, _Y_MAX)
                    if _prev_y is None or abs(y - _prev_y) >= _Y_MIN_SPREAD:
                        _prev_y = y
                        return y
                # Fallback: mirror the previous position across the midpoint
                y = 0.40 if _prev_y is None else (_Y_MIN + _Y_MAX) / 2 + ((_Y_MIN + _Y_MAX) / 2 - _prev_y)
                y = max(_Y_MIN, min(_Y_MAX, y))
                _prev_y = y
                return y

            t = 0.0
            for idx, (clip, dur) in enumerate(zip(clips, clip_durations)):
                t_start = t
                t += dur

                if t_start >= final_video.duration:
                    break
                if t_start < last_text_end:
                    continue

                text_dur = max(TEXT_MIN_DURATION, dur)
                text_dur = min(text_dur, final_video.duration - t_start)
                if text_dur <= 0:
                    continue

                rgba = None
                if idx == 0:
                    rgba = render_hook_rgba(script.get("hook", ""), y_frac=hook_y)
                elif idx == n - 1:
                    rgba = render_cta_rgba(script.get("cta", ""))
                else:
                    y_frac = _next_benefit_y()
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

        # ── Step D: audio ─────────────────────────────────────────────────────
        audio_tracks = []
        if voice_path and voice_path.exists():
            audio_tracks.append(AudioFileClip(str(voice_path)).volumex(1.0))

        music_path = get_background_music(variation, keyword, "benefits", history)
        if music_path:
            music_clip = AudioFileClip(str(music_path))
            music = music_clip.subclip(0, min(music_clip.duration, final_video.duration)).volumex(MUSIC_VOLUME)
            audio_tracks.append(music)

        if audio_tracks:
            final_video = final_video.set_audio(CompositeAudioClip(audio_tracks))

        # ── Step E: export ────────────────────────────────────────────────────
        _write_video(final_video, output_path, paths, variation)

        for clip in clips:
            clip.close()
        final_video.close()

        console.print(f"  [green]Done → {output_path}[/green]")
        return output_path

    except Exception as e:
        console.print(f"  [red]Assembly failed: {e}[/red]")
        return None


def assemble_video(
    keyword: str,
    clip_paths: list[Path],
    voice_path: "Path | None" = None,
    variation: int = 0,
    target_duration: float = TARGET_DURATION,
    script: dict = {},
) -> "Path | None":
    """Backwards-compatible alias for assemble_benefits()."""
    return assemble_benefits(
        keyword, clip_paths, voice_path, variation, target_duration, script
    )


def assemble_emotion(
    keyword: str,
    clip_paths: list[Path],
    voice_path: "Path | None" = None,
    variation: int = 0,
    target_duration: float = TARGET_DURATION,
    script: dict = {},
    output_dir: "Path | None" = None,
    history: "AssetHistory | None" = None,
    language: str = "en",
) -> "Path | None":
    """
    Emotion format: single centered emotional text overlay for the full video.
    language: ISO 639-1 code — used for output subfolder and filename.
    """
    if not check_ram("video assembly"):
        return None
    if not clip_paths:
        console.print("  [red]No clips to assemble[/red]")
        return None

    paths = get_keyword_paths(keyword)
    if output_dir is None:
        date_str   = datetime.now().strftime("%Y%m%d_%H%M%S")
        date_label = datetime.now().strftime("%Y-%m-%d")
        output_dir = paths["output"] / language / f"{date_str}_{variation:02d}"
        output_path = output_dir / f"{date_label}_emotion_{language}.mp4"
    else:
        # Called internally (e.g. as product section inside hook_transition):
        # use a stable name so the caller can find the file reliably.
        output_path = output_dir / "final.mp4"
    output_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"  Assembling {len(clip_paths)} clips  [dim](Emotion)[/dim]")

    try:
        # ── Step A+B: load, resize, concatenate, trim ─────────────────────────
        clips = [_load_and_resize(cp, max_dur=MAX_CLIP_DURATION) for cp in clip_paths]
        final_video = concatenate_videoclips(clips, method="compose")
        if final_video.duration > target_duration:
            final_video = final_video.subclip(0, target_duration)

        # ── Step C: single full-duration emotion overlay ───────────────────────
        rgba = render_emotion_rgba(script.get("emotion", ""))
        if rgba is not None:
            layer = ImageClip(rgba, ismask=False).set_duration(final_video.duration)
            final_video = CompositeVideoClip([final_video, layer], size=(FRAME_W, FRAME_H))

        # ── Step D: audio ─────────────────────────────────────────────────────
        audio_tracks = []
        if voice_path and voice_path.exists():
            audio_tracks.append(AudioFileClip(str(voice_path)).volumex(1.0))

        music_path = get_background_music(variation, keyword, "emotion", history)
        if music_path:
            music_clip = AudioFileClip(str(music_path))
            music = music_clip.subclip(0, min(music_clip.duration, final_video.duration)).volumex(MUSIC_VOLUME)
            audio_tracks.append(music)

        if audio_tracks:
            final_video = final_video.set_audio(CompositeAudioClip(audio_tracks))

        # ── Step E: export ────────────────────────────────────────────────────
        _write_video(final_video, output_path, paths, variation)

        for clip in clips:
            clip.close()
        final_video.close()

        console.print(f"  [green]Done → {output_path}[/green]")
        return output_path

    except Exception as e:
        console.print(f"  [red]Assembly failed: {e}[/red]")
        return None


def assemble_hook_transition(
    keyword: str,
    clip_paths: list[Path],
    base_format: str = "benefits",
    voice_path: "Path | None" = None,
    variation: int = 0,
    target_duration: float = TARGET_DURATION,
    script: dict = {},
    history: "AssetHistory | None" = None,
    language: str = "en",
) -> "Path | None":
    """
    Hook Transition format: random clip from D:\\Hook Transitions (original audio)
    concatenated with a Benefits or Emotion product video.
    language: ISO 639-1 code — used for output subfolder and filename.
    """
    if not check_ram("video assembly"):
        return None

    hook_clip_path = _get_random_video(HOOK_TRANSITIONS_DIR, variation, history, "hook_clip")
    if hook_clip_path is None:
        console.print("  [yellow]Hook Transitions folder empty — falling back to Benefits[/yellow]")
        return assemble_benefits(keyword, clip_paths, voice_path, variation, target_duration, script, history=history, language=language)

    paths = get_keyword_paths(keyword)
    date_str   = datetime.now().strftime("%Y%m%d_%H%M%S")
    date_label = datetime.now().strftime("%Y-%m-%d")
    output_dir = paths["output"] / language / f"{date_str}_{variation:02d}"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path  = output_dir / f"{date_label}_hook_transition_{language}.mp4"
    temp_product = paths["temp"] / f"product_section_{variation}.mp4"

    console.print(
        f"  Assembling  [dim](Hook Transition → {base_format})[/dim]\n"
        f"  Hook clip: [cyan]{hook_clip_path.name}[/cyan]"
    )

    try:
        # ── Step 1: assemble product section to a temp file ───────────────────
        assembler = assemble_emotion if base_format == "emotion" else assemble_benefits
        assembler(
            keyword, clip_paths, voice_path, variation, target_duration, script,
            output_dir=paths["temp"] / f"product_tmp_{variation}",
            history=history,
        )
        product_tmp_dir = paths["temp"] / f"product_tmp_{variation}"
        product_tmp_file = product_tmp_dir / "final.mp4"

        if not product_tmp_file.exists():
            console.print("  [red]Product section assembly failed[/red]")
            return None

        # ── Step 2: load hook clip (keep original audio) + product clip ───────
        hook_clip = _load_and_resize(hook_clip_path)
        hook_dur = hook_clip.duration
        product_clip = VideoFileClip(str(product_tmp_file))

        # Strip audio from product clip — we'll replace it below
        product_video_only = product_clip.without_audio()

        # ── Step 3: concatenate ───────────────────────────────────────────────
        final_video = concatenate_videoclips([hook_clip.without_audio(), product_video_only], method="compose")

        # ── Step 4: composite audio — hook audio + music starting after hook ──
        audio_tracks = []
        if hook_clip.audio:
            hook_audio = hook_clip.audio.subclip(0, hook_dur)
            audio_tracks.append(hook_audio)

        music_path = get_background_music(variation, keyword, "hook_transition", history)
        if music_path:
            product_dur = product_clip.duration
            music_clip = AudioFileClip(str(music_path))
            music = (
                music_clip
                .subclip(0, min(music_clip.duration, product_dur))
                .volumex(MUSIC_VOLUME)
                .set_start(hook_dur)
            )
            audio_tracks.append(music)

        if audio_tracks:
            final_video = final_video.set_audio(CompositeAudioClip(audio_tracks))

        # ── Step 5: export ────────────────────────────────────────────────────
        _write_video(final_video, output_path, paths, variation)

        hook_clip.close()
        product_clip.close()
        final_video.close()

        # Clean up temp product file
        try:
            product_tmp_file.unlink(missing_ok=True)
            product_tmp_dir.rmdir()
        except Exception:
            pass

        console.print(f"  [green]Done → {output_path}[/green]")
        return output_path

    except Exception as e:
        console.print(f"  [red]Hook Transition assembly failed: {e}[/red]")
        return None


def assemble_plot_twist(
    keyword: str,
    clip_paths: list[Path],
    voice_path: "Path | None" = None,
    variation: int = 0,
    target_duration: float = TARGET_DURATION,
    script: dict = {},
    history: "AssetHistory | None" = None,
    language: str = "en",
) -> "Path | None":
    """
    Plot Twist format: 3×1s cuts of a creator clip + product clips.
    PLOT_HOOK text during creator section, PLOT_REVEAL text during product section.
    Audio: background music only (no original audio from any clip).
    language: ISO 639-1 code — used for output subfolder and filename.
    """
    if not check_ram("video assembly"):
        return None

    creator_path = _get_random_video(CREATORS_DIR, variation, history, "creator_clip")
    if creator_path is None:
        console.print("  [red]Creators folder empty — cannot assemble Plot Twist[/red]")
        return None

    if not clip_paths:
        console.print("  [red]No product clips to assemble[/red]")
        return None

    paths = get_keyword_paths(keyword)
    date_str   = datetime.now().strftime("%Y%m%d_%H%M%S")
    date_label = datetime.now().strftime("%Y-%m-%d")
    output_dir = paths["output"] / language / f"{date_str}_{variation:02d}"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{date_label}_plot_twist_{language}.mp4"

    console.print(
        f"  Assembling  [dim](Plot Twist)[/dim]\n"
        f"  Creator clip: [cyan]{creator_path.name}[/cyan]"
    )

    try:
        # ── Step 1: creator section — 3 × 1s subclips from same file ─────────
        creator_raw = VideoFileClip(str(creator_path))
        creator_clips = []
        for i in range(3):
            if i >= creator_raw.duration:
                break
            end = min(i + 1, creator_raw.duration)
            sub = creator_raw.subclip(i, end)
            sub = sub.resize(height=1920)
            if sub.w > 1080:
                x1 = (sub.w - 1080) // 2
                sub = sub.crop(x1=x1, width=1080)
            creator_clips.append(sub)

        if not creator_clips:
            console.print("  [red]Creator clip too short[/red]")
            creator_raw.close()
            return None

        creator_section = concatenate_videoclips(creator_clips, method="compose")
        creator_dur = creator_section.duration

        # ── Step 2: product section ───────────────────────────────────────────
        product_clips = [_load_and_resize(cp, max_dur=MAX_CLIP_DURATION) for cp in clip_paths]
        product_section = concatenate_videoclips(product_clips, method="compose")

        # ── Step 3: concatenate + trim ────────────────────────────────────────
        final_video = concatenate_videoclips([creator_section, product_section], method="compose")
        if final_video.duration > target_duration:
            final_video = final_video.subclip(0, target_duration)

        # ── Step 4: text overlays ─────────────────────────────────────────────
        overlay_layers = [final_video]

        plot_hook_rgba = render_emotion_rgba(script.get("plot_hook", ""))
        if plot_hook_rgba is not None:
            hook_dur = min(creator_dur, final_video.duration)
            layer = (
                ImageClip(plot_hook_rgba, ismask=False)
                .set_start(0)
                .set_duration(hook_dur)
            )
            overlay_layers.append(layer)

        plot_reveal_rgba = render_emotion_rgba(script.get("plot_reveal", ""))
        if plot_reveal_rgba is not None and creator_dur < final_video.duration:
            reveal_dur = final_video.duration - creator_dur
            layer = (
                ImageClip(plot_reveal_rgba, ismask=False)
                .set_start(creator_dur)
                .set_duration(reveal_dur)
            )
            overlay_layers.append(layer)

        if len(overlay_layers) > 1:
            final_video = CompositeVideoClip(overlay_layers, size=(FRAME_W, FRAME_H))

        # ── Step 5: background music only (no original audio) ─────────────────
        music_path = get_background_music(variation, keyword, "plot_twist", history)
        if music_path:
            music_clip = AudioFileClip(str(music_path))
            music = music_clip.subclip(0, min(music_clip.duration, final_video.duration)).volumex(MUSIC_VOLUME)
            final_video = final_video.set_audio(music)

        # ── Step 6: export ────────────────────────────────────────────────────
        _write_video(final_video, output_path, paths, variation)

        creator_raw.close()
        for clip in product_clips:
            clip.close()
        final_video.close()

        console.print(f"  [green]Done → {output_path}[/green]")
        return output_path

    except Exception as e:
        console.print(f"  [red]Plot Twist assembly failed: {e}[/red]")
        return None
