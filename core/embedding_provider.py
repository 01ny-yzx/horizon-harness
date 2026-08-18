"""Embedding provider abstraction for semantic retrieval v1."""

from __future__ import annotations

import importlib.util
import hashlib
from typing import Any

from config.settings import settings
from core.cache import CacheManager


MAX_EMBED_TEXT_CHARS = 6000
MAX_BATCH_SIZE = 32


class EmbeddingProvider:
    """OpenAI-compatible embedding provider wrapper."""

    def __init__(
        self,
        enabled: bool | None = None,
        provider: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.enabled = settings.embedding_enabled if enabled is None else enabled
        self.provider = provider or settings.embedding_provider
        self.api_key = settings.embedding_api_key if api_key is None else api_key
        self.base_url = settings.embedding_base_url if base_url is None else base_url
        self.model = model or settings.embedding_model

    def get_status(self) -> dict[str, Any]:
        """Return current embedding capability status without exposing secrets."""

        api_key_configured = bool((self.api_key or "").strip())
        base_url_configured = bool((self.base_url or "").strip())
        if not self.enabled:
            available = False
            reason = "Embedding is disabled by EMBEDDING_ENABLED=false."
        elif self.provider != "openai_compatible":
            available = False
            reason = f"Unsupported embedding provider: {self.provider}."
        elif not api_key_configured or not base_url_configured:
            available = False
            reason = "EMBEDDING_API_KEY or EMBEDDING_BASE_URL is not configured."
        elif importlib.util.find_spec("openai") is None:
            available = False
            reason = "openai package is not installed. Please run pip install -r requirements.txt."
        else:
            available = True
            reason = "Embedding provider is configured."
        return {
            "success": True,
            "data": {
                "enabled": self.enabled,
                "provider": self.provider,
                "model": self.model,
                "base_url_configured": base_url_configured,
                "api_key_configured": api_key_configured,
                "available": available,
                "reason": reason,
            },
        }

    def embed_text(self, text: str) -> dict[str, Any]:
        """Embed one text string with an OpenAI-compatible API."""

        if not text or not text.strip():
            return {"success": False, "error": "text cannot be empty."}
        status = self.get_status().get("data", {})
        if not isinstance(status, dict) or not status.get("available"):
            return {"success": False, "error": str(status.get("reason", "Embedding provider is unavailable."))}

        try:
            from openai import OpenAI
        except ImportError:
            return {"success": False, "error": "openai package is not installed. Please install requirements.txt."}

        safe_text = text[:MAX_EMBED_TEXT_CHARS]
        cache = CacheManager()
        cache_key = cache.make_key("embedding", self.provider, self.model, hashlib.sha256(safe_text.encode("utf-8")).hexdigest())
        if settings.embedding_cache_enabled:
            cached = cache.get("embedding", cache_key)
            if isinstance(cached, dict) and isinstance(cached.get("embedding"), list):
                return {
                    "success": True,
                    "data": {
                        "embedding": cached.get("embedding", []),
                        "embedding_dim": int(cached.get("embedding_dim", 0) or 0),
                        "embedding_model": cached.get("embedding_model", self.model),
                        "truncated": len(text) > len(safe_text),
                        "cached": True,
                    },
                }
        try:
            client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            response = client.embeddings.create(model=self.model, input=safe_text)
            embedding = list(response.data[0].embedding)
            if settings.embedding_cache_enabled:
                cache.set(
                    "embedding",
                    cache_key,
                    {
                        "embedding": embedding,
                        "embedding_dim": len(embedding),
                        "embedding_model": self.model,
                        "text_hash": hashlib.sha256(safe_text.encode("utf-8")).hexdigest(),
                    },
                    ttl_seconds=30 * 24 * 3600,
                )
            return {
                "success": True,
                "data": {
                    "embedding": embedding,
                    "embedding_dim": len(embedding),
                    "embedding_model": self.model,
                    "truncated": len(text) > len(safe_text),
                    "cached": False,
                },
            }
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"embedding request failed: {exc}"}

    def embed_batch(self, texts: list[str]) -> dict[str, Any]:
        """Embed a small batch by looping over embed_text."""

        if not isinstance(texts, list) or not texts:
            return {"success": False, "error": "texts must be a non-empty list."}
        items = texts[:MAX_BATCH_SIZE]
        results = []
        failed = 0
        for index, text in enumerate(items):
            result = self.embed_text(str(text))
            if not result.get("success"):
                failed += 1
                results.append({"index": index, "success": False, "error": result.get("error", "")})
                continue
            data = result.get("data", {})
            results.append(
                {
                    "index": index,
                    "success": True,
                    "embedding": data.get("embedding") if isinstance(data, dict) else [],
                    "embedding_dim": data.get("embedding_dim") if isinstance(data, dict) else 0,
                    "embedding_model": data.get("embedding_model") if isinstance(data, dict) else self.model,
                }
            )
        return {
            "success": True,
            "data": {
                "requested": len(texts),
                "processed": len(items),
                "failed": failed,
                "results": results,
            },
        }
