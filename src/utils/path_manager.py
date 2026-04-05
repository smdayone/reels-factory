"""
Windows path handling utilities for NTFS SSD.
"""
from pathlib import Path, PureWindowsPath


def to_windows_path(path: Path) -> str:
    """Convert Path object to Windows-style path string."""
    return str(PureWindowsPath(path))


def safe_filename(name: str) -> str:
    """Strip characters invalid in Windows filenames."""
    invalid = r'\/:*?"<>|'
    for ch in invalid:
        name = name.replace(ch, "_")
    return name.strip()


def ensure_dir(path: Path) -> Path:
    """Create directory if it doesn't exist, return path."""
    path.mkdir(parents=True, exist_ok=True)
    return path
