"""History deletion is explicit; closing a tab remains presentation-only."""
from __future__ import annotations

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, OptionList, Static

from tui.options import KeyboardOptionList
from tui.pickers import SelectionPicker


class SessionDeleteConfirm(ModalScreen[bool]):
    BINDINGS = [Binding('escape', 'cancel', priority=True),
                Binding('left', 'select_cancel', priority=True),
                Binding('right', 'select_delete', priority=True),
                Binding('enter', 'choose', priority=True)]

    def compose(self):
        with Vertical(id='session-delete-confirm'):
            yield Static(self.app.tr('会话和聊天记录将永久删除，无法恢复。'), id='session-delete-warning', markup=False)
            with Horizontal(classes='session-delete-actions'):
                yield Button(self.app.tr('取消'), id='session-delete-cancel', compact=True)
                yield Button(self.app.tr('删除'), id='session-delete-yes', compact=True)

    def on_mount(self):
        self.action_select_cancel()

    def action_select_cancel(self):
        self.query_one('#session-delete-cancel', Button).focus()

    def action_select_delete(self):
        self.query_one('#session-delete-yes', Button).focus()

    def action_choose(self):
        self.dismiss(self.focused is self.query_one('#session-delete-yes', Button))

    def action_cancel(self):
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        self.dismiss(event.button.id == 'session-delete-yes')


class SessionHistoryPicker(SelectionPicker):
    BINDINGS = [Binding('delete', 'delete_selected', priority=True),
                Binding('backspace', 'delete_selected', priority=True)]

    def compose(self):
        with Vertical(id='selection-picker'):
            yield Label(self.title_text)
            yield KeyboardOptionList(id='picker-options')
            yield Static('', id='picker-empty', markup=False)
            yield Static(self.app.tr('↑↓ 选择  Enter 打开  Delete 删除  Esc 返回'), classes='picker-hint')

    def action_delete_selected(self):
        index = self.query_one(OptionList).highlighted
        if index is None or not self.filtered:
            return
        row = self.filtered[index]
        def confirmed(yes):
            if yes and self.is_mounted:
                self.dismiss({**row, '_delete': True})
        self.app.push_screen(SessionDeleteConfirm(), confirmed)
