"""
Structured logging per session.
Logs to logs/ directory with timestamp-based filenames.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

_session_id = datetime.now().strftime("%Y%m%d_%H%M%S")


def get_logger(name: str) -> logging.Logger:
    """Get a named logger that writes to both console and file."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    log_file = LOGS_DIR / f"{_session_id}_{name}.log"
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


def log_event(event: dict):
    """Append a structured JSON event to the session log."""
    event["timestamp"] = datetime.now().isoformat()
    log_path = LOGS_DIR / f"{_session_id}_events.json"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")
