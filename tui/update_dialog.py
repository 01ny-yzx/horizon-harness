"""Keyboard-first update confirmation; installation starts after orderly shutdown."""
from rich.text import Text
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static, OptionList
from textual.widgets.option_list import Option
from tui.options import KeyboardOptionList


class UpdateDialog(ModalScreen[str | None]):
    BINDINGS = [Binding('escape','back',priority=True), Binding('up','move(-1)',priority=True),
                Binding('down','move(1)',priority=True), Binding('enter','choose',priority=True)]
    DEFAULT_CSS = '''
    UpdateDialog { align: center middle; background: ansi_default 30%; }
    #update-dialog { width: 68; max-width: 90%; height: auto; max-height: 85%; padding: 1 2; border: round ansi_default; background: ansi_default; color: ansi_default; }
    #update-title { text-style: bold; margin-bottom: 1; }
    #update-status { height: auto; margin-bottom: 1; }
    #update-actions, #update-actions:focus {
        height: auto; min-height: 1; max-height: 3;
        border: none; padding: 0; background: ansi_default; color: ansi_default;
    }
    #update-actions > .option-list--option-highlighted {
        color: ansi_default; background: ansi_default; text-style: reverse;
    }
    '''

    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.version = None
        self.actions = []
        self.checking = False

    def compose(self):
        with Vertical(id='update-dialog'):
            yield Static(self.app.tr('版本更新'),id='update-title')
            yield Static(self.app.tr('正在检查更新…'),id='update-status',markup=False)
            yield KeyboardOptionList(id='update-actions')
            yield Static(self.app.tr('↑↓ 选择  Enter 确认  Esc 返回'),classes='picker-hint')

    def on_mount(self):
        self.run_worker(self.check(), exclusive=True)

    def show_state(self, message, actions):
        self.query_one('#update-status', Static).update(message)
        self.actions = actions
        options = self.query_one(OptionList)
        options.clear_options()
        options.add_options([Option(Text(self.app.tr(label))) for _, label in actions])
        options.highlighted = 0 if actions else None
        if actions:
            options.focus()

    async def check(self):
        self.checking = True
        self.show_state(self.app.tr('正在检查更新…'), [])
        try:
            if self.manager.installation.method == 'source':
                self.show_state(self.app.tr('当前从源码运行，请通过 Git 更新源码'), [('back','返回')])
                return
            self.version = await self.manager.check(manual=True)
            if not self.version:
                self.show_state(self.app.tr('已是最新版本') + f' · v{self.manager.current}', [('back','返回')])
            elif self.manager.installation.method == 'unknown':
                self.show_state(f'v{self.manager.current} → v{self.version}\n\n' + self.app.tr('无法识别安装方式，请通过原安装渠道更新'), [('back','返回')])
            else:
                self.show_state(f'v{self.manager.current} → v{self.version} · {self.manager.installation.method}\n\n' + self.app.tr('确认后退出界面并关闭本地服务，在终端显示更新进度；成功后自动重启'),
                                [('install','更新并重启'),('skip','跳过此版本')])
        except Exception as error:
            self.show_state(self.app.tr(str(error)), [('retry','重试'),('back','返回')])
        finally:
            self.checking = False

    def action_move(self, offset):
        options = self.query_one(OptionList)
        if self.actions:
            options.highlighted = ((options.highlighted or 0) + offset) % len(self.actions)

    def action_choose(self):
        index = self.query_one(OptionList).highlighted
        if index is None or not self.actions:
            return
        action = self.actions[index][0]
        if action == 'retry':
            self.run_worker(self.check(), exclusive=True)
        elif action == 'skip':
            try:
                self.manager.skip(self.version)
            except OSError:
                self.show_state(self.app.tr('无法保存更新设置'), [('back','返回')])
                return
            self.dismiss(None)
        else:
            self.dismiss(self.version if action == 'install' else None)

    def on_option_list_option_selected(self, event):
        self.query_one(OptionList).highlighted = event.option_index
        self.action_choose()

    def action_back(self):
        self.dismiss(None)
