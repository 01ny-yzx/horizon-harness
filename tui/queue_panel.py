"""Compact durable queue rows and an explicit steer confirmation."""
import asyncio
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class PendingQueue(Vertical):
    class Action(Message):
        def __init__(self, action: str, message_id: str):
            super().__init__()
            self.action, self.message_id = action, message_id

    def __init__(self):
        super().__init__(id='pending-queue')
        self.rows: list[dict] = []
        self.selected: str | None = None
        self.background: str | None = None
        self.display = False
        self._signature = None
        self._render_lock = asyncio.Lock()

    async def show(self, rows: list[dict], tr, *, select: str | None = None):
        async with self._render_lock:
            await self._show(rows, tr, select=select)

    async def _show(self, rows, tr, *, select=None):
        self.rows = [r for r in rows if r.get('delivery') == 'queue']
        ids = [r['message_id'] for r in self.rows]
        self.selected = select if select in ids else self.selected if self.selected in ids else ids[-1] if ids else None
        signature = (repr(self.rows), self.selected, tr('引导'), tr('取回'), self.background)
        if signature == self._signature:
            return
        self._signature = signature
        self.display = bool(self.rows)
        with self.app.batch_update():
            await self.remove_children()
            for row in self.rows:
                message_id = row['message_id']
                line = Horizontal(classes='queue-row')
                line.message_id = message_id
                if self.background:
                    line.styles.background = self.background
                await self.mount(line)
                title = Text('↳ ' + ' '.join(row['prompt']['text'].split()),
                             style='bold' if message_id == self.selected else '', no_wrap=True, overflow='ellipsis')
                await line.mount(Static(title, classes='queue-title'),
                                 Button('↳ ' + tr('引导'), name='steer', classes='queue-guide', compact=True),
                                 Button('↶', name='recall', classes='queue-recall', tooltip=tr('取回'), compact=True),
                                 Button('×', name='remove', classes='queue-remove', tooltip=tr('移除'), compact=True))

    def move(self, direction: int):
        ids = [r['message_id'] for r in self.rows]
        if ids:
            self.selected = ids[(ids.index(self.selected) + direction) % len(ids)]

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.parent and hasattr(event.button.parent, 'message_id'):
            event.stop()
            self.selected = event.button.parent.message_id
            self.post_message(self.Action(event.button.name, self.selected))


class QueueConfirm(ModalScreen[bool]):
    BINDINGS = [('enter', 'confirm', 'Confirm'), ('escape', 'cancel', 'Cancel')]

    def __init__(self, text: str, tr):
        super().__init__()
        self.text, self.tr = text, tr

    def compose(self) -> ComposeResult:
        with Vertical(id='queue-confirm'):
            yield Static(self.tr('将这条消息插队？'), classes='queue-confirm-title', markup=False)
            yield Static(Text(self.text), id='queue-confirm-text')
            yield Static(self.tr('作为补充要求，优先交给当前任务处理。'), markup=False)
            with Horizontal():
                yield Button(self.tr('Enter 确认插队'), id='queue-confirm-yes', compact=True)
                yield Button(self.tr('Esc 继续排队'), id='queue-confirm-no', compact=True)

    def action_confirm(self):
        self.dismiss(True)

    def action_cancel(self):
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        self.dismiss(event.button.id == 'queue-confirm-yes')
