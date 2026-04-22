import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Base paths
BASE_DIR = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Platform-aware SSD / storage paths
#
# Priority for every path: explicit .env variable → platform default.
#
# Windows default : D:\Products Reels  (drive letter from SSD_DRIVE)
# macOS   default : ~/Products Reels   (inside user home — works with exFAT SSD
#                   mounted at /Volumes/<name> when SSD_BASE_PATH is set in .env)
# ---------------------------------------------------------------------------

def _win(rel: str) -> str:
    """Build a Windows path using SSD_DRIVE (ignored on macOS)."""
    drive = os.getenv("SSD_DRIVE", "D")
    return f"{drive}:\\{rel}"

def _default(env_win: str, mac_home_rel: str) -> str:
    """Return env_win on Windows, ~/mac_home_rel on macOS — both overridable via .env."""
    return env_win if sys.platform != "darwin" else str(Path.home() / mac_home_rel)

# Windows legacy: SSD_DRIVE lets you change D: → E: without touching other vars
SSD_DRIVE = os.getenv("SSD_DRIVE", "D")   # Windows only; ignored on macOS

SSD_BASE = Path(os.getenv(
    "SSD_BASE_PATH",
    _default(_win("Products Reels"), "Products Reels"),
))

# Music folder — scanned recursively, subfolders supported
MUSIC_DIR = Path(os.getenv(
    "MUSIC_FOLDER",
    _default(_win("Music for Shorts"), "Music for Shorts"),
))

# Hook Transition clips — video intro with original audio (format: Hook Transition)
HOOK_TRANSITIONS_DIR = Path(os.getenv(
    "HOOK_TRANSITIONS_DIR",
    _default("D:\\Hook Transitions", "Hook Transitions"),
))

# Creator clips — 3-second intros for Plot Twist format
CREATORS_DIR = Path(os.getenv(
    "CREATORS_DIR",
    _default("D:\\Creators", "Creators"),
))

def get_keyword_paths(keyword: str) -> dict:
    """Return all paths for a given keyword."""
    base = SSD_BASE / keyword
    return {
        "base":          base,
        "raw":           base / "raw",
        "clips": {
            "hook":          base / "clips" / "hook",
            "ai":            base / "clips" / "ai",          # AI-generated clips — loaded manually
            "problem":       base / "clips" / "problem",
            "solution":      base / "clips" / "solution",
            "demo":          base / "clips" / "demo",
            "unboxing":      base / "clips" / "unboxing",
            "cta":           base / "clips" / "cta",
            "unclassified":  base / "clips" / "unclassified",
        },
        "voice":         base / "voice",       # extracted vocals
        "music_stems":   base / "music_stems", # extracted background music
        "transcripts":   base / "transcripts",
        "output":        base / "output",
        "temp":          base / "temp",
    }

def create_keyword_dirs(keyword: str):
    """Create all directories for a keyword."""
    paths = get_keyword_paths(keyword)
    paths["raw"].mkdir(parents=True, exist_ok=True)
    for cat, clip_dir in paths["clips"].items():
        if cat == "ai":
            continue  # ai/ is loaded manually by the user — do not auto-create
        clip_dir.mkdir(parents=True, exist_ok=True)
    paths["voice"].mkdir(parents=True, exist_ok=True)
    paths["music_stems"].mkdir(parents=True, exist_ok=True)
    paths["transcripts"].mkdir(parents=True, exist_ok=True)
    paths["output"].mkdir(parents=True, exist_ok=True)
    paths["temp"].mkdir(parents=True, exist_ok=True)

# Model settings
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
DEMUCS_MODEL  = os.getenv("DEMUCS_MODEL", "htdemucs")
CLAUDE_MODEL  = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

# API
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Video settings
TARGET_DURATION  = int(os.getenv("TARGET_DURATION", 30))
MIN_CLIP_DURATION = float(os.getenv("MIN_CLIP_DURATION", 2))
SCENE_THRESHOLD  = float(os.getenv("SCENE_THRESHOLD", 0.4))

# System safety
RAM_SAFETY_GB = float(os.getenv("RAM_SAFETY_GB", 2))

# Max duration used from each individual clip (seconds).
# Long clips are trimmed to this value — keeps cuts fast and dynamic.
# A 36s clip with MAX_CLIP_DURATION=6 contributes only 6s to the total.
MAX_CLIP_DURATION = float(os.getenv("MAX_CLIP_DURATION", 6))

# Background music volume (0.0 – 1.0). Keep low so voice stays audible.
MUSIC_VOLUME = float(os.getenv("MUSIC_VOLUME", 0.07))

# Music source
MUSIC_SOURCE          = os.getenv("MUSIC_SOURCE", "local")   # "local" or "drive"
DRIVE_MUSIC_FOLDER_ID = os.getenv("DRIVE_MUSIC_FOLDER_ID", "")
MUSIC_CACHE_DIR       = Path(os.getenv("MUSIC_CACHE_DIR", str(BASE_DIR / "music_cache")))

# Text overlay minimum screen time (seconds).
# Texts shorter than this are extended to span subsequent clips.
TEXT_MIN_DURATION = float(os.getenv("TEXT_MIN_DURATION", 3.0))

# CTA defaults (overridden interactively in mode_generate)
CTA_MODE         = os.getenv("CTA_MODE", "generic")   # "comment" | "generic"
CTA_COMMENT_WORD = os.getenv("CTA_COMMENT_WORD", "INFO")

# Post-generation HD sharpening filter via FFmpeg (unsharp + contrast boost).
# Set HD_FILTER=false in .env to disable.
HD_FILTER_ENABLED = os.getenv("HD_FILTER", "true").lower() == "true"

# Clip category keywords for transcript-based classification
CLIP_KEYWORDS = {
    "hook": [
        "wait", "stop", "look", "check this", "you need", "game changer",
        "secret", "before you", "did you know", "this changes everything"
    ],
    "problem": [
        "problem", "issue", "struggle", "tired of", "frustrated", "annoying",
        "always", "never", "can't", "impossible", "hate when", "worst part"
    ],
    "solution": [
        "solution", "finally", "fixed", "solved", "works", "easy",
        "simple", "just", "all you need", "introducing", "meet"
    ],
    "demo": [
        "how to", "let me show", "watch this", "here's how", "step",
        "first", "then", "next", "now", "like this", "just like"
    ],
    "unboxing": [
        "unbox", "package", "arrived", "ordered", "shipping", "inside",
        "comes with", "what's in", "open"
    ],
    "cta": [
        "link in bio", "shop now", "get yours", "available", "order",
        "comment", "follow", "save this", "share", "tag someone"
    ],
}
