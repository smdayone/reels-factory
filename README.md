# reels-factory

Automated TikTok / Instagram Reels creator for e-commerce products.  
Takes competitor videos, extracts clips, generates AI scripts, assembles 9:16 final videos — fully non-interactive when needed.

---

## Table of contents

1. [Requirements](#requirements)
2. [Setup](#setup)
3. [Directory structure](#directory-structure)
4. [Pipeline overview](#pipeline-overview)
5. [CLI reference](#cli-reference)
   - [Mode flags](#mode-flags)
   - [Keyword / product](#keyword--product)
   - [Extract flags](#extract-flags)
   - [Generate flags](#generate-flags)
6. [Video formats](#video-formats)
7. [Non-interactive examples](#non-interactive-examples)
8. [Parallel assembly](#parallel-assembly)
9. [Asset history (anti-repetition)](#asset-history-anti-repetition)
10. [Music setup](#music-setup)
11. [Configuration (.env)](#configuration-env)

---

## Requirements

| Dependency | Version | Notes |
|---|---|---|
| Python | 3.11 + | 3.14 tested |
| FFmpeg | any recent | must be in `PATH` |
| MoviePy | 1.0.3 | pinned |
| Pillow | 10 + | text overlays |
| faster-whisper | latest | transcription, no torch needed |
| Demucs | latest | voice separation (CPU, 2-5 min/video) |
| Roboto Light font | — | install from Google Fonts → `C:\Windows\Fonts\Roboto-Light.ttf` |

```powershell
pip install -r requirements.txt
```

---

## Setup

1. **Copy `.env.example` → `.env`** and fill in your values (see [Configuration](#configuration-env)).
2. **Connect your SSD** — all assets live under `D:\Products Reels\` (configurable).
3. **Drop royalty-free MP3s** in the music folder (`D:\Music for Shorts\` by default).
4. **Install Roboto Light** — download from [fonts.google.com/specimen/Roboto](https://fonts.google.com/specimen/Roboto), install system-wide.
5. Run `python main.py --help` to verify the setup.

---

## Directory structure

```
D:\Products Reels\
└── <keyword>/                    # one folder per product / niche
    ├── raw/                      # drop competitor .mp4 files here
    ├── clips/
    │   ├── hook/                 # opening attention-grabbing clips
    │   ├── ai/                   # (optional) AI-generated clips — load manually
    │   ├── problem/              # clips showing the problem
    │   ├── solution/             # product-as-solution clips
    │   ├── demo/                 # demo / usage clips
    │   ├── cta/                  # call-to-action clips
    │   ├── unboxing/             # unboxing clips
    │   └── unclassified/         # clips that couldn't be auto-classified
    ├── output/
    │   └── <YYYYMMDD_HHMMSS_NN>/ # one folder per generated video
    │       ├── final.mp4
    │       └── post_metadata.json
    ├── transcripts/              # Whisper .txt files (auto-generated)
    ├── voice/                    # Demucs stems (no_vocals track)
    ├── temp/                     # temporary frames / files (auto-cleaned)
    ├── captions_reference.json   # (optional) high-ER caption examples
    └── .asset_history.json       # anti-repetition FIFO queue (auto-managed)
```

> **`clips/ai/`** is never created automatically — create it manually and drop
> AI-generated clips there. They are treated as high-priority (second only to hook clips).

---

## Pipeline overview

```
raw/*.mp4
  │
  ▼  --extract
  ├─ Scene detection (FFmpeg)
  ├─ Speech detection
  ├─ Transcription (Whisper)
  ├─ Voice separation (Demucs) [optional, --voice]
  ├─ Clip classification (transcript keywords or Claude Vision)
  └─ Subtitle / hardcoded-text check (Claude Vision) [optional]
       │
       ▼  --generate
       ├─ Persona identification
       ├─ Script generation (Claude API) → HOOK · PROBLEM · SOLUTION · PROOF · CTA · CAPTION
       ├─ Clip selection (seeded, anti-repetition)
       ├─ Assembly (MoviePy + FFmpeg)
       │   ├─ Text overlays — Roboto Light, white + black stroke
       │   ├─ Background music (royalty-free MP3, seeded rotation)
       │   └─ HD filter (FFmpeg unsharp + contrast boost)
       └─ output/<datetime_NN>/final.mp4  +  post_metadata.json
```

---

## CLI reference

```
python main.py [MODE] [--keyword KEYWORD] [OPTIONS]
```

Run with no arguments for fully interactive mode.

---

### Mode flags

Exactly one of these selects the pipeline stage to run.  
They are mutually exclusive with each other and with `--mode`.

| Flag | Equivalent | Description |
|---|---|---|
| `--extract` | `--mode extract` | Analyze raw videos, cut and classify clips |
| `--generate` | `--mode generate` | Generate final videos from existing clips |
| `--reclassify` | `--mode reclassify` | Re-run Claude Vision on `unclassified/` clips |
| `--mode <name>` | — | Long form: `extract` · `reclassify` · `generate` |

**Examples:**
```powershell
python main.py --extract
python main.py --generate
python main.py --mode reclassify
```

---

### Keyword / product

Skip the interactive folder picker entirely.

| Flag | Type | Description |
|---|---|---|
| `--keyword <name>` | string | Exact name of the product folder under `SSD_BASE_PATH` |
| `--product <name>` | string | Alias for `--keyword` (same behaviour) |

- If the folder doesn't exist yet, it is created automatically on first run.
- Either flag is accepted for both `--extract` and `--generate`.

**Examples:**
```powershell
# Use an existing keyword folder
python main.py --generate --keyword "hanging portable fan"

# --product is identical — use whichever reads more naturally
python main.py --extract  --product "pattern handbag purse"

# New keyword — folder will be created
python main.py --extract  --product "magnetic phone holder"
```

---

### Extract flags

Used with `--extract`. Control voice separation, subtitle checking and checkpoint resume.

---

#### `--voice`

Enable Demucs voice separation **without prompting**.  
Demucs removes the competitor's voice track while keeping ambient sounds and background music in every cut clip. Adds ~2-5 minutes per source video on CPU.

```powershell
# Non-interactive extract with voice separation enabled
python main.py --extract --product "hanging fan" --voice
```

> Omit `--voice` and omit `--skip-voice` if you want the interactive prompt to appear.

---

#### `--skip-voice`

Skip Demucs entirely. Clips keep the original audio track of the source video.  
Use this for a fast first pass when you just want clips quickly.

```powershell
python main.py --extract --product "hanging fan" --skip-voice
```

---

#### `--skip-subtitle-check`

Skip the Claude Vision frame-check that discards clips with hardcoded subtitles or brand overlays.  
Saves API cost; useful when source videos are known to be clean.

```powershell
python main.py --extract --product "hanging fan" --skip-voice --skip-subtitle-check
```

---

#### `--resume`

If a previous extract run was interrupted, **always resume** from the checkpoint without asking.  
Processed videos are skipped; only the remaining ones are processed.

```powershell
python main.py --extract --product "hanging fan" --resume
```

---

#### `--no-resume`

**Discard** the checkpoint and start the extract pipeline from scratch.  
All raw videos will be re-processed even if some were done in a previous run.

```powershell
python main.py --extract --product "hanging fan" --no-resume
```

> `--resume` and `--no-resume` are mutually exclusive. If neither is passed and a checkpoint exists, you will be asked interactively.

---

### Generate flags

Used with `--generate`. Control count, format, CTA and parallelism.

---

#### `--nvideos <N>` / `--count <N>`

Number of videos to generate. Both flags are equivalent.  
Valid range: 1 – 50.

```powershell
# Generate 12 videos
python main.py --generate --product "hanging fan" --nvideos 12

# Same with --count
python main.py --generate --product "hanging fan" --count 5
```

---

#### `--format <format>`

Video format applied to **every** video in the batch.  
Without this flag, you are asked interactively.

| Value | Description |
|---|---|
| `random` | A different format is picked at random for each video in the batch |
| `benefits` | Hook clip → benefit texts (HOOK · PROBLEM · SOLUTION · PROOF) → CTA |
| `emotion` | Hook clip → full-screen emotional statement → CTA |
| `hook_transition` | Dedicated intro clip → then Benefits or Emotion (see `--format-base`) |
| `plot_twist` | Creator clip (3 s) → plot-hook text → product reveal → plot-reveal text |

```powershell
# All 10 videos use the Benefits format
python main.py --generate --product "fan" --nvideos 10 --format benefits

# Random mix — different format per video
python main.py --generate --product "fan" --nvideos 10 --format random

# Hook Transition with Benefits base
python main.py --generate --product "fan" --nvideos 6 --format hook_transition --format-base benefits
```

---

#### `--format-base <base>`

Only relevant when `--format hook_transition`.  
Specifies what follows the intro hook clip.

| Value | Description |
|---|---|
| `benefits` | *(default)* Benefit texts + CTA after the hook |
| `emotion` | Full-screen emotional statement + CTA after the hook |

```powershell
python main.py --generate --product "fan" --format hook_transition --format-base emotion
```

---

#### `--cta-type <type>`

Choose the call-to-action style. Without this flag, you are asked interactively.

| Value | Description |
|---|---|
| `trigger` | A fixed "Comment 'WORD' below 👇" text — same across every video. Good for ManyChat automations. |
| `generic` | A random CTA is picked per video from a built-in pool (link in bio, tap the link, etc.) |

```powershell
# Generic CTA — different text each video
python main.py --generate --product "fan" --nvideos 8 --cta-type generic

# Trigger CTA — same text every video (default trigger: INFO)
python main.py --generate --product "fan" --nvideos 8 --cta-type trigger

# Trigger CTA with custom word
python main.py --generate --product "fan" --nvideos 8 --cta-type trigger --cta-trigger FREE
```

---

#### `--cta-trigger <word>`

The trigger word to use when `--cta-type trigger`.  
Case-insensitive; automatically uppercased.  
Default: `INFO`.

```powershell
python main.py --generate --product "fan" --cta-type trigger --cta-trigger "FREE GUIDE"
# → "Comment 'FREE GUIDE' below 👇"
```

---

#### `--skip-script`

Skip Claude API script generation entirely.  
Clips are assembled with no overlay text (no HOOK, PROBLEM, SOLUTION, PROOF, EMOTION).  
The CTA overlay is still applied.  
Useful for testing assembly without spending API credits.

```powershell
python main.py --generate --product "fan" --nvideos 3 --skip-script
```

---

#### `--parallel <N>`

Number of parallel assembly workers (1 – 4). Default: `1` (sequential).

Script generation always runs sequentially (API calls in series).  
Only the video assembly step (MoviePy + FFmpeg rendering) is parallelized.

**Thread safety:**  
Workers receive `history=None` — they do not write to `.asset_history.json`.  
The main thread updates the history file after each future completes, protected by a `threading.Lock`.

**When to use:**
- `--parallel 2` — good default on a mid-range CPU (prevents thermal throttling)
- `--parallel 3` / `--parallel 4` — only if your CPU has plenty of cores free; MoviePy is already multi-threaded internally

```powershell
# Generate 12 videos, 3 at a time
python main.py --generate --product "hanging fan" --nvideos 12 --parallel 3

# Maximum parallelism (4 workers)
python main.py --generate --product "fan" --nvideos 8 --parallel 4
```

> Output folders are named `YYYYMMDD_HHMMSS_NN` where `NN` is the variation index, so parallel runs never overwrite each other even when they start in the same second.

---

## Video formats

| Format | Clip sequence | Text overlays |
|---|---|---|
| **Benefits** | hook → problem → solution → demo/cta → unboxing | HOOK (30-50% Y) · PROBLEM · SOLUTION · PROOF · CTA |
| **Emotion** | hook → filler clips | HOOK · full-screen EMOTION text (58px, centered 50%) · CTA |
| **Hook Transition** | intro clip → Benefits or Emotion layout | as above for chosen base |
| **Plot Twist** | creator clip (3 s) → product clips | PLOT_HOOK during creator · PLOT_REVEAL after |

All overlay text: **Roboto Light**, white fill, 5px black stroke, no background pill.  
Hook Y position: randomised per video between **30 % – 50 %** of frame height (seed `variation + 13`).

---

## Non-interactive examples

### Single-stage runs

```powershell
# Extract — fast pass, no voice sep, no subtitle check, fresh start
python main.py --extract --product "hanging portable fan" --no-resume --skip-voice --skip-subtitle-check

# Extract — full quality, resume if interrupted
python main.py --extract --product "hanging portable fan" --resume --voice

# Generate — 6 random-format videos, generic CTA
python main.py --generate --product "hanging portable fan" --nvideos 6 --format random --cta-type generic

# Generate — 12 Benefits videos, trigger CTA "LINK", 3 parallel workers
python main.py --generate --product "hanging portable fan" --nvideos 12 --format benefits --cta-type trigger --cta-trigger LINK --parallel 3

# Reclassify leftover clips with Claude Vision
python main.py --reclassify --keyword "hanging portable fan"
```

### Pipeline (chain with `&&`)

```powershell
# Full pipeline — extract then generate 10 videos, no interaction at any point
python main.py --extract --product "hanging portable fan" --no-resume --skip-voice ^
  && python main.py --generate --product "hanging portable fan" --nvideos 10 --format random --cta-type generic --parallel 3

# Queue two different products back to back
python main.py --generate --product "pattern handbag purse" --nvideos 8 --format benefits --parallel 2 ^
  && python main.py --generate --product "magnetic phone holder" --nvideos 8 --format random --parallel 2
```

> On PowerShell use `^` for line continuation or write everything on one line.  
> `&&` runs the second command only if the first exits with code 0.

---

## Parallel assembly

```
┌──────────────────────────────────────────────────────────┐
│  Phase 1 — Script generation  (sequential, API calls)    │
│  Video 1 script → Video 2 script → … → Video N script   │
└──────────────────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│  Phase 2 — Assembly  (parallel, --parallel N)            │
│                                                          │
│  Worker 1: assemble video 1 ─────────────────────┐      │
│  Worker 2: assemble video 2 ──────────────────┐  │      │
│  Worker 3: assemble video 3 ───────────────┐  │  │      │
│                                            ↓  ↓  ↓      │
│  Main thread: as_completed → history.add → history.save │
└──────────────────────────────────────────────────────────┘
```

Each worker writes to its own uniquely-named output directory (`YYYYMMDD_HHMMSS_NN`).  
No file collisions, no shared state inside workers.

---

## Asset history (anti-repetition)

The file `D:\Products Reels\<keyword>\.asset_history.json` tracks the last **5** uses of each asset type.  
If a candidate asset is in the history, it is skipped and the next candidate is tried.

**Tracked asset types:**

| Key | What is tracked |
|---|---|
| `music` | Background track filename |
| `hook_clip` | Hook clip filename |
| `creator_clip` | Creator/plot-twist clip filename |
| `caption` | Full post caption text |
| `hook_text` | HOOK overlay text |
| `problem_text` | PROBLEM overlay text |
| `solution_text` | SOLUTION overlay text |
| `proof_text` | PROOF overlay text |
| `emotion_text` | EMOTION overlay text |
| `plot_hook_text` | PLOT_HOOK overlay text |
| `plot_reveal_text` | PLOT_REVEAL overlay text |

The history file is updated after every successfully assembled video and persists across sessions.  
Delete `.asset_history.json` to reset tracking for a keyword.

---

## Music setup

Drop royalty-free MP3 files into the folder specified by `MUSIC_FOLDER` in `.env` (default: `D:\Music for Shorts\`).  
Sub-folders by genre or mood are supported — all MP3s are discovered recursively.

Music selection is seeded per `keyword + format + variation` for deterministic but wide rotation.  
The anti-repetition system ensures the same track is not reused within the last 5 videos.

**Google Drive source (optional):**  
Set `MUSIC_SOURCE=drive` in `.env` and fill in `DRIVE_MUSIC_FOLDER_ID`.  
Files are cached locally in `MUSIC_CACHE_DIR`.

---

## Configuration (.env)

Copy `.env.example` to `.env` and edit.

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Claude API key (required for scripts + Vision) |
| `SSD_DRIVE` | `D` | Drive letter of your external SSD |
| `SSD_BASE_PATH` | `D:\Products Reels` | Root folder for all keyword folders |
| `WHISPER_MODEL` | `base` | `tiny` · `base` · `small` · `medium` · `large` |
| `DEMUCS_MODEL` | `htdemucs` | Demucs model — `htdemucs` is lightest on CPU |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Claude model for scripts and Vision |
| `TARGET_DURATION` | `30` | Target video length in seconds (also used by script prompt) |
| `MIN_CLIP_DURATION` | `2` | Clips shorter than this (seconds) are discarded |
| `MAX_CLIP_DURATION` | `6` | Clips longer than this are trimmed |
| `SCENE_THRESHOLD` | `0.4` | FFmpeg scene-cut sensitivity (0 = detect all cuts, 1 = none) |
| `RAM_SAFETY_GB` | `2` | Pause processing if free RAM drops below this value |
| `MUSIC_VOLUME` | `0.03` | Background music volume (0.0 – 1.0) |
| `MUSIC_FOLDER` | `D:\Music for Shorts` | Local folder with royalty-free MP3s |
| `MUSIC_SOURCE` | `local` | `local` or `drive` (Google Drive) |
| `HD_FILTER` | `true` | Apply FFmpeg sharpening pass after export (`true` / `false`) |

**HD filter** (`HD_FILTER=true`): runs `unsharp=5:5:1.0` + `eq=contrast=1.05:saturation=1.1` via FFmpeg on the final render, then replaces `final.mp4` in-place. Add `HD_FILTER=false` to `.env` to disable.
