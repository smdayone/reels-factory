"""
reels-factory -- Main entry point (interactive)

Two modes:
  extract   Analyze raw videos, cut clips, classify, subtitle-check
  generate  Use existing clips to produce N final videos (script + assembly)

Usage:
  python main.py                    # fully interactive
  python main.py --mode extract
  python main.py --mode generate
  python main.py --mode extract  --skip-voice
  python main.py --mode generate --skip-script --count 5

Output: D:/Products Reels/[keyword]/output/[YYYYMMDD_HHMMSS]/final.mp4
                                                             post_metadata.json
"""
import argparse
import json
import random
import subprocess
from pathlib import Path
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, IntPrompt

from config.settings import (
    create_keyword_dirs, get_keyword_paths,
    ANTHROPIC_API_KEY, SSD_BASE,
)
from src.utils.system_check import system_report, check_disk_space
from src.analyzer.scene_detector import detect_scenes, has_audio_speech, get_video_duration
from src.analyzer.transcriber import transcribe, save_transcript
from src.analyzer.subtitle_detector import has_hardcoded_text
from src.extractor.voice_separator import separate_voice
from src.extractor.clip_classifier import classify_by_transcript, classify_by_vision
from src.script.persona_builder import identify_persona
from src.script.script_generator import generate_script
from src.assembler.video_builder import select_clips, assemble_video, get_clips_inventory

console = Console()


# ---------------------------------------------------------------------------
# Interactive helpers
# ---------------------------------------------------------------------------

def pick_keyword() -> str:
    """Scan SSD_BASE for folders and let the user choose one."""
    if not SSD_BASE.exists():
        console.print(f"[red]SSD not found: {SSD_BASE}[/red]")
        console.print("Check that the drive is connected and SSD_DRIVE is correct in .env")
        raise SystemExit(1)

    folders = sorted([
        d for d in SSD_BASE.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    ])

    if folders:
        table = Table(
            title=f"Folders in {SSD_BASE}",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("#", style="dim", width=4)
        table.add_column("Keyword", style="bold")
        table.add_column("Raw videos", justify="right")
        table.add_column("Clip categories with content", justify="right")
        table.add_column("Output runs", justify="right")

        for i, folder in enumerate(folders, start=1):
            raw_count = len(list((folder / "raw").glob("*.mp4"))) if (folder / "raw").exists() else 0
            clips_dir = folder / "clips"
            filled_cats = 0
            if clips_dir.exists():
                filled_cats = sum(
                    1 for d in clips_dir.iterdir()
                    if d.is_dir() and any(d.glob("*.mp4"))
                )
            out_count = 0
            if (folder / "output").exists():
                out_count = sum(1 for d in (folder / "output").iterdir() if d.is_dir())
            table.add_row(str(i), folder.name, str(raw_count), str(filled_cats), str(out_count))

        console.print()
        console.print(table)
        console.print("  [dim]0 — type a new keyword[/dim]\n")

        choice = Prompt.ask("Choose a number", default="1")

        if choice.strip() == "0":
            keyword = Prompt.ask("New keyword (creates folder on SSD)").strip()
        else:
            try:
                keyword = folders[int(choice) - 1].name
            except (ValueError, IndexError):
                console.print("[red]Invalid choice.[/red]")
                raise SystemExit(1)
    else:
        console.print(f"[yellow]No folders found in {SSD_BASE}[/yellow]")
        keyword = Prompt.ask("Enter keyword to create").strip()

    if not keyword:
        console.print("[red]No keyword entered. Exiting.[/red]")
        raise SystemExit(1)

    return keyword


def load_existing_transcripts(keyword: str) -> list[str]:
    """Load all saved transcript .txt files for this keyword."""
    paths = get_keyword_paths(keyword)
    texts = []
    if paths["transcripts"].exists():
        for txt in paths["transcripts"].glob("*.txt"):
            text = txt.read_text(encoding="utf-8", errors="ignore").strip()
            if text:
                texts.append(text)
    return texts


def show_clips_summary(keyword: str) -> int:
    """Print a table of available clips per category. Returns total clip count."""
    inventory = get_clips_inventory(keyword)
    table = Table(
        title=f"Available clips — {keyword}",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Category", style="bold")
    table.add_column("Clips", justify="right")
    table.add_column("Total duration", justify="right")

    total_clips = 0
    for category in ["hook", "problem", "solution", "demo", "unboxing", "cta", "unclassified"]:
        clips = inventory.get(category, [])
        dur = 0.0
        for c in clips:
            d = get_video_duration(c)
            if d:
                dur += d
        if clips:
            table.add_row(category, str(len(clips)), f"{dur:.1f}s")
        total_clips += len(clips)

    console.print()
    console.print(table)
    console.print(f"  Total clips: [bold]{total_clips}[/bold]\n")
    return total_clips


# ---------------------------------------------------------------------------
# Stage 1 — Extract
# ---------------------------------------------------------------------------

def _extract_frame(video_path: Path, timestamp: float, output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y",
        "-ss", str(timestamp),
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "2",
        "-loglevel", "error",
        str(output_path),
    ], check=False)


def _cut_clip(
    video_path: Path,
    clip_out: Path,
    start: float,
    clip_duration: float,
    no_vocals_path: Path | None,
) -> None:
    """
    Cut a clip from video_path [start, start+clip_duration].
    If no_vocals_path is provided, replaces the audio track with that stem
    (voice removed, ambient sounds / music kept).
    If not available, keeps original audio as-is.
    """
    if no_vocals_path and no_vocals_path.exists():
        # Seek both video and no_vocals to the same start position
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start), "-t", str(clip_duration), "-i", str(video_path),
            "-ss", str(start), "-t", str(clip_duration), "-i", str(no_vocals_path),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "libx264", "-c:a", "aac",
            "-loglevel", "error",
            str(clip_out),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", str(video_path),
            "-t", str(clip_duration),
            "-c:v", "libx264", "-c:a", "aac",
            "-loglevel", "error",
            str(clip_out),
        ]
    subprocess.run(cmd, check=False)


