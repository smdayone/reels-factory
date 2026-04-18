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
import re
import subprocess
import time
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
from src.assembler.video_builder import (
    select_clips, get_clips_inventory,
    assemble_benefits, assemble_emotion,
    assemble_hook_transition, assemble_plot_twist,
    assemble_video,  # backwards-compat alias
)
from src.utils.asset_history import AssetHistory

console = Console()


def _clean_text(text: str) -> str:
    """Strip special/punctuation characters from overlay text.
    Keeps: letters, digits, spaces, apostrophe.
    Removes: em-dash, en-dash, slash, commas, periods, symbols, etc.
    """
    text = text.replace("\u2014", " ").replace("\u2013", " ")  # em-dash, en-dash
    text = text.replace("/", " ").replace("\\", " ")
    text = re.sub(r"[^\w\s']", " ", text)        # keep word chars + space + apostrophe
    text = re.sub(r"\s+", " ", text).strip()
    return text


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
    _SHOW_CATS = ["hook", "ai", "problem", "solution", "demo", "unboxing", "cta", "unclassified"]
    for category in _SHOW_CATS:
        clips = inventory.get(category, [])
        if not clips:
            continue  # skip empty / non-existent categories (incl. 'ai' when not loaded)
        dur = 0.0
        for c in clips:
            d = get_video_duration(c)
            if d:
                dur += d
        label = f"[bold green]{category} (AI)[/bold green]" if category == "ai" else category
        table.add_row(label, str(len(clips)), f"{dur:.1f}s")
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


def load_er_references(keyword: str, top_n: int = 3) -> list[dict]:
    """
    Load high-ER caption references from captions_reference.json.
    File format: [{"caption": "...", "er": 4.8}, ...]
    Returns the top_n entries sorted by ER descending, or [] if file missing.
    """
    ref_file = get_keyword_paths(keyword)["base"] / "captions_reference.json"
    if not ref_file.exists():
        return []
    try:
        refs = json.loads(ref_file.read_text(encoding="utf-8"))
        return sorted(refs, key=lambda x: x.get("er", 0), reverse=True)[:top_n]
    except Exception:
        return []


