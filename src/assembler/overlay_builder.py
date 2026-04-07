"""
Text overlay builder for TikTok/Reels-style text on video clips.
Uses Pillow — no ImageMagick required.

Overlay types:
  hook     — white pill background, black bold text, TOP position (8% from top)
  benefit  — white text with black stroke, no background, variable position (25-55%)
  cta      — black pill background, white bold text, CENTER-BOTTOM (72%)

Public API (two levels):
  render_hook_rgba(text)            -> np.ndarray | None   (RGBA canvas)
  render_benefit_rgba(text, y_frac) -> np.ndarray | None
  render_cta_rgba(text)             -> np.ndarray | None

  add_hook_overlay(clip, text)      -> clip  (legacy wrappers, still usable)
  add_benefit_overlay(clip, text, y_frac=Y_BENEFIT)
  add_cta_overlay(clip, text)
"""
import numpy as np
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import ImageClip, CompositeVideoClip
from rich.console import Console

console = Console()

# ── layout constants ─────────────────────────────────────────────────────────
FONT_PATH  = Path(r"C:\Windows\Fonts\arialbd.ttf")
FONT_SIZE  = 52
WRAP_WIDTH = 900          # max text width in px before wrapping
FRAME_W    = 1080
FRAME_H    = 1920

Y_HOOK    = 0.08          # 8% from top  (fixed)
Y_BENEFIT = 0.40          # 40% default — callers pass varied value
Y_CTA     = 0.72          # 72% from top (fixed)

PILL_PAD_X = 36
PILL_PAD_Y = 20
PILL_RADIUS = 30
STROKE_W    = 3

ALPHA_HOOK = 230          # ~90% opacity
ALPHA_CTA  = 210          # ~82% opacity
# ─────────────────────────────────────────────────────────────────────────────


def _load_font(size: int = FONT_SIZE) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(FONT_PATH), size)
    except OSError:
        console.print("  [yellow]Arial Bold not found — using PIL default font[/yellow]")
        return ImageFont.load_default()


def _wrap_text(text: str, font, max_width: int = WRAP_WIDTH) -> list[str]:
    """Split text into lines that fit within max_width pixels."""
    dummy = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        if dummy.textlength(test, font=font) <= max_width:
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
    return bbox[3] - bbox[1] + 8   # +8 line gap


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
    total_text_h = len(lines) * lh - 8  # remove trailing gap

    max_line_w = max(int(draw.textlength(l, font=font)) for l in lines)
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
        lw = int(draw.textlength(line, font=font))
        draw.text(((FRAME_W - lw) // 2, ty), line, font=font, fill=text_color)
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
        lw = int(draw.textlength(line, font=font))
        draw.text(
            ((FRAME_W - lw) // 2, ty), line,
            font=font,
            fill=text_color,
            stroke_width=STROKE_W,
            stroke_fill=stroke_color,
        )
        ty += lh

    return np.array(canvas)


def _apply(clip, rgba: np.ndarray):
    """Composite a static RGBA overlay on top of clip for its full duration."""
    overlay = ImageClip(rgba, ismask=False).set_duration(clip.duration)
    return CompositeVideoClip([clip, overlay], size=(FRAME_W, FRAME_H))


# ── render_*_rgba — return only the RGBA array (no clip wrapping) ─────────────

def render_hook_rgba(hook_text: str) -> "np.ndarray | None":
    """White pill, black text — top of frame (8%). Returns RGBA array or None."""
    if not hook_text:
        return None
    try:
        font = _load_font()
        lines = _wrap_text(hook_text, font)
        return _render_pill(
            lines, font,
            text_color=(0, 0, 0, 255),
            bg_color=(255, 255, 255, ALPHA_HOOK),
            y_frac=Y_HOOK,
        )
    except Exception as e:
        console.print(f"  [yellow]Hook render failed: {e}[/yellow]")
        return None


def render_benefit_rgba(benefit_text: str, y_frac: float = Y_BENEFIT) -> "np.ndarray | None":
    """
    White text + black stroke, no background.
    y_frac: vertical position (0.0 = top, 1.0 = bottom).
    Safe mobile range: 0.25 – 0.55 (avoids caption/description overlap in basso).
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


def render_cta_rgba(cta_text: str) -> "np.ndarray | None":
    """Black pill, white text — center-bottom (72%). Returns RGBA array or None."""
    if not cta_text:
        return None
    try:
        font = _load_font()
        lines = _wrap_text(cta_text, font)
        return _render_pill(
            lines, font,
            text_color=(255, 255, 255, 255),
            bg_color=(0, 0, 0, ALPHA_CTA),
            y_frac=Y_CTA,
        )
    except Exception as e:
        console.print(f"  [yellow]CTA render failed: {e}[/yellow]")
        return None


# ── legacy clip-level wrappers (kept for compatibility) ───────────────────────

def add_hook_overlay(clip, hook_text: str):
    """White pill, black text — first clip, top of frame."""
    rgba = render_hook_rgba(hook_text)
    return _apply(clip, rgba) if rgba is not None else clip


def add_benefit_overlay(clip, benefit_text: str, y_frac: float = Y_BENEFIT):
    """White text + black stroke, no bg — middle clips, variable position."""
    rgba = render_benefit_rgba(benefit_text, y_frac)
    return _apply(clip, rgba) if rgba is not None else clip


def add_cta_overlay(clip, cta_text: str):
    """Black pill, white text — last clip, center-bottom."""
    rgba = render_cta_rgba(cta_text)
    return _apply(clip, rgba) if rgba is not None else clip
