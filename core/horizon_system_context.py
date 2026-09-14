"""Horizon built-in System Context source catalog."""

from __future__ import annotations

from datetime import date
import platform
from pathlib import Path
from typing import Any

from core.persistent_memory import PersistentMemory
from core.session import SessionInfo
from core.system_context import SystemContext, SystemContextSource, UNAVAILABLE
from core.system_context_registry import SystemContextRegistry, SystemContextRegistryEntry


def build_horizon_system_context_registry(
    session: SessionInfo,
    persistent_memory: PersistentMemory,
) -> SystemContextRegistry:
    registry = SystemContextRegistry()
    registry.register(
        SystemContextRegistryEntry(
            "horizon/builtins",
            lambda: SystemContext(
                (
                    _environment_source(session),
                    _date_source(),
                )
            ),
        )
    )
    registry.register(
        SystemContextRegistryEntry(
            "horizon/instructions",
            lambda: _instruction_context(Path(session.directory)),
        )
    )
    registry.register(
        SystemContextRegistryEntry(
            "horizon/persistent-memory",
            lambda: _persistent_context(persistent_memory),
        )
    )
    return registry


def _environment_source(session: SessionInfo) -> SystemContextSource:
    value = {
        "directory": str(Path(session.directory).resolve()),
        "workspace_id": session.workspace_id,
        "project_id": session.project_id,
        "is_git_repo": (Path(session.directory) / ".git").exists(),
        "platform": platform.system(),
    }
    return SystemContextSource(
        key="horizon/environment",
        load=lambda: dict(value),
        baseline=lambda current: (
            "Here is some useful information about the environment you are running in:\n"
            + _render_environment(current)
        ),
        update=lambda _previous, current: (
            "The environment you are running in is now:\n"
            + _render_environment(current)
        ),
        decode=_decode_environment_value,
    )


def _render_environment(value: dict[str, Any]) -> str:
    return "\n".join(
        (
            "<env>",
            f"  Working directory: {value['directory']}",
            f"  Workspace root folder: {value['directory']}",
            f"  Workspace identity: {value['workspace_id']}",
            f"  Project identity: {value['project_id']}",
            f"  Is directory a git repo: {'yes' if value['is_git_repo'] else 'no'}",
            f"  Platform: {value['platform']}",
            "</env>",
        )
    )


def _date_source() -> SystemContextSource:
    return SystemContextSource(
        key="horizon/date",
        load=lambda: date.today().isoformat(),
        baseline=lambda value: f"Today's date: {value}",
        update=lambda _previous, current: f"Today's date is now: {current}",
        decode=_decode_date_value,
    )


def _instruction_context(project_root: Path) -> SystemContext:
    path = (project_root / "HORIZON.md").resolve()

    def load() -> Any:
        try:
            if not path.exists():
                return []
            return [
                {
                    "path": str(path),
                    "content": path.read_text(encoding="utf-8").strip(),
                }
            ]
        except (OSError, UnicodeError):
            return UNAVAILABLE

    def render(value: list[dict[str, str]]) -> str:
        return "\n\n".join(
            f"Instructions from: {item['path']}\n{item['content']}" for item in value
        )

    source = SystemContextSource(
        key="horizon/instructions",
        load=load,
        baseline=render,
        update=lambda _previous, current: (
            "These instructions replace all previously loaded project instructions.\n\n"
            + render(current)
        ),
        removed=lambda _previous: "Previously loaded project instructions no longer apply.",
        decode=_decode_instruction_value,
    )
    # A missing file is a genuinely absent source; a present empty file remains available.
    try:
        if not path.exists():
            return SystemContext()
    except OSError:
        pass
    return SystemContext((source,))


