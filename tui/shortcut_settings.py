"""Keyboard-first shortcut settings and isolated key capture."""
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option
from tui.options import KeyboardOptionList
from tui.keybindings import DEFAULTS, LABELS, KeyConflict, display_key


class KeyCapture(ModalScreen):
    def __init__(self, action):
        super().__init__()
        self.action = action

    def compose(self) -> ComposeResult:
        with Vertical(id='key-capture'):
            yield Static(self.app.tr(LABELS[self.action]), classes='shortcut-title', markup=False)
            yield Static(self.app.tr('请按下新的组合键，Esc 取消'), markup=False)
            yield Static('', id='key-capture-error', markup=False)

    def on_key(self, event: events.Key):
        event.stop()
        event.prevent_default()
        if event.key == 'escape':
            self.dismiss(None)
            return
        try:
            self.app.shortcuts.assign(self.action, event.key)
            self.app.apply_shortcuts()
        except KeyConflict as exc:
            message = self.app.tr('此快捷键已用于：{action}', action=self.app.tr(LABELS[exc.owner])) if exc.owner else self.app.tr('此按键用于编辑、系统操作或无法识别，请换一个组合键')
            self.query_one('#key-capture-error', Static).update(message)
            return
        except OSError:
            self.query_one('#key-capture-error', Static).update(self.app.tr('无法保存快捷键，原设置未改变'))
            return
        self.dismiss(True)


class ShortcutSettings(ModalScreen):
    BINDINGS = [('escape', 'dismiss', '')]

    def compose(self) -> ComposeResult:
        with Vertical(id='shortcut-settings'):
            yield Static(self.app.tr('快捷键设置'), classes='shortcut-title', markup=False)
            yield KeyboardOptionList(id='shortcut-options')
            yield Static(self.app.tr('↑↓ 选择  Enter 修改  Esc 返回'), markup=False)

    def on_mount(self):
        self.show_options()
        self.query_one(OptionList).focus()

    def show_options(self):
        options = self.query_one(OptionList)
        selected = options.highlighted or 0
        options.clear_options()
        for action in DEFAULTS:
            label = self.app.tr(LABELS[action])
            key = display_key(self.app.shortcuts.key(action))
            row = Text(label)
            row.append(' ' * max(2, 32 - row.cell_len))
            row.append(key, style='bold')
            options.add_option(Option(row, id=action))
        options.add_option(Option(self.app.tr('恢复默认快捷键'), id='reset'))
        options.highlighted = min(selected, len(DEFAULTS))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        event.stop()
        action = event.option.id
        if action == 'reset':
            try:
                self.app.shortcuts.reset()
                self.app.apply_shortcuts()
                self.show_options()
            except OSError:
                self.app.notify('无法保存快捷键，原设置未改变', severity='error')
        else:
            self.app.push_screen(KeyCapture(action), self.captured)

    def captured(self, result):
        self.show_options()
        self.query_one(OptionList).focus()
