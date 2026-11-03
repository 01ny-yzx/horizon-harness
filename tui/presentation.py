"""Small semantic accents; terminal-default surfaces and prose stay neutral."""
from rich.style import Style
from rich.text import Text
from textual.content import Content
from textual.widgets import Collapsible, Static

_LIGHT = {'info': '#245caa', 'success': '#246b3b', 'warning': '#8a5a00', 'error': '#b42318'}
_DARK = {'info': '#7aa2f7', 'success': '#8bc891', 'warning': '#e4b665', 'error': '#ef8b83'}


def semantic_color(role: str, dark: bool) -> str:
    return (_DARK if dark else _LIGHT)[role]


def phase_role(text: str) -> str:
    if any(word in text for word in ('失败', '出错', '错误')):
        return 'error'
    if any(word in text for word in ('压缩', '中断', '重新连接', '连接已断开')):
        return 'warning'
    if text in {'已完成', '已同步会话'}:
        return 'success'
    return 'info'


class ToolRow(Collapsible):
    def __init__(self, name: str, status: str, detail: str, *, dark: bool):
        self.tool_name, self.tool_status = name, status
        super().__init__(Static(Text(detail, style='dim')), title=self._label(dark), classes='tool')

    def _label(self, dark: bool) -> Text:
        symbol, role = {'completed': ('✓', 'success'), 'failed': ('×', 'error'),
                        'error': ('×', 'error'),
                        'running': ('⠿', 'info')}.get(self.tool_status, ('·', 'info'))
        return Text.assemble((symbol + ' ', Style(color=semantic_color(role, dark), bold=True)),
                             (self.tool_name, Style(bold=True)))

    def set_dark(self, dark: bool) -> None:
        label = self._label(dark)
        # Rich Text / Content equality ignores style-only changes.
        self.set_reactive(Collapsible.title, label)
        if self.is_mounted:
            title = self.query_one('CollapsibleTitle')
            title.set_reactive(type(title).label, Content.from_text(label))
            title.mutate_reactive(type(title).label)
