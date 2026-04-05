"""
reels-factory — Main entry point

Usage:
  python main.py --keyword "wireless earbuds" --product "Smart TWS Earbuds"
  python main.py --keyword "wireless earbuds" --product "Smart TWS Earbuds" --skip-voice
  python main.py --keyword "wireless earbuds" --product "Smart TWS Earbuds" --skip-script
"""
import argparse
import json
import subprocess
from pathlib import Path
from datetime import datetime
from rich.console import Console
from rich.panel import Panel

from config.settings import create_keyword_dirs, get_keyword_paths, ANTHROPIC_API_KEY
from src.utils.system_check import system_report, check_disk_space
from src.analyzer.scene_detector import detect_scenes, has_audio_speech, get_video_duration
from src.analyzer.transcriber import transcribe, save_transcript
from src.extractor.voice_separator import separate_voice
from src.extractor.clip_classifier import classify_by_transcript, classify_by_vision
from src.script.persona_builder import identify_persona
from src.script.script_generator import generate_script
from src.assembler.video_builder import select_clips, assemble_video

console = Console()


def process_video(video_path: Path, keyword: str, product_name: str,
                  skip_voice: bool, all_transcripts: list) -> None:
    """Process a single input video through the full pipeline."""
    console.print(f"\n[bold blue]Processing:[/bold blue] {video_path.name}")
    paths = get_keyword_paths(keyword)

    # 1. Detect if spoken
    is_spoken = has_audio_speech(video_path)
    console.print(f"  Speech detected: {'Yes' if is_spoken else 'No'}")

    # 2. Detect scene cuts
    scenes = detect_scenes(video_path)
    console.print(f"  Scene cuts found: {len(scenes) - 1}")

    # 3. Transcribe if spoken
    transcript = None
    if is_spoken:
        transcript = transcribe(video_path)
        save_transcript(
            transcript,
            paths["transcripts"] / video_path.stem
        )
        all_transcripts.append(transcript.get("text", ""))

    # 4. Separate voice (optional)
    if not skip_voice and is_spoken:
        separate_voice(video_path, paths["voice"])

    # 5. Extract and classify clips
    duration = get_video_duration(video_path)
    if duration is None:
        return

    for i in range(len(scenes) - 1):
        start = scenes[i]
        end = scenes[i + 1]
        clip_duration = end - start

        if clip_duration < 1.5:  # skip very short clips
            continue

        # Classify this segment
        if transcript and transcript.get("segments"):
            # Find transcript text for this time range
            seg_text = " ".join(
                s["text"] for s in transcript["segments"]
                if s["start"] >= start and s["end"] <= end + 1
            )
            category = classify_by_transcript(seg_text) if seg_text else "unclassified"
        else:
            # Extract a frame and use Vision
            frame_path = paths["temp"] / f"{video_path.stem}_frame_{i:03d}.jpg"
            _extract_frame(video_path, start + clip_duration / 2, frame_path)
            category = classify_by_vision(frame_path, product_name)

        # Cut clip with FFmpeg
        clip_out = paths["clips"][category] / f"{video_path.stem}_clip_{i:03d}.mp4"
        subprocess.run([
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", str(video_path),
            "-t", str(clip_duration),
            "-c:v", "libx264", "-c:a", "aac",
            "-loglevel", "error",
            str(clip_out)
        ], check=False)

    console.print(f"  [green]Clips extracted and classified[/green]")


def _extract_frame(video_path: Path, timestamp: float, output_path: Path):
    """Extract a single frame from video at given timestamp."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y",
        "-ss", str(timestamp),
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "2",
        "-loglevel", "error",
        str(output_path)
    ], check=False)


def main():
    parser = argparse.ArgumentParser(
        description="reels-factory — Automated product video creator"
    )
    parser.add_argument("--keyword",  required=True,
                        help="Product keyword (must match folder name on SSD)")
    parser.add_argument("--product",  required=True,
                        help="Product name for script generation")
    parser.add_argument("--skip-voice",   action="store_true",
                        help="Skip Demucs voice separation (faster)")
    parser.add_argument("--skip-script",  action="store_true",
                        help="Skip Claude script generation")
    parser.add_argument("--skip-assembly", action="store_true",
                        help="Only analyze and extract — do not assemble final video")
    args = parser.parse_args()

    console.print(Panel.fit(
        f"[bold blue]reels-factory[/bold blue]\n"
        f"Keyword: [yellow]{args.keyword}[/yellow]\n"
        f"Product: [yellow]{args.product}[/yellow]",
        border_style="blue"
    ))

    # System check
    system_report()
    create_keyword_dirs(args.keyword)
    paths = get_keyword_paths(args.keyword)

    if not check_disk_space(paths["base"]):
        console.print("[red]Insufficient disk space. Aborting.[/red]")
        return

    # Find input videos
    raw_videos = sorted(paths["raw"].glob("*.mp4"))
    if not raw_videos:
        console.print(f"[red]No .mp4 files found in: {paths['raw']}[/red]")
        console.print("Place downloaded videos in the raw/ folder and try again.")
        return

    console.print(f"\n[bold]Found {len(raw_videos)} video(s) to process[/bold]")

    # Process each video
    all_transcripts = []
    for video_path in raw_videos:
        process_video(
            video_path, args.keyword, args.product,
            args.skip_voice, all_transcripts
        )

    if args.skip_assembly:
        console.print("\n[yellow]--skip-assembly flag set. Stopping before assembly.[/yellow]")
        return

    # Generate script
    script = {}
    if not args.skip_script and ANTHROPIC_API_KEY:
        console.print("\n[bold]Generating script...[/bold]")
        persona = identify_persona(args.product, all_transcripts)
        console.print(f"  Persona identified: [cyan]{persona['name']}[/cyan]")
        problem_summary = persona.get("main_pain", "")
        script = generate_script(
            args.product,
            "tech accessories",
            persona,
            problem_summary
        )
        if script:
            console.print(f"  [green]Script generated[/green]")
            console.print(f"\n  [bold]Hook:[/bold] {script.get('hook', '')}")

    # Assemble final video
    console.print("\n[bold]Assembling final video...[/bold]")
    clip_paths = select_clips(args.keyword)
    output_path = assemble_video(
        args.keyword,
        clip_paths,
        voice_path=None,  # voice from most relevant clip — future enhancement
    )

    # Save metadata
    if output_path:
        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        metadata = {
            "product": args.product,
            "keyword": args.keyword,
            "created_at": date_str,
            "script": script,
            "clips_used": [str(p) for p in clip_paths],
            "output": str(output_path),
            "post_caption": script.get("hook", "") + " " + script.get("cta", ""),
        }
        meta_path = output_path.parent / "post_metadata.json"
        meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        console.print(f"\n[green]Metadata saved: {meta_path}[/green]")

    console.print(Panel.fit(
        f"[bold green]Done![/bold green]\n"
        f"Output: {output_path}\n"
        f"Place videos in Metricool for manual scheduling.",
        border_style="green"
    ))


if __name__ == "__main__":
    main()
