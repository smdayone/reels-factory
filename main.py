"""
reels-factory — Main entry point

Modes: extract | reclassify | generate

Interactive (default):
  python main.py

Non-interactive examples:
  python main.py --extract  --product "hanging fan"  --no-resume --skip-voice
  python main.py --generate --product "hanging fan"  --nvideos 12 --format random
  python main.py --generate --product "hanging fan"  --nvideos 6 --parallel 3
  python main.py --generate --product "hanging fan"  --cta-type trigger --cta-trigger INFO
  python main.py --reclassify --keyword "wireless earbuds"

Pipeline (queue with |):
  python main.py --extract --product "fan" --no-resume && python main.py --generate --product "fan" --nvideos 10 --parallel 3

Output: D:/Products Reels/[keyword]/output/[YYYYMMDD_HHMMSS_NN]/final.mp4
                                                                  post_metadata.json
"""
import argparse
import concurrent.futures
import json
import random
import re
import subprocess
import threading
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
from src.analyzer.subtitle_detector import check_and_blur_text
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
from src.utils.languages import SUPPORTED_LANGUAGES, GENERIC_CTAS, CTA_TRIGGER_TEMPLATE
from src.utils.mispelling import apply_mispelling

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

def pick_keyword(args=None) -> str:
    """Scan SSD_BASE for folders and let the user choose one.
    If args.keyword or args.product is supplied, bypass interactive picker.
    """
    # Non-interactive bypass
    kw_arg = getattr(args, "keyword", None) or getattr(args, "product", None)
    if kw_arg:
        return kw_arg.strip()

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

        # Subtitle / hardcoded text check — blur regions instead of discarding
        if not skip_subtitle_check and clip_out.exists():
            had_text, blur_ok = check_and_blur_text(clip_out)
            if had_text:
                discarded_text += 1  # counter repurposed: "clips with text found"

        kept += 1

    elapsed = time.time() - _t0
    console.print(
        f"  [green]Done:[/green] {kept} clips kept  |  "
        f"{discarded_short} too short  |  {discarded_text} blurred (text overlay)  |  "
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
    elif getattr(args, "voice", False):
        run_voice_sep = True
        console.print("  Voice separation: [green]enabled[/green]  [dim](--voice)[/dim]")
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
            # Non-interactive resume flags: --resume / --no-resume
            if getattr(args, "no_resume", False):
                resume = False
                progress_file.unlink(missing_ok=True)
                console.print(f"  [dim]Previous run discarded (--no-resume)[/dim]")
            elif getattr(args, "resume", False):
                resume = True
                skip_names = set(prev.get("processed", []))
                console.print(
                    f"  Resuming previous run — [green]{done_count}[/green]/"
                    f"{prev.get('total', '?')} video(s) already done  [dim](--resume)[/dim]"
                )
            else:
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

def _pick_cta(args=None, language: str = "en") -> "str | None":
    """
    Interactive CTA picker (or non-interactive via args).
    Returns:
      str  — fixed CTA text used for every video (comment trigger)
      None — generic mode: a random CTA from GENERIC_CTAS[language] is picked per video

    Non-interactive:
      --cta-type trigger [--cta-trigger WORD]  → comment trigger (language-aware)
      --cta-type generic                        → randomized per video from language pool
    """
    cta_type_arg = getattr(args, "cta_type", None)
    if cta_type_arg:
        if cta_type_arg == "trigger":
            trigger = (getattr(args, "cta_trigger", None) or "INFO").upper().strip()
            template = CTA_TRIGGER_TEMPLATE.get(language, CTA_TRIGGER_TEMPLATE["en"])
            cta_text = template.format(trigger=trigger)
            console.print(f"  CTA: [green]{cta_text}[/green]  [dim](from --cta-type trigger)[/dim]\n")
            return cta_text
        # generic
        console.print("  CTA: [dim]randomizzata per ogni video (--cta-type generic)[/dim]\n")
        return None

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
        template = CTA_TRIGGER_TEMPLATE.get(language, CTA_TRIGGER_TEMPLATE["en"])
        cta_text = template.format(trigger=trigger)
        console.print(f"  CTA: [green]{cta_text}[/green]\n")
        return cta_text

    # Generic — signal the loop to randomize per video from the language pool
    console.print("  CTA: [dim]randomizzata per ogni video[/dim]\n")
    return None


def _pick_format(args=None) -> "tuple[str, str | None]":
    """
    Interactive picker for video format (or non-interactive via args).
    Returns (format_name, base_format | None).
    base_format is set only for 'hook_transition'.

    Non-interactive:
      --format random|benefits|emotion|hook_transition|plot_twist
      --format-base benefits|emotion   (only for hook_transition)
    """
    _VALID_FMTS = {"random", "benefits", "emotion", "hook_transition", "plot_twist"}
    fmt_arg = getattr(args, "format", None)
    if fmt_arg:
        fmt = fmt_arg.lower().replace("-", "_")
        if fmt not in _VALID_FMTS:
            console.print(f"  [yellow]Unknown --format '{fmt_arg}' — defaulting to random[/yellow]")
            fmt = "random"
        base_format = None
        if fmt == "hook_transition":
            fb = getattr(args, "format_base", None)
            base_format = fb if fb in ("benefits", "emotion") else "benefits"
        console.print(f"  Format: [magenta]{fmt}[/magenta]" + (
            f"  [dim](base: {base_format})[/dim]" if base_format else ""
        ) + "  [dim](from --format)[/dim]\n")
        return fmt, base_format

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


def _assemble_one(
    keyword: str,
    job: dict,
    history: "AssetHistory | None",
) -> "tuple[dict, Path | None, float]":
    """
    Run the assembly step for a single video job.
    Returns (job, output_path, elapsed_seconds).
    history=None is used when running in a worker thread (parallel mode).
    """
    _t0 = time.time()
    asm_kwargs = dict(
        voice_path=None,
        variation=job["i"],
        target_duration=job["target_dur"],
        script=job["script"],
        history=history,
        language=job.get("language", "en"),
    )
    fmt = job["actual_fmt"]
    clip_paths = job["clip_paths"]
    base = job["actual_base"]

    if fmt == "benefits":
        out = assemble_benefits(keyword, clip_paths, **asm_kwargs)
    elif fmt == "emotion":
        out = assemble_emotion(keyword, clip_paths, **asm_kwargs)
    elif fmt == "hook_transition":
        out = assemble_hook_transition(keyword, clip_paths,
                                       base_format=base or "benefits", **asm_kwargs)
    elif fmt == "plot_twist":
        out = assemble_plot_twist(keyword, clip_paths, **asm_kwargs)
    else:
        out = assemble_benefits(keyword, clip_paths, **asm_kwargs)

    return job, out, time.time() - _t0


def _update_history_and_meta(
    job: dict,
    output_path: Path,
    history: "AssetHistory",
    history_lock: "threading.Lock",
) -> None:
    """Record text assets + write post_metadata.json. Thread-safe via lock."""
    script = job["script"]
    with history_lock:
        if script.get("caption"):
            history.add("caption", script["caption"])
        for field in ["hook", "problem", "solution", "proof",
                      "emotion", "plot_hook", "plot_reveal"]:
            if script.get(field):
                history.add(f"{field}_text", script[field])
        history.save()

    metadata = {
        "keyword":         job["keyword"],
        "language":        job.get("language", "en"),
        "video_index":     job["i"] + 1,
        "format":          job["actual_fmt"],
        "base_format":     job["actual_base"],
        "target_duration": job["target_dur"],
        "created_at":      datetime.now().strftime("%Y%m%d_%H%M%S"),
        "script":          script,
        "clips_used":      [str(p) for p in job["clip_paths"]],
        "output":          str(output_path),
        "post_caption":    script.get("caption", ""),
    }
    meta_path = output_path.parent / "post_metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")


