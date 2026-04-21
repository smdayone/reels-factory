"""
Asset history tracker — prevents repeating the same music, video clips,
captions, and in-video texts across consecutive video generations.

Each asset type keeps a FIFO queue of the last MAX_HISTORY (5) used values.
When a new asset is about to be used:
  - If NOT in the history  → use it, add to history, save
  - If already in history  → skip, try the next candidate

File location: D:\\Products Reels\\[keyword]\\.asset_history.json
"""
import json
from pathlib import Path
from rich.console import Console

console = Console()

MAX_HISTORY = 10

_ASSET_TYPES = [
    "music",
    "hook_clip",
    "creator_clip",
    "caption",
    "hook_text",
    "problem_text",
    "solution_text",
    "proof_text",
    "emotion_text",
    "plot_hook_text",
    "plot_reveal_text",
]


class AssetHistory:
    """FIFO history of recently used assets per keyword."""

    def __init__(self, base_path: Path):
        """
        base_path: keyword base directory (e.g. D:\\Products Reels\\pattern handbag purse)
        """
        self._file = base_path / ".asset_history.json"
        self._data: dict[str, list[str]] = {k: [] for k in _ASSET_TYPES}

    def load(self) -> None:
        """Load history from disk. Missing file → empty history (no error)."""
        if not self._file.exists():
            return
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
            for key in _ASSET_TYPES:
                if key in raw and isinstance(raw[key], list):
                    # Keep only MAX_HISTORY items; clip older entries if file was edited manually
                    self._data[key] = raw[key][-MAX_HISTORY:]
        except Exception as e:
            console.print(f"  [yellow]asset_history load failed: {e} — starting fresh[/yellow]")
            self._data = {k: [] for k in _ASSET_TYPES}

    def save(self) -> None:
        """Persist history to disk."""
        try:
            self._file.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            console.print(f"  [yellow]asset_history save failed: {e}[/yellow]")

    def is_recent(self, asset_type: str, value: str) -> bool:
        """Return True if value appears in the last MAX_HISTORY entries for this type."""
        if asset_type not in self._data:
            return False
        return value in self._data[asset_type]

    def add(self, asset_type: str, value: str) -> None:
        """Add value to history (FIFO — oldest entry removed when > MAX_HISTORY)."""
        if asset_type not in self._data:
            return
        queue = self._data[asset_type]
        if value in queue:
            # Already present: move to end (most recent)
            queue.remove(value)
        queue.append(value)
        if len(queue) > MAX_HISTORY:
            queue.pop(0)
