"""
Path handling utilities — cross-platform (Windows + macOS).
"""
from pathlib import Path


def to_native_path(path: Path) -> str:
    """Convert Path object to native OS string (handles both / and \\ automatically)."""
    return str(path)


def safe_filename(name: str) -> str:
    """Strip characters invalid in Windows or macOS filenames."""
    invalid = r'\/:*?"<>|'
    for ch in invalid:
        name = name.replace(ch, "_")
    return name.strip()


def ensure_dir(path: Path) -> Path:
    """Create directory if it doesn't exist, return path."""
    path.mkdir(parents=True, exist_ok=True)
    return path
