"""Keyboard pickers and a write-only connection form."""
from __future__ import annotations

from rich.text import Text
from tui.options import KeyboardOptionList
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option


class SelectionPicker(ModalScreen[dict | None]):
    BINDINGS = [Binding('escape', 'dismiss(None)', priority=True),
                Binding('up', 'move(-1)', priority=True), Binding('down', 'move(1)', priority=True),
                Binding('home', 'first', priority=True), Binding('end', 'last', priority=True),
                Binding('enter', 'choose', priority=True), Binding('ctrl+e', 'configure', priority=True)]

    def __init__(self, title: str, rows: list[dict], *, models: bool = False):
        super().__init__()
        self.title_text, self.rows, self.models = title, rows, models
        self.filtered: list[dict] = []

    def compose(self):
        with Vertical(id='selection-picker'):
            yield Label(self.title_text)
            yield KeyboardOptionList(id='picker-options')
            yield Static('', id='picker-empty', markup=False)
            yield Static(self.app.tr('↑↓ 选择  Enter 确认  Esc 返回') + (self.app.tr('  Ctrl+E 配置') if self.models else ''), classes='picker-hint')

    def on_mount(self):
        self._populate()
        self._resize_panel()
        self.query_one(OptionList).focus()

    def on_resize(self):
        if self.is_mounted:
            self._resize_panel()

    def _resize_panel(self):
        panel = self.query_one('#selection-picker')
        panel.styles.height = min(len(self.rows) + 9, max(9, int(self.size.height * .8)))

    def _populate(self):
        self.filtered = self.rows
        options = self.query_one(OptionList)
        options.clear_options()
        options.add_options([Option(Text(row['label'])) for row in self.filtered])
        options.highlighted = next((i for i, row in enumerate(self.filtered) if row.get('current')), 0) if self.filtered else None
        options.display = bool(self.filtered)
        self.query_one('#picker-empty', Static).update(self.app.tr('没有匹配项') if not self.filtered else '')
        self.query_one('#picker-empty').display = not self.filtered

    def action_move(self, offset: int):
        if self.filtered:
            options = self.query_one(OptionList)
            options.highlighted = ((options.highlighted or 0) + offset) % len(self.filtered)

    def action_first(self):
        if self.filtered:
            self.query_one(OptionList).highlighted = 0

    def action_last(self):
        if self.filtered:
            self.query_one(OptionList).highlighted = len(self.filtered) - 1

    def action_choose(self):
        index = self.query_one(OptionList).highlighted
        if index is not None and self.filtered:
            self.dismiss(self.filtered[index])

    def action_configure(self):
        index = self.query_one(OptionList).highlighted
        if self.models and index is not None and self.filtered:
            self.dismiss({**self.filtered[index], '_configure': True})

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        if event.option_index < len(self.filtered):
            self.dismiss(self.filtered[event.option_index])


