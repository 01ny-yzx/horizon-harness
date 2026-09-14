"""Stable runtime catalog for System Context sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.system_context import DuplicateContextKeyError, SystemContext, _KEY_PATTERN, combine


@dataclass(frozen=True)
class SystemContextRegistryEntry:
    key: str
    load: Callable[[], SystemContext]


class SystemContextRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, SystemContextRegistryEntry] = {}

    def register(self, entry: SystemContextRegistryEntry) -> None:
        key = str(entry.key or "").strip()
        if not _KEY_PATTERN.fullmatch(key):
            raise ValueError(f"Invalid System Context registry key: {key}")
        if key in self._entries:
            raise DuplicateContextKeyError(key)
        self._entries[key] = entry

    def load(self) -> SystemContext:
        return combine(*(self._entries[key].load() for key in sorted(self._entries)))


__all__ = ["SystemContextRegistry", "SystemContextRegistryEntry"]
