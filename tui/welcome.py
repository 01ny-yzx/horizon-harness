"""Finite character-grid sunrise animation, matching the approved prototype."""
from __future__ import annotations

import math
import os
import time

from rich.text import Text
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Static

DURATION = 4.2
_FONT = {
    'H': ('10001', '10001', '10001', '11111', '10001', '10001', '10001'),
    'O': ('01110', '10001', '10001', '10001', '10001', '10001', '01110'),
    'R': ('11110', '10001', '10001', '11110', '10100', '10010', '10001'),
    'I': ('11111', '00100', '00100', '00100', '00100', '00100', '11111'),
    'Z': ('11111', '00001', '00010', '00100', '01000', '10000', '11111'),
    'N': ('10001', '11001', '11001', '10101', '10011', '10011', '10001'),
}
_SMALL_FONT = {
    'H': ('101', '101', '111', '101', '101'),
    'O': ('111', '101', '101', '101', '111'),
    'R': ('110', '101', '110', '101', '101'),
    'I': ('111', '010', '010', '010', '111'),
    'Z': ('111', '001', '010', '100', '111'),
    'N': ('101', '111', '111', '111', '101'),
}
_LIGHT = ('#ce803e', '#cc7136', '#c76330', '#be502d', '#ae402d', '#b37455', '#bd8f75')
_DARK = ('#ffdaa0', '#ffca88', '#f9b373', '#f39a65', '#eb8059', '#c49176', '#a47e68')


def _pack(grid: list[list[int]]) -> list[str]:
    glyph = {(0, 0): ' ', (1, 0): '▀', (0, 1): '▄', (1, 1): '█'}
    return [''.join(glyph[(bit, grid[y + 1][x] if y + 1 < len(grid) else 0)]
                    for x, bit in enumerate(grid[y])) for y in range(0, len(grid), 2)]


def _sun(progress: float, compact: bool) -> list[str]:
    grid = [[0] * 41 for _ in range(14)]
    span = int(20 * min(1, progress * 3))
    for x in range(20 - span, 21 + span):
        grid[10][x] = 1
    cy = 20 - (1 - (1 - min(1, progress)) ** 3) * 10
    for y in range(10):
        for x in range(41):
            if abs(math.sqrt(((x - 20) / 1.9) ** 2 + (y - cy) ** 2) - 8.5) < .8:
                grid[y][x] = 1
    if progress > .65:
        reflection = int(13 * min(1, (progress - .65) / .35))
        for x in range(20 - reflection, 21 + reflection):
            grid[13][x] = 1
    if compact:
        small = [[grid[int(y * 14 / 10)][int(x * 41 / 27)] for x in range(27)] for y in range(10)]
        small[7] = [grid[10][int(x * 41 / 27)] for x in range(27)]
        small[9] = [grid[13][int(x * 41 / 27)] for x in range(27)]
        grid = small
    return _pack(grid)


def render_welcome(elapsed: float, *, width: int, height: int, dark: bool, enter_hint: str = 'Press Enter to start ↵') -> Text:
    """Return styled cells only: no terminal escape codes or fixed background."""
    p = min(1, max(0, elapsed / DURATION))
    compact = width < 41 or height < 16
    palette = _DARK if dark else _LIGHT
    rows: list[Text] = []
    if height >= 13:
        sun = _sun(min(1, p / .44), compact)
        for y, line in enumerate(sun):
            color_index = y if not compact else (int(y * 7 / 5) if y < 3 else 5 if y == 3 else 6)
            rows.append(Text(line, style=palette[color_index]))
        rows.append(Text(''))
    font = _SMALL_FONT if compact else _FONT
    glyph_height = len(font['H'])
    bits = []
    reveal = max(0, min(1, (p - .28) / .4))
    bitmap_width = 27 if compact else 41
    for y in range(glyph_height):
        line = '0'.join(font[c][y] for c in 'HORIZON')
        bits.append([int(bit) if x / bitmap_width <= reveal else 0 for x, bit in enumerate(line)])
    scan = int((p - .72) / .25 * (bitmap_width + 4)) - 2 if .72 < p < .97 else -9
    if width >= 27 and height >= 8:
        for line in _pack(bits):
            row = Text()
            for x, char in enumerate(line):
                row.append(char, style='dim' if abs(x - scan) <= 1 else '')
            rows.append(row)
    else:
        rows.append(Text('HORIZON' if p >= .68 else ''))
    if height >= 8:
        rows.extend((Text(''), Text('H A R N E S S' if p >= .68 else ''), Text('')))
    rows.append(Text(enter_hint, style='dim'))
    canvas_width = max(row.cell_len for row in rows)
    for row in rows:
        row.pad_left((canvas_width - row.cell_len) // 2)
        row.pad_right(canvas_width - row.cell_len)
    result = Text('\n').join(rows)
    result.no_wrap = True
    return result


def render_home_logo(*, width: int, height: int, dark: bool,
                     elapsed: float | None = None) -> Text:
    """Loop the welcome wordmark's final light pass; keep the sunrise complete."""
    frame_time = DURATION
    if elapsed is not None:
        phase = elapsed % 3.6
        if phase < DURATION * .25:
            # Reuse the exact final wordmark phase of the welcome animation.
            # The sun and letters are already complete at this point.
            frame_time = DURATION * .72 + phase
    brand = render_welcome(frame_time, width=width, height=height, dark=dark, enter_hint='')
    rows = brand.split('\n')
    for index, row in enumerate(rows):
        if row.plain.strip() == 'H A R N E S S':
            rows = rows[:index]
            break
    while rows and not rows[-1].plain.strip():
        rows.pop()
    result = Text('\n').join(rows)
    result.no_wrap = True
    return result


class WelcomeScreen(ModalScreen[None]):
    BINDINGS = [Binding('enter', 'enter_home', priority=True)]

    def __init__(self):
        super().__init__()
        self._art = Static('', id='welcome-art', markup=False)
        self._started = 0.0
        self._elapsed = 0.0
        self._dark = True
        self._animation_timer = None

    def compose(self):
        yield self._art

    def on_mount(self):
        self._started = time.monotonic()
        reduced = os.getenv('NO_ANIMATION', '').lower() not in {'', '0', 'false'} or os.getenv('TEXTUAL_ANIMATIONS') == 'none'
        self._elapsed = DURATION if reduced else 0
        self._draw()
        if not reduced:
            self._animation_timer = self.set_interval(1 / 16, self._advance)

    def _draw(self):
        self._art.update(render_welcome(self._elapsed, width=max(1, self.size.width - 4),
                                        height=max(1, self.size.height - 2), dark=self._dark,
                                        enter_hint=self.app.tr('Press Enter to start ↵')))

    def _advance(self):
        self._elapsed = min(DURATION, time.monotonic() - self._started)
        self._draw()
        if self._elapsed >= DURATION:
            self._stop_animation()

    def set_dark(self, dark: bool):
        self._dark = dark
        if self.is_mounted:
            self._draw()

    def on_resize(self):
        if self.is_mounted:
            self._draw()

    def _stop_animation(self):
        if self._animation_timer is not None:
            self._animation_timer.stop()
            self._animation_timer = None

    def action_enter_home(self):
        self._stop_animation()
        self.dismiss(None)

    def on_unmount(self):
        self._stop_animation()
