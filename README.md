# reels-factory

Automated TikTok / Instagram Reels creator for e-commerce products.  
Takes competitor videos, extracts clips, generates AI scripts, assembles 9:16 final videos — fully non-interactive when needed.

Runs on **Windows 10/11** and **macOS 12+ (Apple Silicon & Intel)**.

---

## Table of contents

1. [Requirements](#requirements)
2. [Setup — macOS](#setup--macos)
3. [Setup — Windows](#setup--windows)
4. [SSD setup — exFAT (recommended for cross-platform use)](#ssd-setup--exfat)
5. [Directory structure](#directory-structure)
6. [Pipeline overview](#pipeline-overview)
7. [CLI reference](#cli-reference)
8. [Video formats](#video-formats)
9. [Non-interactive examples](#non-interactive-examples)
10. [Parallel assembly](#parallel-assembly)
11. [Asset history (anti-repetition)](#asset-history-anti-repetition)
12. [Music setup](#music-setup)
13. [Configuration (.env)](#configuration-env)

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
| TikTok Sans Bold | OFL-1.1 | [Google Fonts](https://fonts.google.com/specimen/TikTok+Sans) or [GitHub releases](https://github.com/tiktok/TikTokSans/releases) |

---

## Setup — macOS

### 1. Install Homebrew (if not already installed)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### 2. Install system dependencies

```bash
brew install python@3.11 ffmpeg
```

Verify:

```bash
python3 --version   # Python 3.11.x or newer
ffmpeg -version     # any recent build
```

### 3. Clone the repo and install Python packages

```bash
git clone https://github.com/your-org/reels-factory.git
cd reels-factory
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> **Tip:** add `source /path/to/reels-factory/.venv/bin/activate` to your `~/.zshrc` so the
> environment activates automatically in new terminal sessions.

### 4. Install TikTok Sans Bold font

Download `TikTokSans-Bold.ttf` (and optionally `TikTokSans-Black.ttf`) from
[fonts.google.com/specimen/TikTok+Sans](https://fonts.google.com/specimen/TikTok+Sans)
(click **Download family**) or from
[github.com/tiktok/TikTokSans/releases](https://github.com/tiktok/TikTokSans/releases).

Install the font:

```bash
# Copy to user fonts folder (no admin required)
cp TikTokSans-Bold.ttf ~/Library/Fonts/
cp TikTokSans-Black.ttf ~/Library/Fonts/   # optional
```

Font fallback chain if not installed: TikTok Sans Black → Roboto Light → PIL default.

### 5. Configure `.env`

```bash
cp .env.example .env
```

Edit `.env` and set `SSD_BASE_PATH` to wherever your product folders live:

```dotenv
# macOS — exFAT SSD mounted at /Volumes/ReelsFactory
SSD_BASE_PATH=/Volumes/ReelsFactory/Products Reels
MUSIC_FOLDER=/Volumes/ReelsFactory/Music for Shorts

# macOS — local folder (no external SSD)
# SSD_BASE_PATH=/Users/yourname/Products Reels
# MUSIC_FOLDER=/Users/yourname/Music for Shorts
```

### 6. Verify

```bash
python main.py --help
```

---

## Setup — Windows

### 1. Install Python 3.11+

Download from [python.org/downloads](https://www.python.org/downloads/).  
During installation, check **"Add Python to PATH"**.

### 2. Install FFmpeg

Download a pre-built binary from [ffmpeg.org/download.html](https://ffmpeg.org/download.html)
(e.g. the gyan.dev build).  
Extract and add the `bin/` folder to your system `PATH`.

Verify in PowerShell:

```powershell
python --version
ffmpeg -version
```

### 3. Clone the repo and install Python packages

```powershell
git clone https://github.com/your-org/reels-factory.git
cd reels-factory
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 4. Install TikTok Sans Bold font

Download `TikTokSans-Bold.ttf` from
[fonts.google.com/specimen/TikTok+Sans](https://fonts.google.com/specimen/TikTok+Sans)
or [github.com/tiktok/TikTokSans/releases](https://github.com/tiktok/TikTokSans/releases).

Double-click the `.ttf` file → **Install for all users** (installs to `C:\Windows\Fonts\`).  
Optionally install `TikTokSans-Black.ttf` as well.

Font fallback chain if not installed: TikTok Sans Black → Roboto Light → Impact → Segoe UI Bold → Arial Bold → PIL default.

### 5. Configure `.env`

```powershell
copy .env.example .env
```

Edit `.env` and set `SSD_BASE_PATH` to your products folder:

```dotenv
# Windows — exFAT or NTFS SSD on drive D:
SSD_BASE_PATH=D:\Products Reels
MUSIC_FOLDER=D:\Music for Shorts
SSD_DRIVE=D
```

### 6. Verify

```powershell
python main.py --help
```

---

## SSD setup — exFAT

**exFAT** is the recommended format if you use the same SSD on both Windows and macOS.
It is natively supported (read + write, no drivers needed) on both operating systems.

> If you only use the SSD on one OS, skip this section.

### What you need

- A backup of all data currently on the SSD (reformatting erases everything).
- ~15 minutes.

---

### Reformat on Windows

1. Back up all data from the SSD to another drive.
2. Open **File Explorer** → right-click the SSD → **Format…**
3. Set **File system** to `exFAT`.
4. Give it a label (e.g. `ReelsFactory`).
5. Click **Start** → **OK** to confirm.

After formatting, recreate your folder structure:

```
ReelsFactory\
├── Products Reels\     ← SSD_BASE_PATH
└── Music for Shorts\   ← MUSIC_FOLDER
```

---

### Reformat on macOS

1. Back up all data from the SSD.
2. Open **Disk Utility** (Spotlight → type "Disk Utility").
3. Select the SSD in the left sidebar (the top-level disk, not a partition).
4. Click **Erase** in the toolbar.
5. Set **Name** to `ReelsFactory` (or any label you prefer).
6. Set **Format** to `ExFAT`.
7. Set **Scheme** to `Master Boot Record` (required for Windows compatibility).
8. Click **Erase** → **Done**.

After formatting, create the folder structure:

```bash
mkdir -p "/Volumes/ReelsFactory/Products Reels"
mkdir -p "/Volumes/ReelsFactory/Music for Shorts"
```

---

### Mount the SSD on macOS after reformatting

The SSD mounts automatically when plugged in. Its path will be:

```
/Volumes/ReelsFactory/
```

Set your `.env` accordingly:

```dotenv
SSD_BASE_PATH=/Volumes/ReelsFactory/Products Reels
MUSIC_FOLDER=/Volumes/ReelsFactory/Music for Shorts
```

On Windows the SSD will continue to appear as a drive letter (e.g. `D:`), no changes needed.

---

## Directory structure

```
<SSD_BASE_PATH>/
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
    │   └── <lang>/
    │       └── <YYYYMMDD_HHMMSS_NN>/
    │           ├── <YYYY-MM-DD>_<format>_<lang>.mp4
    │           └── post_metadata.json
    ├── transcripts/              # Whisper .txt files (auto-generated)
    ├── voice/                    # Demucs stems (no_vocals track)
    ├── temp/                     # temporary frames / files (auto-cleaned)
    ├── captions_reference.json   # (optional) high-ER caption examples
    └── .asset_history.json       # anti-repetition FIFO queue (auto-managed)
```

> **`clips/ai/`** is never created automatically — create it manually and drop
> AI-generated clips there. They are treated as high-priority (second only to hook clips),
> with a cap of 2 AI clips per video.

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
       ├─ Anti-repetition context injection (recent texts → Claude prompt)
       ├─ Clip selection (seeded, anti-repetition)
       ├─ Assembly (MoviePy + FFmpeg)
       │   ├─ Text overlays — TikTok Sans Bold, white + black stroke
       │   ├─ Background music (royalty-free MP3, seeded rotation)
       │   └─ HD filter (FFmpeg unsharp + contrast boost)
       └─ output/<lang>/<datetime_NN>/<date>_<format>_<lang>.mp4  +  post_metadata.json
```

---

## CLI reference

```
python main.py [MODE] [--keyword KEYWORD] [OPTIONS]
```

Run with no arguments for fully interactive mode.

---

### Mode flags

| Flag | Equivalent | Description |
|---|---|---|
| `--extract` | `--mode extract` | Analyze raw videos, cut and classify clips |
| `--generate` | `--mode generate` | Generate final videos from existing clips |
| `--reclassify` | `--mode reclassify` | Re-run Claude Vision on `unclassified/` clips |

---

### Keyword / product

| Flag | Type | Description |
|---|---|---|
| `--keyword <name>` | string | Exact name of the product folder under `SSD_BASE_PATH` |
| `--product <name>` | string | Alias for `--keyword` |

---

### Extract flags

| Flag | Description |
|---|---|
| `--voice` | Enable Demucs voice separation without prompting (~2-5 min/video on CPU) |
| `--skip-voice` | Skip Demucs entirely — clips keep original audio |
| `--skip-subtitle-check` | Skip Claude Vision subtitle/text check (saves API cost) |
| `--resume` | Always resume from checkpoint without prompting |
| `--no-resume` | Discard checkpoint and start fresh |

---

### Generate flags

| Flag | Type | Default | Description |
|---|---|---|---|
| `--nvideos <N>` / `--count <N>` | int | — | Number of videos to generate (1–50) |
| `--format <format>` | string | interactive | `random` · `benefits` · `emotion` · `hook_transition` · `plot_twist` |
| `--format-base <base>` | string | `benefits` | Base for `hook_transition`: `benefits` or `emotion` |
| `--cta-type <type>` | string | interactive | `trigger` · `generic` |
| `--cta-trigger <word>` | string | `INFO` | Trigger word for `--cta-type trigger` |
| `--language <lang>` / `--lang` | string | `en` | Language for all generated text: `en` · `es` · `de` · `fr` · `it` |
| `--skip-script` | flag | — | Skip Claude script generation (no overlay text) |
| `--parallel <N>` | int | `1` | Parallel assembly workers (1–4) |

---

## Video formats

| Format | Clip sequence | Text overlays |
|---|---|---|
| **Benefits** | hook → problem → solution → demo/cta → unboxing | HOOK (30-50% Y) · PROBLEM · SOLUTION · PROOF · CTA |
| **Emotion** | hook → filler clips | HOOK · full-screen EMOTION text (58px, centered 50%) · CTA |
| **Hook Transition** | intro clip → Benefits or Emotion layout | as above for chosen base |
| **Plot Twist** | creator clip (3 s) → product clips | PLOT_HOOK during creator · PLOT_REVEAL after |

All overlay text: **TikTok Sans Bold**, white fill, 6px black stroke, no background pill.  
Font fallback chain (macOS): TikTok Sans Bold → TikTok Sans Black → Roboto Light → PIL default.  
Font fallback chain (Windows): TikTok Sans Bold → TikTok Sans Black → Roboto Light → Impact → Segoe UI Bold → Arial Bold → PIL default.  
Hook Y position: randomised per video between **30% – 50%** of frame height.

---

## Non-interactive examples

### macOS

```bash
# Extract — fast pass, no voice separation, fresh start
python main.py --extract --product "hanging portable fan" --no-resume --skip-voice --skip-subtitle-check

# Extract — full quality, resume if interrupted
python main.py --extract --product "hanging portable fan" --resume --voice

# Generate — 6 random-format videos, Spanish, generic CTA
python main.py --generate --product "hanging portable fan" --nvideos 6 --format random --language es --cta-type generic

# Generate — 12 Benefits videos, trigger CTA, 3 parallel workers
python main.py --generate --product "hanging portable fan" --nvideos 12 --format benefits --cta-type trigger --cta-trigger LINK --parallel 3

# Full pipeline — extract then generate 10 videos
python main.py --extract --product "hanging portable fan" --no-resume --skip-voice \
  && python main.py --generate --product "hanging portable fan" --nvideos 10 --format random --cta-type generic --parallel 3

# Queue two products back to back
python main.py --generate --product "pattern handbag purse" --nvideos 8 --format benefits --parallel 2 \
  && python main.py --generate --product "magnetic phone holder" --nvideos 8 --format random --parallel 2
```

### Windows (PowerShell)

```powershell
# Extract — fast pass
python main.py --extract --product "hanging portable fan" --no-resume --skip-voice --skip-subtitle-check

# Generate — 12 Benefits videos, trigger CTA, 3 parallel workers
python main.py --generate --product "hanging portable fan" --nvideos 12 --format benefits --cta-type trigger --cta-trigger LINK --parallel 3

# Full pipeline (use ^ for line continuation in PowerShell)
python main.py --extract --product "hanging portable fan" --no-resume --skip-voice `
  && python main.py --generate --product "hanging portable fan" --nvideos 10 --format random --cta-type generic --parallel 3
```

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

The file `<keyword>/.asset_history.json` tracks the last **10** uses of each asset type.  
If a candidate asset is in the history it is skipped; the next candidate is tried instead.

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

**Script-level anti-repetition:** before each Claude API call, the last 5 texts per field
are injected into the prompt as an `AVOID REPETITION` section, so generated texts stay
fresh even within the same `--nvideos` batch.

Delete `.asset_history.json` to reset all tracking for a keyword.

---

## Music setup

Drop royalty-free MP3 files into the folder set by `MUSIC_FOLDER` in `.env`.  
Sub-folders by genre or mood are supported — all MP3s are discovered recursively.

Music selection is seeded per `keyword + format + variation` for deterministic but wide rotation.  
The anti-repetition system prevents the same track from repeating within the last 10 videos.

**Google Drive source (optional):**  
Set `MUSIC_SOURCE=drive` in `.env` and fill in `DRIVE_MUSIC_FOLDER_ID`.  
Files are cached locally in `MUSIC_CACHE_DIR`.

---

## Configuration (.env)

Copy `.env.example` to `.env` and edit.

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Claude API key (required for scripts + Vision) |
| `SSD_BASE_PATH` | platform default | Root folder for all keyword folders |
| `SSD_DRIVE` | `D` | Windows only — drive letter (ignored on macOS) |
| `MUSIC_FOLDER` | platform default | Folder with royalty-free MP3s |
| `MUSIC_SOURCE` | `local` | `local` or `drive` (Google Drive) |
| `WHISPER_MODEL` | `base` | `tiny` · `base` · `small` · `medium` · `large` |
| `DEMUCS_MODEL` | `htdemucs` | Demucs model — `htdemucs` is lightest on CPU |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Claude model for scripts and Vision |
| `TARGET_DURATION` | `30` | Target video length in seconds |
| `MIN_CLIP_DURATION` | `2` | Clips shorter than this (seconds) are discarded |
| `MAX_CLIP_DURATION` | `6` | Clips longer than this are trimmed |
| `SCENE_THRESHOLD` | `0.4` | FFmpeg scene-cut sensitivity (0 = all cuts, 1 = none) |
| `RAM_SAFETY_GB` | `2` | Pause if free RAM drops below this (GB) |
| `MUSIC_VOLUME` | `0.03` | Background music volume (0.0 – 1.0) |
| `HD_FILTER` | `true` | FFmpeg sharpening pass after export (`true` / `false`) |

**HD filter** (`HD_FILTER=true`): runs `unsharp=5:5:1.0` + `eq=contrast=1.05:saturation=1.1`
via FFmpeg on the final render, then replaces the file in-place.  
On macOS the filter uses the **VideoToolbox** hardware encoder (Apple Silicon) for speed.  
Set `HD_FILTER=false` to skip it entirely.

**Platform defaults** (when `SSD_BASE_PATH` / `MUSIC_FOLDER` are not set in `.env`):

| Variable | Windows default | macOS default |
|---|---|---|
| `SSD_BASE_PATH` | `D:\Products Reels` | `~/Products Reels` |
| `MUSIC_FOLDER` | `D:\Music for Shorts` | `~/Music for Shorts` |
| `HOOK_TRANSITIONS_DIR` | `D:\Hook Transitions` | `~/Hook Transitions` |
| `CREATORS_DIR` | `D:\Creators` | `~/Creators` |
