"""Resolve lightweight background-memory discovery for one model request."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.persistent_memory import PersistentMemory
from core.unicode_safety import sanitize_unicode


@dataclass(frozen=True)
class MemoryReferenceGuidance:
    """Current request-local discovery metadata for optional memory references."""

    system_messages: tuple[dict[str, Any], ...]
    available: bool
    available_types: tuple[str, ...]
    counts: dict[str, int]
    load_success: bool


def resolve_memory_reference_guidance(
    *,
    persistent_memory: PersistentMemory,
) -> MemoryReferenceGuidance:
    """Freshly describe available reference types without exposing record bodies."""

    loaded = persistent_memory.load_all()
    if not loaded.get("success"):
        return MemoryReferenceGuidance(
            system_messages=(),
            available=False,
            available_types=(),
            counts={},
            load_success=False,
        )

    counts = persistent_memory.get_memory_reference_counts()
    available_types = tuple(name for name, count in counts.items() if count > 0)
    content = persistent_memory.format_reference_guidance(max_chars=None)
    messages: list[dict[str, Any]] = []
    if content:
        messages.append(
            {
                "role": "system",
                "content": content,
                "metadata": {
                    "note_type": "memory_reference_guidance",
                    "available_types": list(available_types),
                    "counts": dict(counts),
                },
            }
        )
    clean_messages = sanitize_unicode(messages)
    return MemoryReferenceGuidance(
        system_messages=tuple(dict(message) for message in clean_messages),
        available=bool(available_types),
        available_types=available_types,
        counts=dict(counts),
        load_success=True,
    )