def mode_generate(keyword: str, args) -> None:
    """Generate N final videos from existing clips.

    Parallel mode (--parallel N):
      Script generation is always sequential (API calls).
      Assembly is dispatched to N worker threads simultaneously.
      Each worker gets history=None; the main thread records history after
      each future completes (thread-safe via Lock).
    """
    # ── Language selection ────────────────────────────────────────────────────
    language = getattr(args, "language", None) or None
    if language:
        language = language.lower().strip()
        if language not in SUPPORTED_LANGUAGES:
            console.print(
                f"[red]Unsupported language '{language}'.[/red]  Supported: "
                + "  ".join(f"[cyan]{k}[/cyan] ({v})" for k, v in SUPPORTED_LANGUAGES.items())
            )
            raise SystemExit(1)
    else:
        lang_choices = list(SUPPORTED_LANGUAGES.keys())
        console.print("\n[bold]Language[/bold]")
        for i, (k, v) in enumerate(SUPPORTED_LANGUAGES.items(), 1):
            console.print(f"  [cyan]{i}[/cyan]  {v} ({k})")
        raw = Prompt.ask("Choose a number", default="1").strip()
        try:
            idx = int(raw) - 1
            language = lang_choices[idx] if 0 <= idx < len(lang_choices) else "en"
        except (ValueError, IndexError):
            language = "en"

    # ── Mispelling mode ───────────────────────────────────────────────────────
    from config.settings import MISPELLING_MODE
    use_mispelling = getattr(args, "mispelling", False)
    if not use_mispelling:
        if MISPELLING_MODE == "true":
            use_mispelling = True
        elif MISPELLING_MODE == "ask":
            use_mispelling = Prompt.ask(
                "\nVuoi applicare un mispelling intenzionale?",
                choices=["s", "n"], default="n",
            ) == "s"
    if use_mispelling:
        console.print("  [magenta]Mispelling:[/magenta] attivo — un errore per video\n")

    total_clips = show_clips_summary(keyword)

    if total_clips == 0:
        console.print("[red]No clips found.[/red] Run [bold]--mode extract[/bold] first.")
        raise SystemExit(1)

    # How many videos?
    n_videos_arg = getattr(args, "nvideos", None) or getattr(args, "count", None)
    if n_videos_arg:
        n_videos = n_videos_arg
    else:
        n_videos = IntPrompt.ask("How many videos to generate?", default=3)
    n_videos = max(1, min(n_videos, 50))  # clamp 1-50

    # Parallel workers
    parallel = max(1, min(getattr(args, "parallel", 1) or 1, 4, n_videos))

    # Format & CTA — bypass interactive if flags supplied; CTA is language-aware
    fmt, base_format = _pick_format(args)
    cta_override = _pick_cta(args, language=language)

    fmt_label = fmt if base_format is None else f"{fmt} ({base_format})"
    parallel_label = f"  [dim]parallel: {parallel} workers[/dim]" if parallel > 1 else ""
    console.print(
        f"\n[bold]Generating {n_videos} video(s) for [cyan]{keyword}[/cyan]  "
        f"[dim]— {fmt_label}  "
        f"[cyan]{SUPPORTED_LANGUAGES[language]}[/cyan] ({language})[/dim][/bold]"
        f"{parallel_label}\n"
    )

    transcripts = load_existing_transcripts(keyword)
    er_refs = load_er_references(keyword)
    if er_refs:
        console.print(f"  [dim]Using {len(er_refs)} high-ER caption reference(s) as inspiration[/dim]\n")

    # Asset history — loaded once per batch
    paths = get_keyword_paths(keyword)
    history = AssetHistory(paths["base"])
    history.load()
    history_lock = threading.Lock()

    _FORMATS_POOL = ["benefits", "emotion", "hook_transition", "plot_twist"]
    _OVERLAY_FIELDS = ["hook", "problem", "solution", "proof",
                       "emotion", "plot_hook", "plot_reveal"]

    # ── Phase 1: Script generation (always sequential) ────────────────────────
    jobs: list[dict] = []
    for i in range(n_videos):
        console.print(Panel.fit(
            f"[bold cyan]Script {i + 1} / {n_videos}[/bold cyan]",
            border_style="cyan",
        ))

        script: dict = {}
        if not args.skip_script and ANTHROPIC_API_KEY:
            persona = identify_persona(keyword, transcripts)
            console.print(f"  Persona: [cyan]{persona['name']}[/cyan]")

            # Collect recently used overlay texts so Claude can avoid repeating them.
            # This covers both cross-batch repetition (from history on disk) and
            # within-batch repetition (texts added to in-memory history below).
            _TEXT_ASSET_TYPES = [
                "hook_text", "problem_text", "solution_text", "proof_text",
                "emotion_text", "plot_hook_text", "plot_reveal_text", "caption",
            ]
            recent_texts = {k: history.get_recent(k, n=5) for k in _TEXT_ASSET_TYPES}
            recent_texts = {k: v for k, v in recent_texts.items() if v}  # drop empty

            script = generate_script(
                keyword, "product",
                persona, persona.get("main_pain", ""),
                er_references=er_refs,
                language=language,
                recent_texts=recent_texts or None,
            )
            if script:
                skipped = []
                for field in _OVERLAY_FIELDS:
                    val = script.get(field, "")
                    if val and val.strip().upper() == "SKIP":
                        script[field] = None
                        skipped.append(field)
                    elif val:
                        script[field] = _clean_text(val)
                if skipped:
                    console.print(f"  [dim]Sections skipped (redundant): {', '.join(skipped)}[/dim]")

                # Enforce caption: single line, max 100 chars
                cap = script.get("caption", "")
                if cap:
                    cap = cap.replace("\n", " ").strip()
                    if len(cap) > 100:
                        # Trim keeping hashtags: cut body, preserve last 2-3 hashtags
                        words = cap.split()
                        hashtags = [w for w in words if w.startswith("#")][-3:]
                        body_words = [w for w in words if not w.startswith("#")]
                        body = " ".join(body_words)
                        suffix = " " + " ".join(hashtags) if hashtags else ""
                        max_body = 100 - len(suffix)
                        body = body[:max_body].rsplit(" ", 1)[0]
                        cap = (body + suffix).strip()
                    script["caption"] = cap

                # Mispelling intenzionale — un errore per video, dopo _clean_text
                if use_mispelling:
                    keyword_path = get_keyword_paths(keyword)["base"]
                    script, mis_entry = apply_mispelling(script, keyword_path, language)
                    if mis_entry:
                        console.print(
                            f"  [magenta][MISPELLING][/magenta] "
                            f"'{mis_entry['original_word']}' → '{mis_entry['mispelled_word']}' "
                            f"({mis_entry['method']}, campo: {mis_entry['field']})"
                        )
                    else:
                        console.print("  [dim][MISPELLING] Nessuna parola eleggibile trovata[/dim]")

                console.print(f"  [green]Script generated[/green]  [dim]({SUPPORTED_LANGUAGES[language]})[/dim]")
                console.print(f"  [bold]Hook:[/bold] {script.get('hook', '')}")

                # Pre-register into in-memory history so the next iteration avoids repeating.
                for field in _OVERLAY_FIELDS:
                    if script.get(field):
                        history.add(f"{field}_text", script[field])
                if script.get("caption"):
                    history.add("caption", script["caption"])

        script["cta"] = cta_override if cta_override is not None else random.choice(GENERIC_CTAS[language])

        target_dur = random.randint(15, 45)
        console.print(f"  Target duration: [yellow]{target_dur}s[/yellow]")

        clip_paths = select_clips(keyword, variation=i, target_duration=target_dur)
        if not clip_paths:
            console.print("  [red]Not enough clips for this variation — skipping[/red]")
            continue

        actual_fmt = random.choice(_FORMATS_POOL) if fmt == "random" else fmt
        actual_base = (
            random.choice(["benefits", "emotion"])
            if actual_fmt == "hook_transition" and base_format is None
            else base_format
        )
        if fmt == "random":
            console.print(f"  Format: [bold magenta]{actual_fmt}[/bold magenta]" + (
                f" [dim]({actual_base})[/dim]" if actual_base else ""
            ))

        jobs.append(dict(
            i=i, keyword=keyword, script=script,
            clip_paths=clip_paths, actual_fmt=actual_fmt,
            actual_base=actual_base, target_dur=target_dur,
            language=language,
        ))

    if not jobs:
        console.print("[red]No jobs to assemble.[/red]")
        return

    # ── Phase 2: Assembly ─────────────────────────────────────────────────────
    console.print(f"\n[bold]Assembling {len(jobs)} video(s)...[/bold]\n")
    generated: list[Path] = []

    if parallel <= 1:
        # Sequential — history is passed directly, updated after each video
        for job in jobs:
            _t0 = time.time()
            _, output_path, elapsed = _assemble_one(keyword, job, history)
            _mins, _secs = divmod(int(elapsed), 60)
            console.print(f"  [dim]\u23f1  Tempo: {_mins}m {_secs:02d}s[/dim]")

            if output_path:
                _update_history_and_meta(job, output_path, history, history_lock)
                generated.append(output_path)
                console.print(
                    f"  [green]\u2713 Video {job['i'] + 1}[/green]  "
                    f"[dim]{output_path.parent.name}[/dim]"
                )
    else:
        # Parallel — workers get history=None; main thread updates history
        # via lock as futures complete
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as executor:
            future_to_job = {
                executor.submit(_assemble_one, keyword, job, None): job
                for job in jobs
            }
            for future in concurrent.futures.as_completed(future_to_job):
                try:
                    job, output_path, elapsed = future.result()
                except Exception as exc:
                    job = future_to_job[future]
                    console.print(
                        f"  [red]Video {job['i'] + 1} failed: {exc}[/red]"
                    )
                    continue

                _mins, _secs = divmod(int(elapsed), 60)
                if output_path:
                    _update_history_and_meta(job, output_path, history, history_lock)
                    generated.append(output_path)
                    console.print(
                        f"  [green]\u2713 Video {job['i'] + 1}[/green]  "
                        f"[dim]{job['actual_fmt']}  {_mins}m {_secs:02d}s  "
                        f"{output_path.parent.name}[/dim]"
                    )
                else:
                    console.print(
                        f"  [yellow]\u2717 Video {job['i'] + 1} — assembly returned None  "
                        f"[dim]{_mins}m {_secs:02d}s[/dim][/yellow]"
                    )

    # ── Summary ───────────────────────────────────────────────────────────────
    console.print()
    if generated:
        table = Table(title="Generated videos", show_header=True, header_style="bold green")
        table.add_column("#", style="dim", width=4)
        table.add_column("File")
        table.add_column("Folder")
        for idx, p in enumerate(
            sorted(generated, key=lambda p: p.parent.name), 1
        ):
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
# Stage 4 — Publish (git commit + push)
# ---------------------------------------------------------------------------

