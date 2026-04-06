# CLAUDE.md — reels-factory

## Project
Automated product video creator for TikTok / Instagram Reels.
Takes competitor videos, extracts clips, generates scripts, assembles final video.

## OS
Windows 11 only — SSD is NTFS (Mac cannot write).
Use Windows paths: D:\Products Reels\[keyword]\

## Pipeline
1. analyze  — scene detection (FFmpeg) + speech detection
2. transcribe — Whisper (offline, ~base model)
3. separate  — Demucs htdemucs (offline, CPU, 2-5min per video — normal)
4. classify  — transcript keywords (free) or Claude Vision (paid, non-spoken only)
5. script    — Claude API, problem-solution framing, NO price/sales language
6. assemble  — MoviePy + FFmpeg, 9:16 format, 30fps

## Key rules
- NEVER mention price in scripts
- NEVER use "buy", "purchase", "shop", "order" in scripts
- Demucs takes 2-5 min per video on CPU — this is expected, do not kill the process
- Always run system_check before Demucs and Whisper
- SSD path: D:\Products Reels\ (verify drive letter in .env)
- music/ folder: drop royalty-free MP3s here before assembly

## Commands
```powershell
# Full pipeline
python main.py --keyword "wireless earbuds" --product "Smart TWS Earbuds 2-in-1"

# Analysis only (no assembly)
python main.py --keyword "wireless earbuds" --product "Smart TWS Earbuds" --skip-assembly

# Fast mode (no voice separation, no script)
python main.py --keyword "wireless earbuds" --product "Smart TWS Earbuds" --skip-voice --skip-script
```

## Input/Output
Input:  D:\Products Reels\[keyword]\raw\*.mp4
Output: D:\Products Reels\[keyword]\output\[datetime]\final.mp4
                                                       post_metadata.json

## Missing / future
- Caption burn-in (text overlays on video) — stub in caption_builder.py, not yet implemented
- Overlay (hook text, CTA text) — stub in overlay_builder.py, not yet implemented
- ElevenLabs voiceover — deferred, using extracted voice for now
- Auto-scheduling to Metricool — manual for now
