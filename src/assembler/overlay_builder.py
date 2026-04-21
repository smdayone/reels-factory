"""
Text overlay builder for TikTok/Reels-style text on video clips.
Uses Pillow — no ImageMagick required.

All overlay types use white text + black stroke, NO pill backgrounds.

Font: TikTok Sans Bold (OFL-1.1 — install from fonts.google.com/specimen/TikTok+Sans)
      → TikTok Sans Black → Roboto Light → Impact → Segoe UI Bold → Arial Bold → PIL default
Letter spacing: LETTER_SPACING pixels between characters (0 = native tracking for Bold)

Public API:
  render_hook_rgba(text, y_frac)    -> np.ndarray | None   (RGBA canvas, y_frac 0.30-0.50)
  render_benefit_rgba(text, y_frac) -> np.ndarray | None
  render_cta_rgba(text)             -> np.ndarray | None
  render_emotion_rgba(text)         -> np.ndarray | None
"""
import numpy as np
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import ImageClip, CompositeVideoClip
from rich.console import Console

console = Console()

# ── layout constants ─────────────────────────────────────────────────────────
_FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\TikTokSans-Bold.ttf"),          # TikTok Sans Bold — primary (OFL-1.1)
    Path(r"C:\Users\Windows20\AppData\Local\Microsoft\Windows\Fonts\TikTokSans-Bold.ttf"),
    Path(r"C:\Windows\Fonts\TikTokSans-Black.ttf"),          # TikTok Sans Black — heavier fallback
    Path(r"C:\Users\Windows20\AppData\Local\Microsoft\Windows\Fonts\TikTokSans-Black.ttf"),
    Path(r"C:\Windows\Fonts\Roboto-Light.ttf"),              # Roboto Light — previous primary
    Path(r"C:\Users\Windows20\AppData\Local\Microsoft\Windows\Fonts\Roboto-Light.ttf"),
    Path(r"C:\Windows\Fonts\impact.ttf"),                    # Windows built-in bold fallback
    Path(r"C:\Windows\Fonts\segoeuib.ttf"),                  # Segoe UI Bold
    Path(r"C:\Windows\Fonts\arialbd.ttf"),
]

FONT_SIZE         = 46
FONT_SIZE_EMOTION = 58
WRAP_WIDTH     = 900
FRAME_W        = 1080
FRAME_H        = 1920
LETTER_SPACING = 0                 # TikTok Sans Bold — native tracking, no adjustment needed

# Hook y_frac is randomised per video (0.30-0.50) — no fixed constant
Y_BENEFIT = 0.40
Y_CTA     = 0.72

STROKE_W  = 6                      # bold stroke — matches native TikTok caption style

# Reusable dummy draw for text measurement (no allocation per call)
_DUMMY_DRAW = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
# ─────────────────────────────────────────────────────────────────────────────


def _load_font(size: int = FONT_SIZE) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    console.print("  [yellow]No preferred font found — using PIL default[/yellow]")
    return ImageFont.load_default()


# ── typography helpers ────────────────────────────────────────────────────────

def _char_advance(ch: str, font) -> int:
    """Advance width of a single character (pixels)."""
    return int(_DUMMY_DRAW.textlength(ch, font=font))


def _text_width(text: str, font) -> int:
    """Total rendered width of text with LETTER_SPACING applied."""
    if not text:
        return 0
    w = sum(_char_advance(ch, font) for ch in text)
    w += LETTER_SPACING * (len(text) - 1)
    return w


