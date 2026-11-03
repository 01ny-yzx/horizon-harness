"""Atomic UI layout storage. Contains identifiers only, never message contents."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid
from core.platform_support import process_file_lock, private_file_mode

_LOCK = threading.RLock()
_SESSION = re.compile(r'ses_[A-Za-z0-9_-]{1,128}\Z')


_process_lock = process_file_lock

class TabLayoutStore:
    def __init__(self, path: Path, *, scope: str = 'local'):
        self.path = Path(path)
        self.scope = hashlib.sha256(scope.encode()).hexdigest()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('layouts'), dict):
            raise ValueError('Invalid tab layout')
        return data['layouts']

    def load(self) -> dict | None:
        data = self._read().get(self.scope)
        if data is None:
            return None
        if not isinstance(data, dict) or not isinstance(data.get('sessions'), list):
            raise ValueError('Invalid tab layout')
        sessions = data['sessions']
        if any(not isinstance(sid, str) or not _SESSION.fullmatch(sid) for sid in sessions):
            raise ValueError('Invalid tab session ID')
        sessions = list(dict.fromkeys(sessions))
        active = data.get('active')
        if active is not None and active not in sessions:
            active = sessions[0] if sessions else None
        return {'sessions': sessions, 'active': active}

    def save(self, sessions: list[str], active: str | None) -> None:
        with _LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with _process_lock(self.path.with_suffix('.lock')):
                self._save_locked(sessions, active)

    def _save_locked(self, sessions: list[str], active: str | None) -> None:
        try:
            layouts = self._read()
        except ValueError:
            # Retain damaged data for inspection, then allow subsequent
            # close/reorder operations to persist normally again.
            backup = self.path.with_name(f'tabs.corrupt-{uuid.uuid4().hex}.json')
            os.replace(self.path, backup)
            layouts = {}
        data = {'sessions': list(sessions), 'active': active if active in sessions else None}
        if layouts.get(self.scope) == data:
            return
        layouts[self.scope] = data
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix='.tabs-')
        try:
            private_file_mode(fd)
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump({'version': 1, 'layouts': layouts}, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
