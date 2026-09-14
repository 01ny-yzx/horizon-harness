"""Structured, refreshable privileged context for durable Sessions."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Iterable


class _Unavailable:
    def __repr__(self) -> str:
        return "UNAVAILABLE"


UNAVAILABLE = _Unavailable()


class SystemContextInitializationBlocked(RuntimeError):
    """Raised when an initial baseline cannot observe every active source."""

    def __init__(self, keys: Iterable[str]) -> None:
        self.keys = tuple(sorted(str(key) for key in keys))
        super().__init__("System Context unavailable: " + ", ".join(self.keys))


InitializationBlocked = SystemContextInitializationBlocked


class DuplicateContextKeyError(ValueError):
    """Raised when independently registered context sources share a key."""


@dataclass(frozen=True)
class SystemContextSource:
    key: str
    load: Callable[[], Any]
    baseline: Callable[[Any], str]
    update: Callable[[Any, Any], str]
    removed: Callable[[Any], str] | None = None
    decode: Callable[[Any], Any] = lambda value: value


@dataclass(frozen=True)
class SystemContextSnapshot:
    sources: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return {key: dict(value) for key, value in sorted(self.sources.items())}

    @classmethod
    def from_dict(cls, value: Any) -> "SystemContextSnapshot":
        if not isinstance(value, dict):
            raise ValueError("System Context snapshot must be an object")
        sources: dict[str, dict[str, Any]] = {}
        for key, item in value.items():
            if not _KEY_PATTERN.fullmatch(str(key)):
                raise ValueError(f"Invalid System Context snapshot key: {key}")
            if not isinstance(item, dict) or "value" not in item:
                raise ValueError(f"Invalid System Context snapshot source: {key}")
            if "removed" in item and (
                not isinstance(item["removed"], str) or not item["removed"]
            ):
                raise ValueError(f"Invalid System Context removal rendering: {key}")
            sources[str(key)] = dict(item)
        return cls(sources)


@dataclass(frozen=True)
class SystemContextGeneration:
    baseline: str
    snapshot: SystemContextSnapshot


@dataclass(frozen=True)
class Unchanged:
    snapshot: SystemContextSnapshot


@dataclass(frozen=True)
class Updated:
    text: str
    snapshot: SystemContextSnapshot


@dataclass(frozen=True)
class ReplacementReady:
    generation: SystemContextGeneration


@dataclass(frozen=True)
class ReplacementBlocked:
    snapshot: SystemContextSnapshot


SystemContextReconcileResult = Unchanged | Updated | ReplacementReady | ReplacementBlocked
_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._/-]*$")


class SystemContext:
    """A stable-key collection of independently observable context sources."""

    def __init__(self, sources: Iterable[SystemContextSource] = ()) -> None:
        by_key: dict[str, SystemContextSource] = {}
        for source in sources:
            key = str(source.key or "").strip()
            if not _KEY_PATTERN.fullmatch(key):
                raise ValueError(f"Invalid System Context source key: {key}")
            if key in by_key:
                raise DuplicateContextKeyError(key)
            by_key[key] = source
        self.sources = tuple(by_key.values())
        self._by_key = by_key

    def initialize(self) -> SystemContextGeneration:
        observed: list[tuple[SystemContextSource, Any]] = []
        unavailable: list[str] = []
        for source in self.sources:
            value = source.load()
            if value is UNAVAILABLE:
                unavailable.append(source.key)
            else:
                observed.append((source, value))
        if unavailable:
            raise SystemContextInitializationBlocked(unavailable)
        return self._generation(observed)

    def reconcile(self, previous: SystemContextSnapshot) -> SystemContextReconcileResult:
        observed: dict[str, Any] = {}
        incompatible = False
        for source in self.sources:
            value = source.load()
            observed[source.key] = value

        updates: list[str] = []
        next_sources: dict[str, dict[str, Any]] = {}
        for source in self.sources:
            key = source.key
            prior = previous.sources.get(key)
            loaded = observed[key]
            if loaded is UNAVAILABLE:
                if prior is not None:
                    next_sources[key] = dict(prior)
                continue
            if prior is None:
                text = _require_text(source.key, "baseline", source.baseline(loaded))
                updates.append(text)
                next_sources[key] = _snapshot_item(source, loaded)
                continue
            try:
                old_value = source.decode(prior.get("value"))
            except Exception:
                incompatible = True
                continue
            if old_value != loaded:
                text = _require_text(
                    source.key, "update", source.update(old_value, loaded)
                )
                updates.append(text)
                next_sources[key] = _snapshot_item(source, loaded)
            else:
                next_sources[key] = dict(prior)

        for key in sorted(previous.sources):
            if key in self._by_key:
                continue
            removed = previous.sources[key].get("removed")
            if not isinstance(removed, str):
                incompatible = True
                continue
            updates.append(removed)

        if incompatible:
            return self._replace_observation(previous, observed)
        snapshot = SystemContextSnapshot(next_sources)
        text = "\n\n".join(updates)
        if not text:
            return Unchanged(snapshot)
        return Updated(text, snapshot)

    def replace(self, previous: SystemContextSnapshot) -> ReplacementReady | ReplacementBlocked:
        observed = {source.key: source.load() for source in self.sources}
        return self._replace_observation(previous, observed)

    def _replace_observation(
        self,
        previous: SystemContextSnapshot,
        observed: dict[str, Any],
    ) -> ReplacementReady | ReplacementBlocked:
        available: list[tuple[SystemContextSource, Any]] = []
        for source in self.sources:
            value = observed[source.key]
            if value is UNAVAILABLE:
                if source.key in previous.sources:
                    return ReplacementBlocked(previous)
                continue
            available.append((source, value))
        return ReplacementReady(self._generation(available))

    @staticmethod
    def _generation(
        observed: Iterable[tuple[SystemContextSource, Any]],
    ) -> SystemContextGeneration:
        baseline: list[str] = []
        snapshot: dict[str, dict[str, Any]] = {}
        for source, value in observed:
            text = _require_text(source.key, "baseline", source.baseline(value))
            baseline.append(text)
            snapshot[source.key] = _snapshot_item(source, value)
        return SystemContextGeneration(
            baseline="\n\n".join(baseline),
            snapshot=SystemContextSnapshot(snapshot),
        )


def _snapshot_item(source: SystemContextSource, value: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"value": value}
    if source.removed is not None:
        item["removed"] = _require_text(
            source.key, "removal", str(source.removed(value))
        )
    return item


def _require_text(key: str, kind: str, value: str) -> str:
    text = str(value)
    if not text:
        raise ValueError(f"System Context source {key} rendered an empty {kind}")
    return text


def combine(*contexts: SystemContext) -> SystemContext:
    return SystemContext(source for context in contexts for source in context.sources)


def initialize(context: SystemContext) -> SystemContextGeneration:
    return context.initialize()


def reconcile(
    context: SystemContext,
    previous: SystemContextSnapshot,
) -> SystemContextReconcileResult:
    return context.reconcile(previous)


def replace(
    context: SystemContext,
    previous: SystemContextSnapshot,
) -> ReplacementReady | ReplacementBlocked:
    return context.replace(previous)


__all__ = [
    "DuplicateContextKeyError",
    "InitializationBlocked",
    "ReplacementBlocked",
    "ReplacementReady",
    "SystemContext",
    "SystemContextGeneration",
    "SystemContextInitializationBlocked",
    "SystemContextSnapshot",
    "SystemContextSource",
    "UNAVAILABLE",
    "Unchanged",
    "Updated",
    "combine",
    "initialize",
    "reconcile",
    "replace",
]
