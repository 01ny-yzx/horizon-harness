"""Private write-only search credentials, separate from model API keys."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import threading
from core.platform_support import private_file_mode
from core.path_grounding import build_path_context

_LOCK=threading.RLock()

class SearchConnections:
    def __init__(self, root: Path | None=None):
        self.path=(root or build_path_context().user_data_root)/'connections'/'search.json'

    def _saved(self) -> dict:
        try:
            data=json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data,dict) or data.get('provider')!='tavily' or not isinstance(data.get('api_key'),str):
                raise ValueError()
            return data
        except FileNotFoundError:
            return {}
        except (OSError,ValueError):
            raise ValueError('搜索配置无法读取，请检查本地配置文件') from None

    def key(self) -> str:
        return self._saved().get('api_key','').strip() or os.getenv('TAVILY_API_KEY','').strip()

    def provider(self) -> str | None:
        return self._saved().get('provider')

    def configured(self) -> bool:
        return bool(self.key())

    def save(self, key: str):
        if any(c in key for c in '\r\n') or len(key)>4096:
            raise ValueError('搜索 Key 格式无效')
        with _LOCK:
            secret=key.strip() or self.key()
            if not secret:
                raise ValueError('请填写搜索 API Key')
            self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            os.chmod(self.path.parent,0o700)
            fd,temp=tempfile.mkstemp(dir=self.path.parent,prefix='.search-')
            try:
                private_file_mode(fd)
                with os.fdopen(fd,'w',encoding='utf-8') as output:
                    json.dump({'provider':'tavily','api_key':secret},output)
                    output.flush();os.fsync(output.fileno())
                os.replace(temp,self.path)
            finally:
                if os.path.exists(temp):os.unlink(temp)
