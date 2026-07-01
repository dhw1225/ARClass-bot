"""Render `/guess` history cards as a PNG."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Optional

from guess_game import GuessSong


CANVAS_WIDTH = 1040
CARD_HEIGHT = 236
OUTER_MARGIN = 16
CARD_GAP = 12
GREEN = "#39b878"
GRAY = "#dddddf"
BACKGROUND = "#f5f5f5"
TEXT = "#111111"
WHITE = "#ffffff"
FONT_PATH = Path(__file__).parent / "assets" / "NotoSansCJKsc-Regular.otf"


def _format_number(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def comparison_marker(guessed: Optional[float], answer: Optional[float]) -> str:
    if guessed is None or answer is None or guessed == answer:
        return ""
    return "↑" if answer > guessed else "↓"


def _fit_font(draw, text: str, maximum_width: int, start_size: int, minimum: int = 15):
    from PIL import ImageFont

    for size in range(start_size, minimum - 1, -1):
        font = ImageFont.truetype(str(FONT_PATH), size)
        if draw.textbbox((0, 0), text, font=font)[2] <= maximum_width:
            return font
    return ImageFont.truetype(str(FONT_PATH), minimum)


def _draw_centered(draw, box, text: str, *, size: int, fill=TEXT, max_width=None):
    width = box[2] - box[0]
    font = _fit_font(draw, text, max_width or width - 12, size)
    bounds = draw.textbbox((0, 0), text, font=font)
    x = box[0] + (width - (bounds[2] - bounds[0])) / 2
    y = box[1] + (box[3] - box[1] - (bounds[3] - bounds[1])) / 2 - bounds[1]
    draw.text((x, y), text, font=font, fill=fill)


def _draw_tile(draw, x: int, y: int, width: int, label: str, value: str, matched: bool):
    box = (x, y, x + width, y + 72)
    draw.rounded_rectangle(box, radius=12, fill=GREEN if matched else GRAY)
    color = WHITE if matched else TEXT
    _draw_centered(draw, (x, y + 5, x + width, y + 34), label, size=20, fill=color)
    _draw_centered(draw, (x, y + 33, x + width, y + 68), value, size=18, fill=color)


def _field_values(song: GuessSong):
    extra_label = (
        f"{song.extra_difficulty} 定数"
        if song.extra_difficulty is not None
        else "BYD/ETR 定数"
    )
    return [
        ("曲包", song.pack, None),
        ("官方分组", song.pack_type, None),
        ("属性", song.side, None),
        ("最低 BPM", _format_number(song.bpm_min), song.bpm_min),
        ("最高 BPM", _format_number(song.bpm_max), song.bpm_max),
        ("PST 定数", _format_number(song.pst), song.pst),
        ("PRS 定数", _format_number(song.prs), song.prs),
        ("FTR 定数", _format_number(song.ftr), song.ftr),
        (extra_label, _format_number(song.extra_constant), song.extra_constant),
        ("实装年份", str(song.year), float(song.year)),
    ]


def render_guess_history(history: tuple[GuessSong, ...], answer: GuessSong) -> bytes:
    from PIL import Image, ImageDraw

    if not FONT_PATH.is_file():
        raise FileNotFoundError(f"guess image font not found: {FONT_PATH}")
    height = OUTER_MARGIN * 2 + len(history) * CARD_HEIGHT + max(0, len(history) - 1) * CARD_GAP
    image = Image.new("RGB", (CANVAS_WIDTH, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    answer_fields = _field_values(answer)
    first_widths = [240, 200, 160, 180, 180]
    second_widths = [150, 150, 150, 220, 190]

    for index, song in enumerate(history):
        top = OUTER_MARGIN + index * (CARD_HEIGHT + CARD_GAP)
        card = (OUTER_MARGIN, top, CANVAS_WIDTH - OUTER_MARGIN, top + CARD_HEIGHT)
        draw.rounded_rectangle(card, radius=14, fill=WHITE, outline="#e1e1e1", width=2)
        _draw_centered(draw, (32, top + 7, CANVAS_WIDTH - 32, top + 50), song.title, size=25)
        guessed_fields = _field_values(song)

        for row_index, widths in enumerate((first_widths, second_widths)):
            gap = 12
            row_width = sum(widths) + gap * (len(widths) - 1)
            x = (CANVAS_WIDTH - row_width) // 2
            y = top + 54 + row_index * 84
            for offset, width in enumerate(widths):
                field_index = row_index * 5 + offset
                label, display, numeric = guessed_fields[field_index]
                _, answer_display, answer_numeric = answer_fields[field_index]
                matched = numeric == answer_numeric if numeric is not None or answer_numeric is not None else display == answer_display
                if field_index == 8:
                    matched = (
                        song.extra_difficulty == answer.extra_difficulty
                        and numeric == answer_numeric
                    )
                if not matched and numeric is not None and answer_numeric is not None:
                    display = f"{display} {comparison_marker(numeric, answer_numeric)}"
                _draw_tile(draw, x, y, width, label, display, matched)
                x += width + gap

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
