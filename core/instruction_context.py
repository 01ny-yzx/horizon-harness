"""Discover Horizon instruction files within one authoritative project root."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


INSTRUCTION_FILE_NAME = "HORIZON.md"
LOCAL_READ_TOOL_NAMES = frozenset({"read_file", "read_document"})


@dataclass(frozen=True)
class InstructionSource:
    path: str
    content: str


@dataclass(frozen=True)
class InstructionContext:
    content: str
    paths: tuple[str, ...]
    sources: tuple[InstructionSource, ...] = ()
    unavailable_paths: tuple[str, ...] = ()


def load_initial_instruction_context(project_root: Path | str) -> InstructionContext:
    """Load only the active project's root-level Horizon instruction."""

    root = Path(project_root).expanduser().resolve()
    return _load_instruction_paths([root / INSTRUCTION_FILE_NAME])


def resolve_nearby_instruction_context(
    target_file: Path | str,
    *,
    project_root: Path | str,
    system_paths: Iterable[Path | str] = (),
    loaded_paths: Iterable[Path | str] = (),
    claimed_paths: Iterable[Path | str] = (),
) -> InstructionContext:
    """Resolve instructions near one successfully read file, leaf directory first."""

    target = Path(target_file).expanduser().resolve()
    root = Path(project_root).expanduser().resolve()
    if not target.is_file() or not _is_within(target, root):
        return InstructionContext(content="", paths=())

    excluded = {
        _normalized_path(path)
        for path in (*system_paths, *loaded_paths, *claimed_paths)
        if str(path or "").strip()
    }
    discovered: list[Path] = []
    current = target.parent
    while current != root and _is_within(current, root):
        candidate = (current / INSTRUCTION_FILE_NAME).resolve()
        normalized = str(candidate)
        if candidate.is_file() and candidate != target and normalized not in excluded:
            discovered.append(candidate)
            excluded.add(normalized)
        current = current.parent
    return _load_instruction_paths(discovered)


def instruction_paths_from_messages(messages: Iterable[dict[str, Any]]) -> set[str]:
    """Return instruction paths still exposed by model-visible Read observations."""

    paths: set[str] = set()
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        if str(message.get("name") or "") not in LOCAL_READ_TOOL_NAMES:
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        status = str(payload.get("status") or "").strip().lower()
        if payload.get("success") is not True or status not in {"success", "completed"}:
            continue
        nearby = payload.get("nearby_instructions")
        if not isinstance(nearby, list):
            continue
        for item in nearby:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            instruction_content = item.get("content")
            if (
                isinstance(path, str)
                and path.strip()
                and isinstance(instruction_content, str)
                and instruction_content.strip()
            ):
                paths.add(_normalized_path(path))
    return paths


def _load_instruction_paths(paths: Iterable[Path]) -> InstructionContext:
    sections: list[str] = []
    loaded: list[str] = []
    sources: list[InstructionSource] = []
    unavailable: list[str] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            continue
        try:
            content = resolved.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            unavailable.append(str(resolved))
            continue
        loaded.append(str(resolved))
        if content:
            rendered = f"Instructions from: {resolved}\n{content}"
            sections.append(rendered)
            sources.append(InstructionSource(path=str(resolved), content=rendered))
    return InstructionContext(
        content="\n\n".join(sections),
        paths=tuple(loaded),
        sources=tuple(sources),
        unavailable_paths=tuple(unavailable),
    )


def _normalized_path(path: Path | str) -> str:
    return str(Path(path).expanduser().resolve())


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
