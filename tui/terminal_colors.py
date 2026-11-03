"""Read the terminal's actual background without treating OSC replies as input.

Textual 6.12 does not decode OSC color reports. The Linux/macOS driver below
keeps its usual key/mouse parser and adds a small color-report filter ahead of it.
"""
from __future__ import annotations

import re
import time

from textual._xterm_parser import XTermParser
from textual.message import Message


class TerminalBackground(Message):
    def __init__(self, rgb: tuple[int, int, int]) -> None:
        super().__init__()
        self.rgb = rgb


def windows_background(kernel32=None) -> tuple[int, int, int] | None:
    """Read the original console palette before the UI changes its attributes."""
    import ctypes as c

    class Coord(c.Structure):
        _fields_ = [('x', c.c_int16), ('y', c.c_int16)]

    class Rect(c.Structure):
        _fields_ = [(name, c.c_int16) for name in ('left', 'top', 'right', 'bottom')]

    class BufferInfo(c.Structure):
        _fields_ = [('cbSize', c.c_uint32), ('dwSize', Coord),
                    ('dwCursorPosition', Coord), ('wAttributes', c.c_uint16),
                    ('srWindow', Rect), ('dwMaximumWindowSize', Coord),
                    ('wPopupAttributes', c.c_uint16), ('bFullscreenSupported', c.c_int32),
                    ('ColorTable', c.c_uint32 * 16)]

    try:
        if kernel32 is None:
            kernel32 = c.WinDLL('kernel32', use_last_error=True)
        get_handle = kernel32.GetStdHandle
        get_handle.argtypes = [c.c_uint32]
        get_handle.restype = c.c_void_p
        read = kernel32.GetConsoleScreenBufferInfoEx
        read.argtypes = [c.c_void_p, c.POINTER(BufferInfo)]
        read.restype = c.c_int32
        handle = get_handle(0xFFFFFFF5)  # STD_OUTPUT_HANDLE (-11)
        if handle is None or handle == c.c_void_p(-1).value:
            return None
        info = BufferInfo()
        info.cbSize = c.sizeof(info)
        if not read(handle, c.byref(info)):
            return None
        color = info.ColorTable[(info.wAttributes >> 4) & 15]
        return (color & 255, (color >> 8) & 255, (color >> 16) & 255)
    except (AttributeError, OSError):
        return None


def windows_color_driver(base):
    class WindowsColorDriver(base):
        def __init__(self, *args, **kwargs):
            # Keep the captured default; later reads may see a highlighted cell.
            self.background_rgb = windows_background() or (0, 0, 0)
            super().__init__(*args, **kwargs)

    return WindowsColorDriver


def message_background(rgb: tuple[int, int, int]) -> str:
    """A distinct highlight relative to the terminal, independent of ANSI slots."""
    brightness = sum(c * weight for c, weight in zip(rgb, (.2126, .7152, .0722)))
    target = 0 if brightness >= 128 else 255
    mixed = [round(c + (target - c) * .12) for c in rgb]
    return '#' + ''.join(f'{c:02x}' for c in mixed)


class ColorReportParser:
    """Preserve normal input, including fragmented keys and bracketed paste."""
    PREFIX = '\x1b]11;'
    PASTE_START = '\x1b[200~'
    PASTE_END = '\x1b[201~'
    REPORT = re.compile(r'\x1b\]11;rgb:([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})/([0-9a-fA-F]{1,4})(?:\x07|\x1b\\)\Z')

    def __init__(self, debug: bool = False):
        self.parser = XTermParser(debug)
        self.pending = ''
        self.since = 0.0
        self.pasting = False

    def feed(self, data: str):
        for char in data:
            self.pending += char
            self.since = time.monotonic()
            if self.pending == self.PASTE_START and not self.pasting:
                self.pasting = True
            elif self.pending == self.PASTE_END:
                self.pasting = False
            else:
                prefixes = (self.PASTE_END,) if self.pasting else (self.PREFIX, self.PASTE_START)
                if any(prefix.startswith(self.pending) for prefix in prefixes):
                    continue
                if not self.pasting and self.pending.startswith(self.PREFIX):
                    if self.pending.endswith(('\x07', '\x1b\\')):
                        match = self.REPORT.fullmatch(self.pending)
                        if match:
                            rgb = tuple(round(int(c, 16) * 255 / (16 ** len(c) - 1)) for c in match.groups())
                            yield TerminalBackground(rgb)
                        self.pending = ''
                        continue
                    if len(self.pending) <= 128:
                        continue
                    self.pending = ''
                    continue
            yield from self.parser.feed(self.pending)
            self.pending = ''

    def tick(self):
        if self.pending and time.monotonic() - self.since > .1:
            # Never let partial terminal replies become user prompt text.
            if not self.pending.startswith(self.PREFIX):
                yield from self.parser.feed(self.pending)
            self.pending = ''
        yield from self.parser.tick()


def color_driver(base):
    """Adapt native drivers; leave headless and custom drivers intact."""
    import os
    if os.name == 'nt':
        from textual.drivers.windows_driver import WindowsDriver
        return windows_color_driver(base) if base is WindowsDriver else base
    if os.name != 'posix':
        return base
    from textual.drivers.linux_driver import LinuxDriver
    if base is not LinuxDriver:
        return base

    class TerminalColorDriver(LinuxDriver):
        reports_background = True

        def run_input_thread(self) -> None:
            import os
            import selectors
            from codecs import getincrementaldecoder
            from textual._parser import ParseError

            parser = ColorReportParser(self._debug)
            decode = getincrementaldecoder('utf-8')().decode
            with selectors.SelectSelector() as selector:
                selector.register(self.fileno, selectors.EVENT_READ)
                try:
                    while not self.exit_event.is_set():
                        if selector.select(.05):
                            data = os.read(self.fileno, 4096)
                            if not data:
                                break
                            for event in parser.feed(decode(data)):
                                self.process_message(event)
                        for event in parser.tick():
                            self.process_message(event)
                finally:
                    try:
                        list(parser.parser.feed(''))
                    except (EOFError, ParseError):
                        pass

    return TerminalColorDriver