def mode_publish(args) -> None:
    """
    Commit all pending changes and push to origin/main.

    Stages every file that is tracked and modified (git add -u) plus any new
    files inside the source tree that are not excluded by .gitignore.
    Sensitive files (.env, *.tmp, media, model weights) are already covered
    by .gitignore and will never be staged.

    Non-interactive flags:
      --message / -m  commit message (prompted if omitted)
      --yes    / -y   skip confirmation prompt
    """
    import shutil

    if not shutil.which("git"):
        console.print("[red]git not found in PATH — install git and try again.[/red]")
        raise SystemExit(1)

    # ── Show current status ───────────────────────────────────────────────────
    status_result = subprocess.run(
        ["git", "status", "--short"],
        capture_output=True, text=True,
    )
    status_lines = [l for l in status_result.stdout.splitlines() if l.strip()]

    if not status_lines:
        console.print("[yellow]Nothing to commit — working tree is clean.[/yellow]")
        return

    console.print("\n[bold]Pending changes:[/bold]")
    for line in status_lines:
        flag = line[:2].strip()
        path = line[3:]
        if flag in ("M", "MM"):
            color = "yellow"
        elif flag in ("A", "??"):
            color = "green"
        elif flag in ("D",):
            color = "red"
        else:
            color = "white"
        console.print(f"  [{color}]{flag or '?'}[/{color}]  {path}")

    # ── Commit message ────────────────────────────────────────────────────────
    message = getattr(args, "message", None) or ""
    message = message.strip()
    if not message:
        console.print()
        message = Prompt.ask("Commit message").strip()
    if not message:
        console.print("[red]Commit message cannot be empty. Aborting.[/red]")
        raise SystemExit(1)

    # ── Confirmation ──────────────────────────────────────────────────────────
    skip_confirm = getattr(args, "yes", False)
    if not skip_confirm:
        console.print()
        confirm = Prompt.ask(
            f'Commit [bold green]"{message}"[/bold green] and push to [cyan]origin/main[/cyan]?',
            choices=["y", "n"],
            default="y",
        )
        if confirm != "y":
            console.print("[yellow]Aborted.[/yellow]")
            return

    # ── Stage ─────────────────────────────────────────────────────────────────
    # git add -u  → modified + deleted tracked files
    subprocess.run(["git", "add", "-u"], check=True)
    # git add .   → new untracked files (respects .gitignore — .env, *.tmp,
    #               media files and model weights are already excluded)
    subprocess.run(["git", "add", "."], check=True)

    # ── Commit ────────────────────────────────────────────────────────────────
    commit_result = subprocess.run(
        ["git", "commit", "-m", message],
        capture_output=True, text=True,
    )
    if commit_result.returncode != 0:
        # "nothing to commit" after add is not a real error
        if "nothing to commit" in commit_result.stdout + commit_result.stderr:
            console.print("[yellow]Nothing new to commit after staging.[/yellow]")
            return
        console.print(f"[red]Commit failed:[/red]\n{commit_result.stderr.strip()}")
        raise SystemExit(1)

    # Print the short commit hash from the commit output
    commit_line = commit_result.stdout.strip().splitlines()[0] if commit_result.stdout else ""
    console.print(f"  [green]OK Committed[/green]  [dim]{commit_line}[/dim]")

    # ── Push ──────────────────────────────────────────────────────────────────
    console.print("  Pushing to [cyan]origin/main[/cyan]...")
    push_result = subprocess.run(
        ["git", "push", "origin", "main"],
        capture_output=True, text=True,
    )
    if push_result.returncode != 0:
        console.print(f"[red]Push failed:[/red]\n{push_result.stderr.strip()}")
        raise SystemExit(1)

    console.print("  [green]OK Pushed to origin/main[/green]")
    console.print(
        f"\n[bold green]Published.[/bold green]  "
        f"[dim]https://github.com/smdayone/reels-factory[/dim]"
    )


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
        description="reels-factory — Automated product video creator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples (non-interactive):
  python main.py --extract  --product "hanging fan"  --no-resume --skip-voice
  python main.py --generate --product "hanging fan"  --nvideos 12 --format random --cta-type generic
  python main.py --generate --product "hanging fan"  --nvideos 6  --format benefits --parallel 3
  python main.py --generate --product "hanging fan"  --nvideos 4  --cta-type trigger --cta-trigger INFO
  python main.py --reclassify --keyword "wireless earbuds"