def process_video(
    video_path: Path,
    keyword: str,
    skip_voice: bool,
    skip_subtitle_check: bool,
    all_transcripts: list,
) -> None:
    """Run the full extract pipeline on a single raw video."""
    console.print(f"\n[bold blue]Processing:[/bold blue] {video_path.name}")
    paths = get_keyword_paths(keyword)

    # 1. Speech detection
    is_spoken = has_audio_speech(video_path)
    console.print(f"  Speech: {'yes' if is_spoken else 'no'}")

    # 2. Scene cuts
    scenes = detect_scenes(video_path)
    console.print(f"  Scene cuts: {len(scenes) - 1}")

    # 3. Transcription
    transcript = None
    if is_spoken:
        transcript = transcribe(video_path)
        save_transcript(transcript, paths["transcripts"] / video_path.stem)
        all_transcripts.append(transcript.get("text", ""))

    # 4. Voice separation — must run before clip loop so no_vocals track is ready
    no_vocals_path: Path | None = None
    if not skip_voice and is_spoken:
        stems = separate_voice(video_path, paths["voice"])
        if stems:
            no_vocals_path = stems["no_vocals"]
            console.print("  Voice removed from clips — ambient sounds kept")
    elif skip_voice:
        console.print("  [dim]Voice separation skipped — original audio kept in clips[/dim]")

    # 5. Cut + classify + subtitle-check each scene segment
    duration = get_video_duration(video_path)
    if duration is None:
        console.print("  [red]Could not read video duration — skipping[/red]")
        return

    kept = 0
    discarded_short = 0
    discarded_text = 0

    for i in range(len(scenes) - 1):
        start = scenes[i]
        end = scenes[i + 1]
        clip_duration = end - start

        if clip_duration < 1.5:
            discarded_short += 1
            continue

        # Classify
        if transcript and transcript.get("segments"):
            seg_text = " ".join(
                s["text"] for s in transcript["segments"]
                if s["start"] >= start and s["end"] <= end + 1
            )
            category = classify_by_transcript(seg_text) if seg_text else "unclassified"
        else:
            frame_path = paths["temp"] / f"{video_path.stem}_frame_{i:03d}.jpg"
            _extract_frame(video_path, start + clip_duration / 2, frame_path)
            category = classify_by_vision(frame_path, keyword)

        # Cut clip — audio replaced with no_vocals stem if Demucs ran
        clip_out = paths["clips"][category] / f"{video_path.stem}_clip_{i:03d}.mp4"
        _cut_clip(video_path, clip_out, start, clip_duration, no_vocals_path)

        # Subtitle / hardcoded text check
        if not skip_subtitle_check and clip_out.exists():
            if has_hardcoded_text(clip_out):
                clip_out.unlink(missing_ok=True)
                discarded_text += 1
                console.print(
                    f"  [yellow]Discarded (hardcoded text):[/yellow] "
                    f"{clip_out.name} [{category}]"
                )
                continue

        kept += 1

    console.print(
        f"  [green]Done:[/green] {kept} clips kept  |  "
        f"{discarded_short} too short  |  {discarded_text} discarded (text overlay)"
    )


