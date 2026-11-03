"""Presentation-only browser-style tabs; closing never deletes a Session."""
from dataclasses import dataclass, field

from rich.text import Text
from rich.style import Style
from textual.app import ComposeResult
from textual.containers import Horizontal, HorizontalScroll
from textual.message import Message
from textual.widgets import Button



@dataclass
class SessionTabState:
    title: str
    draft: str = ''
    cursor: tuple[int, int] = (0, 0)
    scroll_y: float | None = None
    event_cursor: int | None = None
    live: dict[str, str] = field(default_factory=dict)


class SessionTab(Horizontal):
    def __init__(self, session_id: str, title: str, *, active: bool, running: bool, dark: bool):
        super().__init__(classes='session-tab active-tab' if active else 'session-tab')
        self.session_id = session_id
        self.title = title
        self.running = running
        self.active, self.dark = active, dark

    def title_text(self, prefix: str = '') -> Text:
        style = Style(bold=True, dim=False) if self.active else Style()
        text = Text(prefix + self.title, style=style)
        text.truncate(22, overflow='ellipsis')
        return text

    def compose(self) -> ComposeResult:
        text = self.title_text('◌ ' if self.running else '')
        yield Button(text, classes='tab-label', compact=True)
        yield Button('×', classes='tab-close', compact=True)

    def on_enter(self) -> None:
        # Enter bubbles from the title/close buttons as well as the tab itself.
        self.add_class('hovered-tab')

    def on_leave(self) -> None:
        self.call_after_refresh(self._update_hover)

    def _update_hover(self) -> None:
        if self.is_mounted:
            self.set_class(self.region.contains(*self.app.mouse_position), 'hovered-tab')


class SessionTabBar(HorizontalScroll):
    class Selected(Message):
        def __init__(self, session_id: str, *, close: bool = False):
            super().__init__()
            self.session_id, self.close = session_id, close

    def __init__(self):
        super().__init__(id='session-tabs')
        self._signature = None

    async def show_tabs(self, states: dict[str, SessionTabState], active: str | None,
                        running: set[str], *, dark: bool = True) -> None:
        signature = (tuple((sid, state.title) for sid, state in states.items()),
                     active, frozenset(running), dark)
        if signature == self._signature:
            return
        self._signature = signature
        await self.remove_children()
        for sid, state in list(states.items()):
            tab = SessionTab(sid, state.title, active=sid == active, running=sid in running, dark=dark)
            await self.mount(tab)
            if sid == active:
                tab.call_after_refresh(tab.scroll_visible, animate=False, immediate=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if isinstance(event.button.parent, SessionTab):
            event.stop()
            self.post_message(self.Selected(event.button.parent.session_id,
                                            close=event.button.has_class('tab-close')))

    def animate_running(self, frame: int) -> None:
        for tab in self.query(SessionTab):
            if tab.running:
                text = tab.title_text('⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'[frame % 10] + ' ')
                # A timer can run while show_tabs is mounting/removing children.
                for label in tab.query('.tab-label'):
                    label.label = text
