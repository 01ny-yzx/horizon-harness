"""One validated key map for runtime dispatch and local shortcut settings."""
import json
import os
from pathlib import Path
import re
import tempfile

DEFAULTS = {
    'send': 'enter', 'recall': 'ctrl+r', 'restore': 'ctrl+g', 'interrupt': 'escape',
    'new_session': 'ctrl+n', 'history': 'ctrl+p', 'previous_tab': 'ctrl+pageup',
    'next_tab': 'ctrl+pagedown', 'close_tab': 'ctrl+w', 'toggle_access': 'tab',
}
LABELS = {
    'send': '发送 / 排队', 'recall': '取回排队消息', 'restore': '恢复暂存草稿',
    'interrupt': '中断任务', 'new_session': '新建会话', 'history': '历史会话',
    'previous_tab': '上一个标签页', 'next_tab': '下一个标签页',
    'close_tab': '关闭标签页', 'toggle_access': '切换权限',
}
# Retain editor/navigation and fixed dialog/recovery controls. Known macOS
# Mission Control shortcuts are explicitly excluded; other OS mappings vary.
RESERVED = {
    'ctrl+q', 'f1', 'ctrl+c', 'ctrl+v', 'ctrl+x', 'ctrl+z', 'ctrl+y',
    'ctrl+w', 'ctrl+s', 'ctrl+i', 'ctrl+m', 'ctrl+h', 'ctrl+a', 'ctrl+e', 'ctrl+j', 'ctrl+k', 'ctrl+u', 'ctrl+d', 'ctrl+f',
    'ctrl+left', 'ctrl+right', 'ctrl+up', 'ctrl+down', 'shift+enter',
    'up', 'down', 'left', 'right', 'home', 'end', 'pageup', 'pagedown',
    'f6', 'f7', 'backspace', 'delete', 'shift+tab',
}


class KeyConflict(ValueError):
    def __init__(self, owner=None):
        super().__init__('Shortcut already in use' if owner else 'Reserved or unsupported shortcut')
        self.owner = owner


def display_key(key):
    return key.replace('escape', 'Esc').replace('pageup', 'PageUp').replace('pagedown', 'PageDown').title().replace('Pageup', 'PageUp').replace('Pagedown', 'PageDown')


class KeyBindings:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self.values = dict(DEFAULTS)
        if self.path:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                overrides = data['bindings']
                candidate = {**DEFAULTS, **{a: k for a, k in overrides.items() if a in DEFAULTS}}
                for action, key in candidate.items():
                    self.validate(action, key, candidate)
                self.values = candidate
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                pass

    def key(self, action):
        return self.values[action]

    def validate(self, action, key, values=None):
        if action not in DEFAULTS or not isinstance(key, str):
            raise KeyConflict()
        if key == DEFAULTS[action]:
            pass
        elif key in RESERVED or key in {'enter', 'escape', 'tab'}:
            raise KeyConflict()
        elif not re.fullmatch(r'(ctrl\+|alt\+|ctrl\+alt\+)[a-z0-9]|f([2-5]|[89]|1[0-2])', key):
            raise KeyConflict()
        for other, value in (self.values if values is None else values).items():
            if other != action and key == value:
                raise KeyConflict(other)

    def assign(self, action, key):
        self.validate(action, key)
        candidate = {**self.values, action: key}
        self._save(candidate)
        self.values = candidate

    def reset(self):
        self._save(DEFAULTS)
        self.values = dict(DEFAULTS)

    def _save(self, values):
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix='.keybindings-')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump({'version': 1, 'bindings': values}, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
