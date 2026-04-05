import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Base paths
BASE_DIR = Path(__file__).parent.parent
MUSIC_DIR = BASE_DIR / "music"

# SSD paths (Windows NTFS)
SSD_DRIVE = os.getenv("SSD_DRIVE", "D")
SSD_BASE = Path(f"{SSD_DRIVE}:\\Product Reels")

def get_keyword_paths(keyword: str) -> dict:
    """Return all paths for a given keyword."""
    base = SSD_BASE / keyword
    return {
        "base":          base,
        "raw":           base / "raw",
        "clips": {
            "hook":          base / "clips" / "hook",
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
    for clip_dir in paths["clips"].values():
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