def _persistent_context(memory: PersistentMemory) -> SystemContext:
    loaded = memory.load_all()
    if not loaded.get("success"):
        instruction_value: Any = UNAVAILABLE
        reference_value: Any = UNAVAILABLE
    else:
        instruction_text = memory.format_instruction_context(max_chars=None)
        instruction_value = {"text": instruction_text} if instruction_text else None
        counts = memory.get_memory_reference_counts()
        available = [key for key, count in counts.items() if int(count) > 0]
        reference_text = memory.format_reference_guidance(max_chars=None)
        reference_value = (
            {"available_types": available, "counts": counts, "text": reference_text}
            if reference_text
            else None
        )

    instruction = SystemContextSource(
        key="horizon/persistent-instructions",
        load=lambda: instruction_value,
        baseline=lambda value: str(value["text"]) if value else "",
        update=lambda _previous, current: (
            "These persistent instructions replace the previously loaded persistent instructions.\n\n"
            + (str(current["text"]) if current else "")
        ),
        removed=lambda _previous: "Previously loaded persistent instructions no longer apply.",
        decode=_decode_persistent_instruction_value,
    )
    reference = SystemContextSource(
        key="horizon/memory-reference-guidance",
        load=lambda: reference_value,
        baseline=lambda value: str(value["text"]) if value else "",
        update=lambda _previous, current: (
            "This memory reference guidance replaces the previously loaded guidance.\n\n"
            + (str(current["text"]) if current else "")
        ),
        removed=lambda _previous: "Previously loaded memory reference guidance no longer applies.",
        decode=_decode_memory_reference_guidance_value,
    )
    sources = []
    if instruction_value is not None:
        sources.append(instruction)
    if reference_value is not None:
        sources.append(reference)
    return SystemContext(sources)


def effective_instruction_paths(snapshot: dict[str, dict[str, Any]]) -> tuple[str, ...]:
    item = snapshot.get("horizon/instructions")
    value = item.get("value") if isinstance(item, dict) else None
    if not isinstance(value, list):
        return ()
    return tuple(
        str(entry.get("path") or "")
        for entry in value
        if isinstance(entry, dict) and str(entry.get("path") or "")
    )


def _decode_environment_value(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("environment snapshot value must be an object")
    required = {
        "directory": str,
        "workspace_id": str,
        "project_id": str,
        "platform": str,
    }
    decoded: dict[str, Any] = {}
    for key, expected in required.items():
        item = value.get(key)
        if not isinstance(item, expected):
            raise TypeError(f"environment.{key} must be a string")
        decoded[key] = item
    git = value.get("is_git_repo")
    if type(git) is not bool:
        raise TypeError("environment.is_git_repo must be a boolean")
    decoded["is_git_repo"] = git
    return {
        "directory": decoded["directory"],
        "workspace_id": decoded["workspace_id"],
        "project_id": decoded["project_id"],
        "is_git_repo": decoded["is_git_repo"],
        "platform": decoded["platform"],
    }


def _decode_date_value(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("date snapshot value must be a string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("date snapshot value must be an ISO calendar date") from exc
    return parsed.isoformat()


def _decode_instruction_value(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise TypeError("instruction snapshot value must be a list")
    decoded: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise TypeError("instruction snapshot entries must be objects")
        path = item.get("path")
        content = item.get("content")
        if not isinstance(path, str) or not path.strip():
            raise TypeError("instruction path must be a non-empty string")
        if not Path(path).is_absolute():
            raise ValueError("instruction path must be absolute")
        if not isinstance(content, str):
            raise TypeError("instruction content must be a string")
        decoded.append({"path": path, "content": content})
    return decoded


def _decode_persistent_instruction_value(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise TypeError("persistent instruction snapshot value must be an object")
    text = value.get("text")
    if not isinstance(text, str):
        raise TypeError("persistent instruction text must be a string")
    return {"text": text}


def _decode_memory_reference_guidance_value(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("memory reference snapshot value must be an object")
    available_types = value.get("available_types")
    counts = value.get("counts")
    text = value.get("text")
    if not isinstance(available_types, list) or any(
        not isinstance(item, str) for item in available_types
    ):
        raise TypeError("memory reference available_types must be a list of strings")
    if not isinstance(counts, dict):
        raise TypeError("memory reference counts must be an object")
    decoded_counts: dict[str, int] = {}
    for key, count in counts.items():
        if not isinstance(key, str):
            raise TypeError("memory reference count keys must be strings")
        if type(count) is not int or count < 0:
            raise TypeError("memory reference counts must be non-negative integers")
        decoded_counts[key] = count
    if not isinstance(text, str):
        raise TypeError("memory reference text must be a string")
    return {
        "available_types": list(available_types),
        "counts": decoded_counts,
        "text": text,
    }


__all__ = ["build_horizon_system_context_registry", "effective_instruction_paths"]
