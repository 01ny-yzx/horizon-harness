"""Private local model connections; public views never contain credentials."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from urllib.parse import urlsplit

from config.settings import settings
from core.platform_support import private_file_mode
from core.path_grounding import build_path_context
from providers.factory import config_from_settings
from providers.model_catalog import SNAPSHOT_PATH, resolve_provider_id

PRESETS_PATH = Path(__file__).resolve().parents[1] / 'providers' / 'connection_presets.json'
_lock = threading.RLock()


class ModelConnections:
    def __init__(self, root: Path | None = None, runtime_settings=settings):
        self.root = Path(root) if root is not None else build_path_context().user_data_root
        self.path = self.root / 'connections' / 'providers.json'
        self.settings = runtime_settings
        self.presets = {p['id']: p for p in json.loads(PRESETS_PATH.read_text(encoding="utf-8"))['presets']}

    @property
    def default_id(self) -> str:
        return resolve_provider_id(self.settings.llm_base_url) or 'environment'

    def _saved(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, OSError):
            raise ValueError('本地模型配置无法读取，请检查用户配置文件') from None

    def connections(self) -> dict:
        result = {key: {**p, 'api_key': '', 'models': []} for key, p in self.presets.items()}
        key = self.default_id
        result[key] = {**result.get(key, {}), 'id': key,
                       'name': result.get(key, {}).get('name', '当前服务'),
                       'base_url': self.settings.llm_base_url,
                       'api_key': self.settings.llm_api_key,
                       'models': [self.settings.llm_model], 'environment': True,
                       'model_contexts': {self.settings.llm_model: self.settings.llm_capabilities_json['max_context_tokens']}
                       if isinstance(self.settings.llm_capabilities_json, dict) and self.settings.llm_capabilities_json.get('max_context_tokens') else {}}
        for key, value in self._saved().items():
            result[key] = {**result.get(key, {}), **value, 'id': key}
        return result

    def public(self, key: str) -> dict:
        item = self.connections().get(key)
        if item is None:
            raise ValueError('未知模型服务')
        public = {name: item.get(name, '') for name in ('id', 'name', 'base_url')}
        parsed = urlsplit(public['base_url'])
        public['base_url'] = parsed._replace(netloc=parsed.netloc.rsplit('@', 1)[-1], query='', fragment='').geturl()
        return public | {
            'custom': key not in self.presets and key != self.default_id,
            'model_contexts': item.get('model_contexts', {}),
            'configured': bool(item.get('api_key')) or self.settings.llm_provider == 'mock' and key == self.default_id}

    def runtime_settings(self, key: str, model: str):
        item = self.connections().get(key)
        if not item or not model.strip():
            raise ValueError('模型服务或模型名称无效')
        if not self.public(key)['configured']:
            raise ValueError('请先配置这个服务的 API Key')
        overrides = {'llm_model': model}
        if not item.get('environment'):
            overrides.update(llm_provider='openai_compatible', llm_extra_body=None)
        overrides.update(llm_base_url=item['base_url'], llm_api_key=item.get('api_key', ''))
        if not item.get('environment') or model != self.settings.llm_model:
            overrides.update(llm_capabilities_json=None, llm_capability_preset='')
        context = item.get('model_contexts', {}).get(model)
        if context:
            overrides['llm_capabilities_json'] = {'max_context_tokens': context}
        return replace(self.settings, **overrides)

    def validate_choice(self, key: str, model: str) -> None:
        if len(model) > 300 or not model.strip() or any(ord(c) < 32 for c in model):
            raise ValueError('模型名称无效')
        try:
            config_from_settings(self.runtime_settings(key, model))
        except ValueError:
            raise
        except Exception:
            raise ValueError('无法确定模型上下文上限，请在服务配置中填写上下文 Token 上限') from None

    def save(self, key: str, *, name: str, base_url: str, api_key: str,
             model: str = '', context_tokens: int | None = None) -> dict:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', key):
            raise ValueError('服务标识须以字母或数字开头，可包含点、下划线和短横线')
        parsed = urlsplit(base_url.strip())
        if parsed.scheme not in {'https', 'http'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('请填写有效的 OpenAI 兼容接口地址，地址不能包含密钥或查询参数')
        if context_tokens is not None and not 1024 <= context_tokens <= 100_000_000:
            raise ValueError('上下文 Token 上限须在 1024 到 100000000 之间')
        with _lock:
            old = self.connections().get(key, {})
            secret = api_key.strip() or old.get('api_key', '')
            if not secret or any(c in secret for c in '\r\n'):
                raise ValueError('请填写 API Key')
            # A blank password can reuse a key only for the same endpoint.
            if not api_key.strip() and old.get('base_url', '').rstrip('/') != base_url.strip().rstrip('/'):
                raise ValueError('更改接口地址时请重新填写 API Key')
            saved = self._saved()
            models = list(dict.fromkeys([*old.get('models', []), *([model.strip()] if model.strip() else [])]))
            contexts = dict(old.get('model_contexts', {}))
            if model.strip() and context_tokens is not None:
                contexts[model.strip()] = context_tokens
            saved[key] = {'name': name.strip()[:80] or key, 'base_url': base_url.strip().rstrip('/'),
                          'api_key': secret, 'models': models,
                          'model_contexts': contexts, 'environment': False}
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.path.parent, 0o700)
            fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix='.providers-')
            try:
                private_file_mode(fd)
                with os.fdopen(fd, 'w', encoding='utf-8') as file:
                    json.dump(saved, file, ensure_ascii=False)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temp, self.path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        return self.public(key)

    def catalog(self) -> list[dict]:
        connections = self.connections()
        configured = {key: bool(item.get('api_key')) or self.settings.llm_provider == 'mock' and key == self.default_id
                      for key, item in connections.items()}
        result = {}
        for item in json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))['models']:
            key = item['provider_id']
            if key not in connections or item.get('status') == 'deprecated':
                continue
            model = item['model_id']
            result[(key, model)] = {'id': f'{key}:{model}', 'connection_id': key, 'model': model,
                                    'provider': connections[key]['name'], 'configured': configured[key]}
        for key, item in connections.items():
            for model in item.get('models', []):
                result[(key, model)] = {'id': f'{key}:{model}', 'connection_id': key, 'model': model,
                                        'provider': item['name'], 'configured': configured[key]}
        return sorted(result.values(), key=lambda p: (not p['configured'], p['provider'].lower(), p['model'].lower()))
