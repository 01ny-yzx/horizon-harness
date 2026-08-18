"""Tools for local cache inspection and maintenance."""

from __future__ import annotations

from typing import Any

from core.cache import CacheManager


def get_cache_status() -> dict[str, Any]:
    """Return counts and expiry state for local cache files."""

    return CacheManager().status()


def clear_cache(cache_name: str = "all") -> dict[str, Any]:
    """Clear one cache or all caches."""

    return CacheManager().clear(cache_name=cache_name)


def cleanup_cache() -> dict[str, Any]:
    """Remove expired cache entries."""

    return CacheManager().cleanup_expired()


CACHE_TOOLS = {
    "get_cache_status": get_cache_status,
    "clear_cache": clear_cache,
    "cleanup_cache": cleanup_cache,
}


CACHE_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_cache_status",
            "description": "Return local cache entry counts, expired entries, and oversized entries.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_cache",
            "description": "Clear one local cache by name, or all caches.",
            "parameters": {"type": "object", "properties": {"cache_name": {"type": "string", "default": "all"}}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cleanup_cache",
            "description": "Remove expired entries from local caches.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]
