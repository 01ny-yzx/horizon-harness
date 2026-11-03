"""Terminal-native selection contrast for keyboard-driven lists."""
from rich.style import Style
from textual.widgets import OptionList


class KeyboardOptionList(OptionList):
    def render_line(self, y: int):
        strip = super().render_line(y)
        # Textual 6's VisualStyle conversion drops CSS reverse. Apply it to the
        # actual Rich strip, including padding, without assuming terminal colors.
        line = self.scroll_offset.y + y
        if 0 <= line < len(self._lines) and self._lines[line][0] == self.highlighted:
            return strip.apply_style(Style(reverse=True))
        return strip
