"""Small local JSON cache manager for web search, RAG, and embeddings."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import settings
from core.workspace import PROJECT_ROOT


CACHE_FILES = {
    "web_search": "web_search_cache.json",
    "rag": "rag_cache.json",
    "embedding": "embedding_cache.json",
}
MAX_VALUE_CHARS = 120_000
SECRET_MARKERS = ("LLM_API_KEY", "DEEPSEEK_API_KEY", "TAVILY_API_KEY", "EMBEDDING_API_KEY", "sk-", "tvly-")


class CacheManager:
    """Read and write bounded local cache entries."""

    def __init__(self, cache_root: str | Path | None = None) -> None:
        configured = cache_root if cache_root is not None else settings.cache_root
        raw = Path(configured)
        self.cache_root = raw if raw.is_absolute() else PROJECT_ROOT / raw
        self.cache_root.mkdir(parents=True, exist_ok=True)

    def get(self, cache_name: str, key: str) -> Any | None:
        """Return a cached value when present and not expired."""

        if not settings.cache_enabled:
            return None
        payload = self._load(cache_name)
        entry = payload.get("entries", {}).get(key)
        if not isinstance(entry, dict):
            return None
        if _utc_ts() >= float(entry.get("expires_at", 0) or 0):
            self.delete(cache_name, key)
            return None
        return entry.get("value")

    def set(self, cache_name: str, key: str, value: Any, ttl_seconds: int) -> bool:
        """Store a bounded cache value."""

        if not settings.cache_enabled or _contains_secret(value):
            return False
        try:
            encoded = json.dumps(value, ensure_ascii=False)
        except TypeError:
            return False
        if len(encoded) > MAX_VALUE_CHARS:
            return False
        payload = self._load(cache_name)
        payload.setdefault("entries", {})[key] = {
            "created_at": _utc_iso(),
            "expires_at": _utc_ts() + max(1, int(ttl_seconds or 1)),
            "value": value,
        }
        self._save(cache_name, payload)
        return True

    def delete(self, cache_name: str, key: str) -> bool:
        payload = self._load(cache_name)
        existed = key in payload.get("entries", {})
        payload.setdefault("entries", {}).pop(key, None)
        self._save(cache_name, payload)
        return existed

    def clear(self, cache_name: str | None = None) -> dict[str, Any]:
        names = list(CACHE_FILES) if cache_name in {None, "all"} else [str(cache_name)]
        cleared: dict[str, int] = {}
        for name in names:
            payload = self._load(name)
            count = len(payload.get("entries", {}))
            self._save(name, {"version": 1, "entries": {}})
            cleared[name] = count
        return {"success": True, "data": {"cleared": cleared}}

    def make_key(self, *parts: Any) -> str:
        normalized = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def cleanup_expired(self) -> dict[str, Any]:
        data: dict[str, dict[str, int]] = {}
        now = _utc_ts()
        for name in CACHE_FILES:
            payload = self._load(name)
            entries = payload.get("entries", {})
            before = len(entries)
            payload["entries"] = {
                key: entry
                for key, entry in entries.items()
                if isinstance(entry, dict) and float(entry.get("expires_at", 0) or 0) > now
            }
            self._save(name, payload)
            data[name] = {"before": before, "expired_removed": before - len(payload["entries"]), "after": len(payload["entries"])}
        return {"success": True, "data": data}

    def status(self) -> dict[str, Any]:
        now = _utc_ts()
        caches = {}
        for name in CACHE_FILES:
            payload = self._load(name)
            entries = payload.get("entries", {})
            expired = sum(1 for entry in entries.values() if isinstance(entry, dict) and float(entry.get("expires_at", 0) or 0) <= now)
            oversized = 0
            for entry in entries.values():
                value = entry.get("value") if isinstance(entry, dict) else entry
                if len(json.dumps(value, ensure_ascii=False, default=str)) > MAX_VALUE_CHARS:
                    oversized += 1
            caches[name] = {"entries": len(entries), "expired": expired, "oversized": oversized, "file": str(self._path(name))}
        return {"success": True, "data": {"cache_root": str(self.cache_root), "caches": caches}}

    def _load(self, cache_name: str) -> dict[str, Any]:
        path = self._path(cache_name)
        if not path.exists():
            self._save(cache_name, {"version": 1, "entries": {}})
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            shutil.copy2(path, path.with_suffix(f".corrupt-{stamp}.json"))
            payload = {"version": 1, "entries": {}}
            self._save(cache_name, payload)
        if not isinstance(payload, dict):
            payload = {"version": 1, "entries": {}}
        payload.setdefault("version", 1)
        payload.setdefault("entries", {})
        return payload

    def _save(self, cache_name: str, payload: dict[str, Any]) -> None:
        self._path(cache_name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _path(self, cache_name: str) -> Path:
        if cache_name not in CACHE_FILES:
            raise ValueError(f"unknown cache name: {cache_name}")
        return self.cache_root / CACHE_FILES[cache_name]


def _utc_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _contains_secret(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return any(marker in text for marker in SECRET_MARKERS)
