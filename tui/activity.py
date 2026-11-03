"""Solid tidal bars and foreground-only shimmer for activity labels."""
from functools import lru_cache
from math import exp, pi, sin

from rich.cells import cell_len
from rich.style import Style
from rich.text import Text

from tui.presentation import semantic_color

WAVE_PERIOD = 2.28
SHIMMER_PERIOD = 2.56
_BARS = ('▁', '▂', '▃', '▄')


@lru_cache(maxsize=512)
def _ink(role: str, dark: bool, rgb: tuple[int, int, int] | None, level: int,
         floor: float = .8) -> Style:
    color = semantic_color(role, dark)
    if rgb is None:
        return Style(color=color, dim=level < 8)
    foreground = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    strength = floor + (1 - floor) * level / 16
    mixed = '#' + ''.join(f'{round(bg + (fg - bg) * strength):02x}'
                          for bg, fg in zip(rgb, foreground))
    return Style(color=mixed)


def activity_frame(label: str, elapsed: float, *, busy: bool, dark: bool,
                   rgb: tuple[int, int, int] | None = None, role: str = 'info',
                   animate: bool = True) -> Text:
    """Keep label characters/geometry intact; vary wave glyphs and ink only."""
    if not busy or not label:
        return Text(label)
    elapsed = max(0., elapsed) if animate else 0.
    frame = Text()
    for i in range(5):
        wave = (1 + sin(2 * pi * elapsed / WAVE_PERIOD - i * .8)) / 2
        height = min(3, int(wave * 4))
        frame.append(_BARS[height], _ink(role, dark, rgb, round(16 * wave), .45))
    frame.append(' ')
    width = cell_len(label)
    position = elapsed % SHIMMER_PERIOD / SHIMMER_PERIOD * (width + 6) - 3
    column = 0
    for char in label:
        char_width = cell_len(char)
        level = round(16 * exp(-((column + char_width / 2 - position) / 2) ** 2))
        frame.append(char, _ink(role, dark, rgb, level))
        column += char_width
    for i in range(3):
        pulse = (1 + sin(elapsed * 2 * pi / 2.28 - i * 1.2)) / 2
        frame.append(' ')
        frame.append('·', _ink(role, dark, rgb, round(16 * pulse), .25))
    return frame