def mode_extract(keyword: str, args) -> list[str]:
    """Extract clips from all raw videos. Returns list of transcript texts."""
    system_report()
    create_keyword_dirs(keyword)
    paths = get_keyword_paths(keyword)

    if not check_disk_space(paths["base"]):
        console.print("[red]Insufficient disk space. Aborting.[/red]")
        raise SystemExit(1)

    raw_videos = sorted(paths["raw"].glob("*.mp4"))
    if not raw_videos:
        console.print(f"[red]No .mp4 files found in:[/red] {paths['raw']}")
        console.print("Copy raw competitor videos into that folder, then run again.")
        raise SystemExit(1)

    console.print(f"\n[bold]Found {len(raw_videos)} video(s) to process[/bold]")

    skip_subtitle = args.skip_subtitle_check
    if not ANTHROPIC_API_KEY and not skip_subtitle:
        console.print("[yellow]No ANTHROPIC_API_KEY — subtitle check disabled[/yellow]")
        skip_subtitle = True

    all_transcripts: list[str] = []
    for video_path in raw_videos:
        process_video(
            video_path,
            keyword,
            skip_voice=args.skip_voice,
            skip_subtitle_check=skip_subtitle,
            all_transcripts=all_transcripts,
        )

    console.print()
    show_clips_summary(keyword)
    console.print("[green]Extract complete.[/green] Run again with [bold]--mode generate[/bold] to create videos.")
    return all_transcripts


# ---------------------------------------------------------------------------
# Stage 2 — Generate
# ---------------------------------------------------------------------------

def mode_generate(keyword: str, args) -> None:
    """Generate N final videos from existing clips."""
    total_clips = show_clips_summary(keyword)

    if total_clips == 0:
        console.print("[red]No clips found.[/red] Run [bold]--mode extract[/bold] first.")
        raise SystemExit(1)

    # How many videos?
    if args.count:
        n_videos = args.count
    else:
        n_videos = IntPrompt.ask("How many videos to generate?", default=3)

    n_videos = max(1, min(n_videos, 20))  # clamp 1-20

    console.print(f"\n[bold]Generating {n_videos} video(s) for [cyan]{keyword}[/cyan]...[/bold]\n")

    transcripts = load_existing_transcripts(keyword)
    generated: list[Path] = []

    for i in range(n_videos):
        console.print(Panel.fit(
            f"[bold cyan]Video {i + 1} / {n_videos}[/bold cyan]",
            border_style="cyan",
        ))

        # New script for each video
        script: dict = {}
        if not args.skip_script and ANTHROPIC_API_KEY:
            persona = identify_persona(keyword, transcripts)
            console.print(f"  Persona: [cyan]{persona['name']}[/cyan]")
            script = generate_script(
                keyword, "product",
                persona, persona.get("main_pain", ""),
            )
            if script:
                console.print(f"  [green]Script generated[/green]")
                console.print(f"  [bold]Hook:[/bold] {script.get('hook', '')}")

        # Random target duration between 15 and 45 seconds
        target_dur = random.randint(15, 45)
        console.print(f"  Target duration: [yellow]{target_dur}s[/yellow]")

        # Different clips each iteration
        clip_paths = select_clips(keyword, variation=i, target_duration=target_dur)
        if not clip_paths:
            console.print("  [red]Not enough clips for this variation — skipping[/red]")
            continue

        # Voice-over: pick a vocals.wav extracted by Demucs during extract phase
        paths = get_keyword_paths(keyword)
        vocals_files = sorted(paths["voice"].rglob("vocals.wav"))
        voice_path: Path | None = None
        if vocals_files:
            voice_path = vocals_files[i % len(vocals_files)]
            console.print(f"  Voice-over: [cyan]{voice_path.parent.name}[/cyan]")
        else:
            console.print("  [dim]No vocals found — run extract without --skip-voice to enable voice-over[/dim]")

        output_path = assemble_video(
            keyword, clip_paths,
            voice_path=voice_path,
            variation=i,
            target_duration=target_dur,
        )

        if output_path:
            metadata = {
                "keyword":        keyword,
                "video_index":    i + 1,
                "target_duration": target_dur,
                "created_at":     datetime.now().strftime("%Y%m%d_%H%M%S"),
                "script":         script,
                "clips_used":     [str(p) for p in clip_paths],
                "output":         str(output_path),
                "post_caption":   (script.get("hook", "") + " " + script.get("cta", "")).strip(),
            }
            meta_path = output_path.parent / "post_metadata.json"
            meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
            generated.append(output_path)

    # Summary
    console.print()
    if generated:
        table = Table(title="Generated videos", show_header=True, header_style="bold green")
        table.add_column("#", style="dim", width=4)
        table.add_column("File")
        table.add_column("Folder")
        for idx, p in enumerate(generated, 1):
            table.add_row(str(idx), p.name, str(p.parent))
        console.print(table)
        console.print(f"\n[green]{len(generated)} video(s) saved in {SSD_BASE / keyword / 'output'}[/green]")
    else:
        console.print("[red]No videos were generated.[/red]")