def _wrap_text(text: str, font, max_width: int = WRAP_WIDTH) -> list[str]:
    """Split text into lines that fit within max_width pixels (respects LETTER_SPACING)."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        if _text_width(test, font) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [text]


def _line_height(font) -> int:
    bbox = font.getbbox("Ag")
    return bbox[3] - bbox[1] + 8


def _draw_line(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font,
    fill: tuple,
    stroke_width: int = 0,
    stroke_fill: tuple | None = None,
) -> None:
    """Draw text character by character applying LETTER_SPACING."""
    for ch in text:
        draw.text(
            (x, y), ch,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        x += _char_advance(ch, font) + LETTER_SPACING


# ── renderers ─────────────────────────────────────────────────────────────────

def _render_pill(
    lines: list[str],
    font,
    text_color: tuple,
    bg_color: tuple,
    y_frac: float,
) -> np.ndarray:
    """RGBA canvas with a rounded-rectangle pill containing centred text."""
    canvas = Image.new("RGBA", (FRAME_W, FRAME_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    lh = _line_height(font)
    total_text_h = len(lines) * lh - 8

    max_line_w = max(_text_width(l, font) for l in lines)
    pill_w = min(max_line_w + PILL_PAD_X * 2, FRAME_W - 40)
    pill_h = total_text_h + PILL_PAD_Y * 2

    x0 = (FRAME_W - pill_w) // 2
    y0 = int(y_frac * FRAME_H)

    draw.rounded_rectangle(
        [x0, y0, x0 + pill_w, y0 + pill_h],
        radius=PILL_RADIUS,
        fill=bg_color,
    )

    ty = y0 + PILL_PAD_Y
    for line in lines:
        lw = _text_width(line, font)
        tx = (FRAME_W - lw) // 2
        _draw_line(draw, tx, ty, line, font, fill=text_color)
        ty += lh

    return np.array(canvas)


def _render_stroke_text(
    lines: list[str],
    font,
    text_color: tuple,
    stroke_color: tuple,
    y_frac: float,
) -> np.ndarray:
    """RGBA canvas with white text + black stroke, no background."""
    canvas = Image.new("RGBA", (FRAME_W, FRAME_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    lh = _line_height(font)
    total_h = len(lines) * lh - 8
    ty = int(y_frac * FRAME_H) - total_h // 2

    for line in lines:
        lw = _text_width(line, font)
        tx = (FRAME_W - lw) // 2
        _draw_line(draw, tx, ty, line, font,
                   fill=text_color,
                   stroke_width=STROKE_W,
                   stroke_fill=stroke_color)
        ty += lh

    return np.array(canvas)


def _apply(clip, rgba: np.ndarray):
    """Composite a static RGBA overlay on top of clip for its full duration."""
    overlay = ImageClip(rgba, ismask=False).set_duration(clip.duration)
    return CompositeVideoClip([clip, overlay], size=(FRAME_W, FRAME_H))


# ── render_*_rgba — return only the RGBA array (no clip wrapping) ─────────────

def render_hook_rgba(hook_text: str, y_frac: float = 0.40) -> "np.ndarray | None":
    """White text + bold black stroke, no background — hook position is variable (0.30-0.50).
    y_frac: randomised per video by the assembler (seed variation+13).
    Returns RGBA array or None."""
    if not hook_text:
        return None
    try:
        font = _load_font()
        lines = _wrap_text(hook_text, font)
        return _render_stroke_text(
            lines, font,
            text_color=(255, 255, 255, 255),
            stroke_color=(0, 0, 0, 255),
            y_frac=y_frac,
        )
    except Exception as e:
        console.print(f"  [yellow]Hook render failed: {e}[/yellow]")
        return None


def render_benefit_rgba(benefit_text: str, y_frac: float = Y_BENEFIT) -> "np.ndarray | None":
    """
    White text + black stroke, no background.
    y_frac: vertical position (0.0 = top, 1.0 = bottom).
    Safe mobile range: 0.25 – 0.55 (avoids caption/description overlap).
    Returns RGBA array or None.
    """
    if not benefit_text:
        return None
    try:
        font = _load_font()
        lines = _wrap_text(benefit_text, font)
        return _render_stroke_text(
            lines, font,
            text_color=(255, 255, 255, 255),
            stroke_color=(0, 0, 0, 255),
            y_frac=y_frac,
        )
    except Exception as e:
        console.print(f"  [yellow]Benefit render failed: {e}[/yellow]")
        return None


def render_emotion_rgba(emotion_text: str) -> "np.ndarray | None":
    """White text + black stroke, centered (50%), 64px font — for Emotion format."""
    if not emotion_text:
        return None
    try:
        font = _load_font(FONT_SIZE_EMOTION)
        lines = _wrap_text(emotion_text, font)
        return _render_stroke_text(
            lines, font,
            text_color=(255, 255, 255, 255),
            stroke_color=(0, 0, 0, 255),
            y_frac=0.50,
        )
    except Exception as e:
        console.print(f"  [yellow]Emotion render failed: {e}[/yellow]")
        return None


def render_cta_rgba(cta_text: str) -> "np.ndarray | None":
    """White text + bold black stroke, no background — center-bottom (72%).
    Returns RGBA array or None."""
    if not cta_text:
        return None
    try:
        font = _load_font()
        lines = _wrap_text(cta_text, font)
        return _render_stroke_text(
            lines, font,
            text_color=(255, 255, 255, 255),
            stroke_color=(0, 0, 0, 255),
            y_frac=Y_CTA,
        )
    except Exception as e:
        console.print(f"  [yellow]CTA render failed: {e}[/yellow]")
        return None


# ── legacy clip-level wrappers (kept for compatibility) ───────────────────────

def add_hook_overlay(clip, hook_text: str, y_frac: float = 0.40):
    """White stroke text — first clip, variable position (0.30-0.50)."""
    rgba = render_hook_rgba(hook_text, y_frac=y_frac)
    return _apply(clip, rgba) if rgba is not None else clip


def add_benefit_overlay(clip, benefit_text: str, y_frac: float = Y_BENEFIT):
    """White text + black stroke, no bg — middle clips, variable position."""
    rgba = render_benefit_rgba(benefit_text, y_frac)
    return _apply(clip, rgba) if rgba is not None else clip


def add_cta_overlay(clip, cta_text: str):
    """Black pill, white text — last clip, center-bottom."""
    rgba = render_cta_rgba(cta_text)
    return _apply(clip, rgba) if rgba is not None else clip
