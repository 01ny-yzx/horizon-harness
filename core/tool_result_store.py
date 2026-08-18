"""Workspace-scoped durable storage for externalized tool result bodies."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
import re
import tempfile
from typing import Any

from core.unicode_safety import sanitize_unicode
from core.workspace_runtime import get_current_workspace


_REGISTERED_ARTIFACT_ROOTS: dict[Path, set[Path]] = {}


@dataclass(frozen=True)
class StoredToolResult:
    content_ref: str
    chars: int
    bytes: int
    line_count: int
    sha256: str
    externalized: bool = True
    reused_existing: bool = False

    def to_dict(self) -> dict[str, Any]:
        return sanitize_unicode(asdict(self))


@dataclass(frozen=True)
class ToolResultArtifactValidation:
    ref: str
    chars: int = 0
    bytes: int = 0
    sha256: str = ""
    valid: bool = False
    error_code: str = ""


def resolve_tool_artifact_metadata(
    ref: str | Path,
    expected_sha256: str = "",
    expected_chars: int | None = None,
    expected_bytes: int | None = None,
) -> ToolResultArtifactValidation:
    """Resolve metadata only for Horizon-registered Tool Artifact files."""

    raw_ref = str(ref or "")
    if not raw_ref:
        return ToolResultArtifactValidation(raw_ref, error_code="replay_artifact_missing")
    raw_path = Path(raw_ref).expanduser()
    if not raw_path.is_absolute():
        return ToolResultArtifactValidation(raw_ref, error_code="replay_artifact_invalid_ref")
    try:
        path = raw_path.resolve(strict=False)
    except (OSError, RuntimeError):
        return ToolResultArtifactValidation(raw_ref, error_code="replay_artifact_invalid_ref")
    workspace_root = get_current_workspace().workspace_dir.resolve()
    default_root = (workspace_root / "tool_results").resolve()
    roots = (default_root, *_REGISTERED_ARTIFACT_ROOTS.get(workspace_root, set()))
    resolved_roots: list[Path] = []
    for root in roots:
        try:
            resolved_roots.append(Path(root).expanduser().resolve())
        except (OSError, RuntimeError):
            continue
    if not any(path == root or root in path.parents for root in resolved_roots):
        return ToolResultArtifactValidation(str(path), error_code="replay_artifact_invalid_ref")
    try:
        if not path.exists() or not path.is_file():
            return ToolResultArtifactValidation(str(path), error_code="replay_artifact_missing")
        raw = path.read_bytes()
    except OSError:
        return ToolResultArtifactValidation(str(path), error_code="replay_artifact_missing")
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 and digest != str(expected_sha256):
        return ToolResultArtifactValidation(str(path), bytes=len(raw), sha256=digest, error_code="replay_artifact_hash_mismatch")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ToolResultArtifactValidation(str(path), bytes=len(raw), sha256=digest, error_code="replay_artifact_decode_error")
    chars = len(text)
    byte_count = len(raw)
    if expected_chars is not None:
        try:
            chars_match = int(expected_chars) == chars
        except (TypeError, ValueError):
            chars_match = False
        if not chars_match:
            return ToolResultArtifactValidation(str(path), chars=chars, bytes=byte_count, sha256=digest, error_code="replay_artifact_chars_mismatch")
    if expected_bytes is not None:
        try:
            bytes_match = int(expected_bytes) == byte_count
        except (TypeError, ValueError):
            bytes_match = False
        if not bytes_match:
            return ToolResultArtifactValidation(str(path), chars=chars, bytes=byte_count, sha256=digest, error_code="replay_artifact_bytes_mismatch")
    return ToolResultArtifactValidation(str(path), chars=chars, bytes=byte_count, sha256=digest, valid=True)


def validate_and_complete_text_artifact(
    ref: str | Path,
    expected_sha256: str = "",
    expected_chars: int | None = None,
    expected_bytes: int | None = None,
    *,
    require_sha256: bool = False,
) -> ToolResultArtifactValidation:
    """Backward-compatible alias for the strict Artifact resolver."""

    del require_sha256
    return resolve_tool_artifact_metadata(
        ref,
        expected_sha256,
        expected_chars,
        expected_bytes,
    )


def resolve_text_artifact_metadata(
    ref: str | Path,
    expected_sha256: str = "",
    expected_chars: int | None = None,
    expected_bytes: int | None = None,
) -> ToolResultArtifactValidation:
    """Compatibility alias for the Tool Artifact-only resolver."""

    return resolve_tool_artifact_metadata(ref, expected_sha256, expected_chars, expected_bytes)


class ToolResultStore:
    """Persist sanitized result bodies under the current workspace."""

    def __init__(self, root_dir: str | Path | None = None) -> None:
        workspace = get_current_workspace()
        self.root_dir = Path(root_dir or (workspace.workspace_dir / "tool_results")).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        workspace_root = workspace.workspace_dir.resolve()
        _REGISTERED_ARTIFACT_ROOTS.setdefault(workspace_root, set()).add(self.root_dir)

    def store_text(self, text: str, *, task_id: str, call_id: str, kind: str, suffix: str = ".txt") -> StoredToolResult:
        safe_text = redact_tool_result_text(text)
        encoded = safe_text.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        # Workspace isolation plus the full content hash gives stable cross-call deduplication.
        filename = f"{_safe_id(kind, 'result')}_{digest}{suffix}"
        path = (self.root_dir / filename).resolve()
        if self.root_dir not in path.parents:
            raise ValueError("tool result path escaped workspace store")
        reused = False
        if path.exists():
            existing = path.read_bytes()
            if hashlib.sha256(existing).hexdigest() != digest:
                raise RuntimeError("tool_result_hash_collision")
            reused = True
        else:
            _atomic_write(path, safe_text)
        return StoredToolResult(
            content_ref=str(path),
            chars=len(safe_text),
            bytes=len(encoded),
            line_count=0 if not safe_text else safe_text.count("\n") + 1,
            sha256=digest,
            reused_existing=reused,
        )

    def store_json(self, value: Any, *, task_id: str, call_id: str, kind: str = "observation") -> StoredToolResult:
        text = json.dumps(sanitize_unicode(value), ensure_ascii=False, indent=2, default=str)
        return self.store_text(text, task_id=task_id, call_id=call_id, kind=kind, suffix=".json")

    def store_canonical_json(self, value: Any, *, task_id: str, call_id: str, kind: str = "observation") -> StoredToolResult:
        """Persist deterministic JSON for recoverable derived tool results."""

        text = json.dumps(
            sanitize_unicode(value),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )
        return self.store_text(text, task_id=task_id, call_id=call_id, kind=kind, suffix=".json")


def redact_tool_result_text(text: Any) -> str:
    value = str(text or "")
    for key, secret in os.environ.items():
        if ("KEY" in key or "TOKEN" in key or "SECRET" in key) and secret and len(secret) >= 8:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(sk-[A-Za-z0-9_\-]{8,}|tvly-[A-Za-z0-9_\-]{8,})", "[REDACTED_KEY]", value)
    value = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED_KEY]", value)
    return sanitize_unicode(value)


def _safe_id(value: str, fallback: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_")
    return (safe or fallback)[:80]


def _atomic_write(path: Path, text: str) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            errors="replace",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


__all__ = [
    "StoredToolResult",
    "ToolResultArtifactValidation",
    "ToolResultStore",
    "redact_tool_result_text",
    "resolve_tool_artifact_metadata",
    "resolve_text_artifact_metadata",
    "validate_and_complete_text_artifact",
]