def process_video(
    video_path: Path,
    keyword: str,
    skip_voice: bool,
    skip_subtitle_check: bool,
    all_transcripts: list,
) -> None:
    """Run the full extract pipeline on a single raw video."""
    _t0 = time.time()
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

    elapsed = time.time() - _t0
    console.print(
        f"  [green]Done:[/green] {kept} clips kept  |  "
        f"{discarded_short} too short  |  {discarded_text} discarded (text overlay)  |  "
        f"[dim]{elapsed:.1f}s[/dim]"
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

    # ── Voice separation — opt-in, default OFF (Demucs: 2-5 min/video on CPU) ──
    if args.skip_voice:
        run_voice_sep = False
    else:
        run_voice_sep = Prompt.ask(
            "Separate voice from clips?  [dim](Demucs \u2014 removes competitor voice, keeps ambient sounds \u2014 2-5 min/video)[/dim]",
            choices=["y", "n"],
            default="n",
        ) == "y"

    # ── Subtitle check ────────────────────────────────────────────────────────
    skip_subtitle = args.skip_subtitle_check
    if not ANTHROPIC_API_KEY and not skip_subtitle:
        console.print("[yellow]No ANTHROPIC_API_KEY — subtitle check disabled[/yellow]")
        skip_subtitle = True

    # ── Checkpoint / resume ───────────────────────────────────────────────────
    progress_file = paths["base"] / ".extract_progress.json"
    skip_names: set[str] = set()

    if progress_file.exists():
        try:
            prev = json.loads(progress_file.read_text(encoding="utf-8"))
            done_count = len(prev.get("processed", []))
            resume = Prompt.ask(
                f"  Previous run found — {done_count}/{prev.get('total', '?')} video(s) done. Resume?",
                choices=["y", "n"],
                default="y",
            ) == "y"
            if resume:
                skip_names = set(prev.get("processed", []))
            else:
                progress_file.unlink(missing_ok=True)
        except Exception:
            progress_file.unlink(missing_ok=True)

    progress = {
        "started_at": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "total": len(raw_videos),
        "processed": list(skip_names),
    }
    progress_file.write_text(json.dumps(progress, indent=2), encoding="utf-8")

    # ── Main loop ─────────────────────────────────────────────────────────────
    all_transcripts: list[str] = []
    for video_path in raw_videos:
        if video_path.name in skip_names:
            console.print(f"\n[dim]Skipping (already done): {video_path.name}[/dim]")
            continue

        process_video(
            video_path,
            keyword,
            skip_voice=not run_voice_sep,
            skip_subtitle_check=skip_subtitle,
            all_transcripts=all_transcripts,
        )

        # Save progress after each successful video
        progress["processed"].append(video_path.name)
        progress_file.write_text(json.dumps(progress, indent=2), encoding="utf-8")

    # Clean up progress file — session complete
    progress_file.unlink(missing_ok=True)

    console.print()
    show_clips_summary(keyword)
    console.print("[green]Extract complete.[/green] Run again with [bold]--mode generate[/bold] to create videos.")
    return all_transcripts


# ---------------------------------------------------------------------------
# Stage 2 — Generate
# ---------------------------------------------------------------------------

# Pool of generic CTAs — one is picked at random per video
_GENERIC_CTAS = [
    "Get yours \u2192 link in bio \u2b06\ufe0f",
    "Tap the link in bio to get yours \U0001f446",
    "Don\u2019t miss out \u2014 link in bio \U0001f517",
    "Follow for more + link in bio \U0001f4f2",
]


def _pick_cta() -> "str | None":
    """
    Interactive CTA picker.
    Returns:
      str  — fixed CTA text used for every video (comment trigger)
      None — generic mode: a random CTA from _GENERIC_CTAS is picked per video
    """
    from rich.table import Table as _Table

    type_tbl = _Table(show_header=False, box=None, padding=(0, 2))
    type_tbl.add_column(style="cyan bold")
    type_tbl.add_column()
    type_tbl.add_row("1", "Comment trigger  [dim](ManyChat \u2014 1 o pi\u00f9 parole)[/dim]")
    type_tbl.add_row("2", "Generica         [dim](randomizzata per ogni video)[/dim]")
    console.print()
    console.print("[bold]Tipo di CTA:[/bold]")
    console.print(type_tbl)

    cta_type = Prompt.ask("Scelta", choices=["1", "2"], default="2")

    if cta_type == "1":
        trigger = Prompt.ask(
            "  Trigger  [dim](es. INFO \u00b7 FREE \u00b7 FREE GUIDE \u00b7 LINK NOW)[/dim]",
            default="INFO",
        ).upper().strip() or "INFO"
        cta_text = f"Comment \u2018{trigger}\u2019 below \U0001f447"
        console.print(f"  CTA: [green]{cta_text}[/green]\n")
        return cta_text

    # Generic — signal the loop to randomize per video
    console.print("  CTA: [dim]randomizzata per ogni video[/dim]\n")
    return None


def _pick_format() -> "tuple[str, str | None]":
    """
    Interactive picker for video format.
    Returns (format_name, base_format | None).
    base_format is set only for 'hook_transition'.
    """
    from rich.table import Table as _Table

    fmt_tbl = _Table(show_header=False, box=None, padding=(0, 2))
    fmt_tbl.add_column(style="cyan bold")
    fmt_tbl.add_column()
    fmt_tbl.add_row("0", "[bold green]Random[/bold green]          [dim](mix automatico a ogni video)[/dim]")
    fmt_tbl.add_row("1", "Benefits         [dim](hook + benefit texts + CTA)[/dim]")
    fmt_tbl.add_row("2", "Emotion          [dim](testo emotivo centrato fisso)[/dim]")
    fmt_tbl.add_row("3", "Hook Transition  [dim](clip intro + Benefits o Emotion)[/dim]")
    fmt_tbl.add_row("4", "Plot Twist       [dim](creator 3s + prodotto, 2 testi)[/dim]")
    console.print()
    console.print("[bold]Formato video:[/bold]")
    console.print(fmt_tbl)

    choice = Prompt.ask("Scelta", choices=["0", "1", "2", "3", "4"], default="0")
    fmt_map = {"0": "random", "1": "benefits", "2": "emotion", "3": "hook_transition", "4": "plot_twist"}
    fmt = fmt_map[choice]

    base_format = None
    if fmt == "hook_transition":
        base_tbl = _Table(show_header=False, box=None, padding=(0, 2))
        base_tbl.add_column(style="cyan bold")
        base_tbl.add_column()
        base_tbl.add_row("1", "Benefits")
        base_tbl.add_row("2", "Emotion")
        console.print()
        console.print("[bold]Base del video dopo l'hook:[/bold]")
        console.print(base_tbl)
        base_choice = Prompt.ask("Scelta", choices=["1", "2"], default="1")
        base_format = "benefits" if base_choice == "1" else "emotion"

    return fmt, base_format


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

    # Format — ask once, applied to every video in this batch
    fmt, base_format = _pick_format()

    # CTA — ask once, applied to every video in this batch
    cta_override = _pick_cta()

    fmt_label = fmt if base_format is None else f"{fmt} ({base_format})"
    console.print(f"\n[bold]Generating {n_videos} video(s) for [cyan]{keyword}[/cyan]  [dim]— {fmt_label}[/dim]...[/bold]\n")

    transcripts = load_existing_transcripts(keyword)
    er_refs = load_er_references(keyword)
    if er_refs:
        console.print(f"  [dim]Using {len(er_refs)} high-ER caption reference(s) as inspiration[/dim]\n")

    # Asset history — loaded once per batch, saved after each video
    paths = get_keyword_paths(keyword)
    history = AssetHistory(paths["base"])
    history.load()

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
                er_references=er_refs,
            )
            if script:
                # Clean overlay fields — remove special chars for human-like text
                _OVERLAY_FIELDS = ["hook", "problem", "solution", "proof",
                                   "emotion", "plot_hook", "plot_reveal"]
                for field in _OVERLAY_FIELDS:
                    if script.get(field):
                        script[field] = _clean_text(script[field])
                console.print(f"  [green]Script generated[/green]")
                console.print(f"  [bold]Hook:[/bold] {script.get('hook', '')}")

        # Override CTA: fixed string (comment trigger) or random generic per video
        script["cta"] = cta_override if cta_override is not None else random.choice(_GENERIC_CTAS)

        # Random target duration between 15 and 45 seconds
        target_dur = random.randint(15, 45)
        console.print(f"  Target duration: [yellow]{target_dur}s[/yellow]")

        # Different clips each iteration
        clip_paths = select_clips(keyword, variation=i, target_duration=target_dur)
        if not clip_paths:
            console.print("  [red]Not enough clips for this variation — skipping[/red]")
            continue

        # Resolve random format per-video
        _FORMATS_POOL = ["benefits", "emotion", "hook_transition", "plot_twist"]
        actual_fmt = (
            random.choice(_FORMATS_POOL) if fmt == "random" else fmt
        )
        actual_base = (
            random.choice(["benefits", "emotion"])
            if actual_fmt == "hook_transition" and base_format is None
            else base_format
        )
        if fmt == "random":
            console.print(f"  Format: [bold magenta]{actual_fmt}[/bold magenta]" + (
                f" [dim]({actual_base})[/dim]" if actual_base else ""
            ))

        asm_kwargs = dict(
            voice_path=None, variation=i,
            target_duration=target_dur, script=script,
            history=history,
        )
        _video_t0 = time.time()
        if actual_fmt == "benefits":
            output_path = assemble_benefits(keyword, clip_paths, **asm_kwargs)
        elif actual_fmt == "emotion":
            output_path = assemble_emotion(keyword, clip_paths, **asm_kwargs)
        elif actual_fmt == "hook_transition":
            output_path = assemble_hook_transition(
                keyword, clip_paths,
                base_format=actual_base or "benefits",
                **asm_kwargs,
            )
        elif actual_fmt == "plot_twist":
            output_path = assemble_plot_twist(keyword, clip_paths, **asm_kwargs)
        else:
            output_path = assemble_benefits(keyword, clip_paths, **asm_kwargs)

        _elapsed = time.time() - _video_t0
        _mins, _secs = divmod(int(_elapsed), 60)
        console.print(f"  [dim]\u23f1  Tempo: {_mins}m {_secs:02d}s[/dim]")

        if output_path:
            # Record text assets in history to avoid repetition in next videos
            if script.get("caption"):
                history.add("caption", script["caption"])
            for field in ["hook", "problem", "solution", "proof",
                          "emotion", "plot_hook", "plot_reveal"]:
                if script.get(field):
                    history.add(f"{field}_text", script[field])
            history.save()  # persist after every video

            metadata = {
                "keyword":         keyword,
                "video_index":     i + 1,
                "format":          actual_fmt,
                "base_format":     actual_base,
                "target_duration": target_dur,
                "created_at":      datetime.now().strftime("%Y%m%d_%H%M%S"),
                "script":          script,
                "clips_used":      [str(p) for p in clip_paths],
                "output":          str(output_path),
                "post_caption":    script.get("caption", ""),
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