Pipeline (queue multiple runs):
  python main.py --extract --product "fan" --no-resume && python main.py --generate --product "fan" --nvideos 10 --parallel 3
        """,
    )

    # ── Mode shortcuts (mutually exclusive with --mode) ───────────────────────
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--mode", choices=["extract", "reclassify", "generate", "publish"], default=None,
        help="Mode selector (long form)",
    )
    mode_group.add_argument("--extract",    dest="mode", action="store_const", const="extract",
                            help="Shortcut: run extract mode")
    mode_group.add_argument("--generate",   dest="mode", action="store_const", const="generate",
                            help="Shortcut: run generate mode")
    mode_group.add_argument("--reclassify", dest="mode", action="store_const", const="reclassify",
                            help="Shortcut: run reclassify mode")
    mode_group.add_argument("--publish",    dest="mode", action="store_const", const="publish",
                            help="Shortcut: commit and push changes to GitHub")

    # ── Keyword / product (bypass interactive picker) ─────────────────────────
    parser.add_argument("--keyword",  type=str, default=None,
                        help="Keyword / product folder name (skips interactive picker)")
    parser.add_argument("--product",  type=str, default=None,
                        help="Alias for --keyword")

    # ── Extract flags ─────────────────────────────────────────────────────────
    parser.add_argument("--voice",                action="store_true",
                        help="[extract] Enable Demucs voice separation without prompting")
    parser.add_argument("--skip-voice",           action="store_true",
                        help="[extract] Skip Demucs voice separation")
    parser.add_argument("--skip-subtitle-check",  action="store_true",
                        help="[extract] Skip Claude Vision subtitle/text check")
    parser.add_argument("--resume",    action="store_true", default=False,
                        help="[extract] Always resume previous run without prompting")
    parser.add_argument("--no-resume", action="store_true", default=False,
                        help="[extract] Discard previous run checkpoint and start fresh")

    # ── Generate flags ────────────────────────────────────────────────────────
    parser.add_argument("--skip-script", action="store_true",
                        help="[generate] Skip script generation")
    parser.add_argument("--count",   type=int, default=None,
                        help="[generate] Number of videos (alias: --nvideos)")
    parser.add_argument("--nvideos", type=int, default=None,
                        help="[generate] Number of videos to generate")
    parser.add_argument(
        "--format", type=str, default=None,
        metavar="FORMAT",
        help="[generate] Video format: random | benefits | emotion | hook_transition | plot_twist",
    )
    parser.add_argument(
        "--format-base", type=str, default=None,
        dest="format_base",
        metavar="BASE",
        help="[generate] Base format for hook_transition: benefits | emotion  (default: benefits)",
    )
    parser.add_argument(
        "--cta-type", type=str, default=None,
        dest="cta_type",
        choices=["trigger", "generic"],
        help="[generate] CTA type: trigger | generic",
    )
    parser.add_argument(
        "--cta-trigger", type=str, default=None,
        dest="cta_trigger",
        metavar="WORD",
        help="[generate] Trigger word for --cta-type trigger  (default: INFO)",
    )
    parser.add_argument(
        "--parallel", type=int, default=1,
        metavar="N",
        help="[generate] Number of parallel assembly workers (1-4, default: 1)",
    )
    parser.add_argument(
        "--mispelling",
        action="store_true",
        default=False,
        dest="mispelling",
        help="[generate] Applica un mispelling intenzionale a un testo overlay per video",
    )
    parser.add_argument(
        "--language", "--lang",
        type=str, default=None,
        dest="language",
        metavar="LANG",
        help=(
            "[generate] Language for all generated text "
            f"(default: en). Supported: "
            + ", ".join(f"{k} ({v})" for k, v in SUPPORTED_LANGUAGES.items())
        ),
    )

    # ── Publish flags ─────────────────────────────────────────────────────────
    parser.add_argument(
        "--message", "-m",
        type=str, default=None,
        metavar="MSG",
        help="[publish] Commit message (prompted interactively if omitted)",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="[publish] Skip confirmation prompt",
    )

    args = parser.parse_args()

    console.print(Panel.fit("[bold blue]reels-factory[/bold blue]", border_style="blue"))

    mode = pick_mode(args.mode)

    # --publish does not need a keyword — skip the product picker entirely
    if mode == "publish":
        mode_publish(args)
        return

    keyword = pick_keyword(args)

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
