"""
Intentional misspelling for overlay texts.

Applies one human-looking typo to a single overlay field per video
to stimulate engagement (comments correcting the mistake).

Priority: hook → problem → solution → proof → emotion
Never touches: cta, caption, plot_hook, plot_reveal
"""
import json
import random
import re
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRIORITY_FIELDS = ["hook", "problem", "solution", "proof", "emotion"]

WORD_BLACKLIST = {
    "ce", "fcc", "rohs", "link", "bio", "url",
    "tiktok", "instagram", "reels", "shorts",
}

MISPELLING_METHODS = ["duplicate", "missing", "transpose", "adjacent_key"]

# QWERTY adjacency map (lowercase) — includes common accented chars for es/de/it/fr
QWERTY_ADJACENT: dict[str, str] = {
    "a": "qwsz", "b": "vghn", "c": "xdfv", "d": "serfcx",
    "e": "wsdr", "f": "drtgvc", "g": "ftyhbv", "h": "gyujnb",
    "i": "ujko", "j": "huikmnb", "k": "jiol", "l": "kop",
    "m": "njk", "n": "bhjm", "o": "iklp", "p": "ol",
    "q": "wa",  "r": "edft",  "s": "awedxz", "t": "rfgy",
    "u": "yhji", "v": "cfgb", "w": "qase", "x": "zsdc",
    "y": "tghu", "z": "asx",
    # Spanish / Italian / French accented vowels map to neighbours of their base
    "á": "qwsz", "é": "wsdr", "í": "ujko", "ó": "iklp", "ú": "yhji",
    "à": "qwsz", "è": "wsdr", "ì": "ujko", "ò": "iklp", "ù": "yhji",
    "ä": "qwsz", "ö": "iklp", "ü": "yhji",
    "ñ": "bhjm",
}

LOG_FILENAME = "mispelling_log.json"
RECENT_WINDOW = 5   # skip a word if it was mispelled in the last N entries


# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------

def _load_log(keyword_path: Path) -> list[dict]:
    log_file = keyword_path / LOG_FILENAME
    if not log_file.exists():
        return []
    try:
        return json.loads(log_file.read_text(encoding="utf-8"))
    except Exception:
        return []


def _append_log(keyword_path: Path, entry: dict) -> None:
    log = _load_log(keyword_path)
    log.append(entry)
    try:
        (keyword_path / LOG_FILENAME).write_text(
            json.dumps(log, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Mispelling engine
# ---------------------------------------------------------------------------

def _do_mispell(word: str, method: str) -> str:
    """Apply one mispelling method to a word. Returns the mispelled word."""
    if len(word) < 4:
        return word  # safety guard

    if method == "duplicate":
        # Duplicate a random interior letter (not first, not last)
        pos = random.randint(1, len(word) - 2)
        return word[:pos + 1] + word[pos] + word[pos + 1:]

    elif method == "missing":
        # Remove a random interior letter
        pos = random.randint(1, len(word) - 2)
        return word[:pos] + word[pos + 1:]

    elif method == "transpose":
        # Swap two adjacent letters (random interior position)
        pos = random.randint(1, len(word) - 2)
        chars = list(word)
        chars[pos], chars[pos + 1] = chars[pos + 1], chars[pos]
        return "".join(chars)

    elif method == "adjacent_key":
        # Replace a random interior letter with an adjacent QWERTY key
        interior = list(range(1, len(word) - 1))
        random.shuffle(interior)
        for pos in interior:
            ch = word[pos].lower()
            neighbours = QWERTY_ADJACENT.get(ch, "")
            if neighbours:
                replacement = random.choice(neighbours)
                # Preserve original case
                if word[pos].isupper():
                    replacement = replacement.upper()
                return word[:pos] + replacement + word[pos + 1:]
        # No adjacent key found — fall back to transpose
        return _do_mispell(word, "transpose")

    return word


def _select_target_word(
    text: str,
    recent_words: set[str],
) -> tuple[str, int] | None:
    """
    Pick an eligible word from text.
    Returns (word, index_in_word_list) or None if nothing is eligible.

    Eligibility rules:
    - Length > 3 characters
    - Does NOT start with uppercase (proxy for proper nouns)
    - Does NOT contain digits
    - NOT in WORD_BLACKLIST
    - NOT in recent_words (anti-repetition)
    """
    words = text.split()
    eligible: list[tuple[str, int]] = []

    for idx, word in enumerate(words):
        # Strip any stray punctuation for the check
        clean = re.sub(r"[^\w]", "", word)
        if not clean:
            continue
        if len(clean) <= 3:
            continue
        if clean[0].isupper():
            continue
        if any(c.isdigit() for c in clean):
            continue
        if clean.lower() in WORD_BLACKLIST:
            continue
        if clean.lower() in recent_words:
            continue
        eligible.append((word, idx))

    if not eligible:
        return None
    return random.choice(eligible)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_mispelling(
    script: dict,
    keyword_path: Path,
    language: str = "en",
) -> tuple[dict, dict | None]:
    """
    Apply one intentional misspelling to the highest-priority overlay field.

    Returns:
        (modified_script, log_entry)  — log_entry is None if nothing was changed.
    """
    # Load recent log to build the anti-repetition set
    log = _load_log(keyword_path)
    recent_words: set[str] = {
        entry["original_word"].lower()
        for entry in log[-RECENT_WINDOW:]
        if "original_word" in entry
    }

    # Try each field in priority order
    for field in PRIORITY_FIELDS:
        text = script.get(field)
        if not text or text.strip().upper() == "SKIP":
            continue

        result = _select_target_word(text, recent_words)
        if result is None:
            continue

        original_word, word_idx = result
        clean_word = re.sub(r"[^\w]", "", original_word)

        # Pick a random method
        method = random.choice(MISPELLING_METHODS)
        mispelled_clean = _do_mispell(clean_word, method)

        # Safety: if nothing changed (e.g., adjacent_key fell through), try once more
        if mispelled_clean == clean_word:
            method = "transpose"
            mispelled_clean = _do_mispell(clean_word, method)

        # Preserve any non-word chars around the word (shouldn't exist after _clean_text,
        # but defensive programming)
        mispelled_word = original_word.replace(clean_word, mispelled_clean, 1)

        # Replace in text (first occurrence only)
        words = text.split()
        words[word_idx] = mispelled_word
        mispelled_text = " ".join(words)

        # Update script
        modified_script = dict(script)
        modified_script[field] = mispelled_text

        # Build log entry
        entry = {
            "date":           datetime.now().isoformat(timespec="seconds"),
            "language":       language,
            "field":          field,
            "original_word":  clean_word,
            "mispelled_word": mispelled_clean,
            "method":         method,
            "original_text":  text,
            "mispelled_text": mispelled_text,
        }
        _append_log(keyword_path, entry)

        return modified_script, entry

    # No eligible word found in any field
    return script, None
