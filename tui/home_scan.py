"""Fixed ASCII geometry with a quiet, terminal-friendly looping light band."""
from functools import lru_cache
from math import exp
import os
import time

from rich.style import Style
from rich.text import Text
from textual.widgets import Static

PERIOD = 7.2


@lru_cache(maxsize=256)
def _scan_style(rgb: tuple[int, int, int], level: int) -> Style:
    dark = sum(c * w for c, w in zip(rgb, (.2126, .7152, .0722))) < 128
    ink = (228, 230, 230) if dark else (48, 61, 69)
    strength = .48 + .52 * level / 16
    color = '#' + ''.join(f'{round(bg + (fg - bg) * strength):02x}' for bg, fg in zip(rgb, ink))
    return Style(color=color, bold=True)


def scan_frame(art: str, elapsed: float, rgb: tuple[int, int, int] | None = None) -> Text:
    lines = art.split('\n')
    width = max(map(len, lines), default=0)
    position = (elapsed % PERIOD) / PERIOD * (width + 24) - 12
    frame = Text(art)
    offset = 0
    for line in lines:
        for x, char in enumerate(line):
            if char != ' ':
                level = round(16 * exp(-((x - position) / 8) ** 2))
                # Until the terminal reports its color, preserve its own foreground.
                style = _scan_style(rgb, level) if rgb is not None else Style(bold=True, dim=level < 7)
                frame.stylize(style, offset + x, offset + x + 1)
        offset += len(line) + 1
    return frame


class HomeScan(Static):
    def __init__(self, art: str):
        super().__init__(Text(art), id='home-title-art')
        self.art = art
        self.rgb: tuple[int, int, int] | None = None
        self.elapsed = 0.0
        self._last_tick = time.monotonic()
        self.enabled = os.getenv('NO_ANIMATION', '').lower() not in {'1', 'true', 'yes'}

    def set_background(self, rgb: tuple[int, int, int]) -> None:
        self.rgb = rgb
        if self.enabled:
            self.update(scan_frame(self.art, self.elapsed, self.rgb))

    def tick(self, visible: bool) -> None:
        now = time.monotonic()
        delta = min(now - self._last_tick, .25)
        self._last_tick = now
        if self.enabled and visible:
            self.elapsed = (self.elapsed + delta) % PERIOD
            self.update(scan_frame(self.art, self.elapsed, self.rgb))