# ---------------------------------------------------------------------------
# Stage 3 — Reclassify
# ---------------------------------------------------------------------------

def mode_reclassify(keyword: str) -> None:
    """
    Re-run Claude Vision on every clip in the 'unclassified' folder.
    Successfully classified clips are moved to their category folder.
    Clips that remain ambiguous stay in 'unclassified'.

    Requires ANTHROPIC_API_KEY.
    Cost: ~$0.002 per clip (1 frame via claude-haiku).
    """
    if not ANTHROPIC_API_KEY:
        console.print("[red]ANTHROPIC_API_KEY not set — cannot run Vision reclassification.[/red]")
        raise SystemExit(1)

    paths = get_keyword_paths(keyword)
    unclassified_dir = paths["clips"]["unclassified"]
    clips = sorted(unclassified_dir.glob("*.mp4"))

    if not clips:
        console.print("[yellow]No clips in 'unclassified' folder.[/yellow]")
        return

    console.print(f"\n[bold]Reclassifying {len(clips)} unclassified clip(s) with Claude Vision...[/bold]")
    console.print("[dim]Cost: ~$0.002 per clip[/dim]\n")

    moved = 0
    kept  = 0

    for clip_path in clips:
        duration = get_video_duration(clip_path)
        if not duration:
            kept += 1
            continue

        # Extract middle frame for Vision
        frame_path = paths["temp"] / f"reclassify_{clip_path.stem}.jpg"
        _extract_frame(clip_path, duration / 2, frame_path)

        category = classify_by_vision(frame_path, keyword)
        frame_path.unlink(missing_ok=True)

        if category != "unclassified":
            dest = paths["clips"][category] / clip_path.name
            clip_path.rename(dest)
            console.print(f"  [green]{clip_path.name}[/green]  →  [cyan]{category}[/cyan]")
            moved += 1
        else:
            console.print(f"  [dim]{clip_path.name}  →  unclassified (kept)[/dim]")
            kept += 1

    console.print(
        f"\n[green]{moved} clip(s) reclassified[/green]  |  "
        f"{kept} remain unclassified"
    )
    show_clips_summary(keyword)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def pick_mode(forced: str | None) -> str:
    """Ask user to choose a mode, unless already specified via --mode."""
    if forced:
        return forced

    console.print()
    console.print("[bold]What do you want to do?[/bold]")
    console.print("  [cyan]1[/cyan]  Extract clips from raw videos")
    console.print("  [cyan]2[/cyan]  Reclassify unclassified clips  [dim](Claude Vision)[/dim]")
    console.print("  [cyan]3[/cyan]  Generate videos from existing clips")
    console.print()
    choice = Prompt.ask("Choose", choices=["1", "2", "3"], default="1")
    return {"1": "extract", "2": "reclassify", "3": "generate"}[choice]


def main():
    parser = argparse.ArgumentParser(
        description="reels-factory -- Automated product video creator"
    )
    parser.add_argument(
        "--mode", choices=["extract", "reclassify", "generate"], default=None,
        help="extract | reclassify | generate",
    )
    # Extract flags
    parser.add_argument("--skip-voice",           action="store_true",
                        help="[extract] Skip Demucs voice separation")
    parser.add_argument("--skip-subtitle-check",  action="store_true",
                        help="[extract] Skip Claude Vision subtitle/text check")
    # Generate flags
    parser.add_argument("--skip-script",  action="store_true",
                        help="[generate] Skip script generation")
    parser.add_argument("--count", type=int, default=None,
                        help="[generate] Number of videos to generate (skips interactive prompt)")
    args = parser.parse_args()

    console.print(Panel.fit("[bold blue]reels-factory[/bold blue]", border_style="blue"))

    mode    = pick_mode(args.mode)
    keyword = pick_keyword()

    console.print(Panel.fit(
        f"[bold blue]reels-factory[/bold blue]\n"
        f"Mode:    [yellow]{mode}[/yellow]\n"
        f"Keyword: [yellow]{keyword}[/yellow]\n"
        f"Path:    [dim]{SSD_BASE / keyword}[/dim]",
        border_style="blue",
    ))

    if mode == "extract":
        mode_extract(keyword, args)
    elif mode == "reclassify":
        mode_reclassify(keyword)
    else:
        mode_generate(keyword, args)


if __name__ == "__main__":
    main()
