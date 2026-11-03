"""User-wide default identifiers, independent of session history and credentials."""
from __future__ import annotations

import json
import os
import tempfile

from core.platform_support import private_file_mode, process_file_lock


class GlobalModelSelection:
    def __init__(self, connections=None):
        from core.model_connections import ModelConnections
        self.connections = connections if connections is not None else ModelConnections()
        self.path = self.connections.root / 'connections' / 'default-model.json'

    def get(self) -> dict:
        if not self.path.exists():
            return {'connection_id': self.connections.default_id, 'model': self.connections.settings.llm_model}
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or any(not isinstance(data.get(k), str) or not data[k].strip()
                                                 for k in ('connection_id', 'model')):
                raise ValueError()
            return {k: data[k] for k in ('connection_id', 'model')}
        except (OSError, ValueError):
            raise ValueError('本地默认模型配置无法读取，请重新选择模型') from None

    def select(self, connection_id: str, model: str) -> dict:
        self.connections.validate_choice(connection_id, model)
        selected = {'connection_id': connection_id, 'model': model}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with process_file_lock(self.path.with_suffix('.lock')):
            fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix='.default-model-')
            try:
                private_file_mode(fd)
                with os.fdopen(fd, 'w', encoding='utf-8') as file:
                    json.dump(selected, file, ensure_ascii=False)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temp, self.path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        return selected
