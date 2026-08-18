"""Helpers for keeping LLM payloads UTF-8 encodable."""

from __future__ import annotations

from typing import Any


def sanitize_unicode(value: Any) -> Any:
    """Recursively replace invalid lone surrogates while preserving valid Unicode."""

    if isinstance(value, str):
        return value.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(value, list):
        return [sanitize_unicode(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_unicode(item) for item in value)
    if isinstance(value, dict):
        return {sanitize_unicode(key): sanitize_unicode(item) for key, item in value.items()}
    return value