class ConnectionForm(ModalScreen[dict | None]):
    BINDINGS = [('escape', 'dismiss(None)', '返回')]

    def __init__(self, client, row: dict, connection: dict):
        super().__init__()
        self.client, self.row, self.connection = client, row, connection
        self.saving = False
        self.custom = row.get('id') == '__custom__' or bool(connection.get('custom'))

    def compose(self):
        with Vertical(id='connection-form', classes='custom' if self.custom else 'simple'):
            yield Label(self.app.tr('配置自定义服务') if self.custom else self.connection.get('name', self.row.get('provider', self.app.tr('模型服务'))))
            if not self.custom:
                yield Static(self.row.get('model', ''), classes='picker-hint', markup=False)
            with VerticalScroll(id='connection-fields'):
                if self.custom:
                    yield Label(self.app.tr('服务标识'))
                    yield Input(value=self.row.get('connection_id', ''), id='connection-id')
                    yield Label(self.app.tr('服务名称'))
                    yield Input(value=self.connection.get('name', ''), id='connection-name')
                    yield Label(self.app.tr('OpenAI 兼容 Base URL'))
                    yield Input(value=self.connection.get('base_url', ''), placeholder='https://…/v1', id='connection-url')
                yield Label(self.app.tr('API Key（留空保留已保存的密钥）') if self.connection.get('configured') else 'API Key')
                yield Input(password=True, id='connection-key')
                if self.custom:
                    yield Label(self.app.tr('模型 ID'))
                    yield Input(value=self.row.get('model', ''), id='connection-model')
                    yield Label(self.app.tr('上下文 Token 上限（目录已知模型可留空）'))
                    yield Input(value=str(self.connection.get('model_contexts', {}).get(self.row.get('model'), '') or ''),
                                placeholder=self.app.tr('例如 128000'), id='connection-context')
            yield Static('', id='connection-error', markup=False)
            if self.custom:
                with Horizontal(classes='form-actions'):
                    yield Button(self.app.tr('保存并使用'), id='connection-save')
                    yield Button(self.app.tr('取消'), id='connection-cancel')
            yield Static(self.app.tr('Enter 保存并使用  Esc 返回'), classes='picker-hint')

    def on_mount(self):
        self.query_one('#connection-key', Input).focus()

    async def on_input_submitted(self, event: Input.Submitted):
        await self._save()

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == 'connection-cancel':
            self.dismiss(None)
        elif event.button.id == 'connection-save':
            await self._save()

    async def _save(self):
        if self.saving:
            return
        if self.custom:
            values = {key: self.query_one('#connection-' + key, Input).value.strip()
                      for key in ('id', 'name', 'url', 'key', 'model', 'context')}
        else:
            values = {'id': self.row['connection_id'], 'name': self.connection.get('name', ''),
                      'url': self.connection['base_url'], 'model': self.row['model'],
                      'key': self.query_one('#connection-key', Input).value.strip(),
                      'context': str(self.connection.get('model_contexts', {}).get(self.row['model'], '') or '')}
        if not values['key'] and not self.connection.get('configured'):
            self.query_one('#connection-error', Static).update(self.app.tr('请填写 API Key'))
            return
        error = self.query_one('#connection-error', Static)
        if not values['id'] or not values['url'] or not values['model']:
            error.update(self.app.tr('请填写服务标识、接口地址和模型 ID'))
            return
        if values['context'] and not values['context'].isdigit():
            error.update(self.app.tr('上下文上限应为整数'))
            return
        self.saving = True
        if self.custom:
            self.query_one('#connection-save', Button).disabled = True
        try:
            await self.client.configure_connection(values['id'], {
                'name': values['name'], 'base_url': values['url'], 'api_key': values['key'],
                'model': values['model'], 'context_tokens': int(values['context']) if values['context'] else None})
            self.query_one('#connection-key', Input).value = ''
            if self.is_mounted:
                self.dismiss({'connection_id': values['id'], 'model': values['model']})
        except Exception as exc:
            if self.is_mounted:
                error.update(self.app.tr(str(exc)) if isinstance(exc, RuntimeError) else self.app.tr('保存失败，请检查本地服务'))
        finally:
            values['key'] = ''
            self.saving = False
            if self.is_mounted and self.custom:
                self.query_one('#connection-save', Button).disabled = False


class SearchConfigForm(ModalScreen[dict | None]):
    BINDINGS=[('escape','dismiss(None)','Back')]

    def __init__(self,client,status:dict):
        super().__init__()
        self.client,self.status=client,status
        self.saving=False

    def compose(self):
        with Vertical(id='connection-form',classes='simple'):
            yield Label(self.app.tr('联网搜索')+' · Tavily')
            yield Static(self.app.tr('搜索 Key 与模型 Key 相互独立'),classes='picker-hint')
            with VerticalScroll(id='connection-fields'):
                yield Label(self.app.tr('API Key（留空保留已保存的密钥）') if self.status.get('configured') else 'API Key')
                yield Input(password=True,id='search-key')
            yield Static(self.app.tr('搜索配置损坏，请重新填写 Key') if self.status.get('configuration_error') else '',id='connection-error',markup=False)
            yield Static(self.app.tr('Enter 保存并使用  Esc 返回'),classes='picker-hint')

    def on_mount(self):
        self.query_one(Input).focus()

    async def on_input_submitted(self,event:Input.Submitted):
        if self.saving:return
        key=self.query_one(Input).value
        if not key.strip() and not self.status.get('configured'):
            self.query_one('#connection-error',Static).update(self.app.tr('请填写搜索 API Key'))
            return
        self.saving=True
        try:
            status=await self.client.configure_search(key)
            self.query_one(Input).value=''
            if self.is_mounted:self.dismiss(status)
        except Exception as exc:
            if self.is_mounted:
                self.query_one('#connection-error',Static).update(self.app.tr(str(exc)) if isinstance(exc,RuntimeError) else self.app.tr('无法保存搜索配置'))
        finally:
            key=''
            self.saving=False
