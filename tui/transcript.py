"""Sticky transcript viewport and coalesced, incremental Markdown output."""
from textual.containers import VerticalScroll
from textual.widgets import Markdown


class Transcript(VerticalScroll):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.scroll_revision = 0

    def release_anchor(self) -> None:
        self.scroll_revision += 1
        super().release_anchor()

    def on_mount(self) -> None:
        # Textual releases this anchor on user scrolling and resumes at the end.
        # Layout-driven growth (Markdown, status changes, resize) keeps following.
        self.anchor()


class LiveMarkdown(Markdown):
    def __init__(self) -> None:
        super().__init__('', id='live-output')
        self._desired = ''
        self._rendering = False
        self.display = False

    def on_mount(self) -> None:
        self.set_interval(1 / 30, self._flush)

    def set_text(self, text: str) -> None:
        self._desired = text
        self.display = bool(text)

    async def _flush(self) -> None:
        if self._rendering or self._desired == self.source:
            return
        self._rendering = True
        text = self._desired
        try:
            if self.source and text.startswith(self.source):
                await self.append(text[len(self.source):])
            else:
                await self.update(text)
        finally:
            self._rendering = False
