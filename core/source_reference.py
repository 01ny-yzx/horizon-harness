"""Validated references to user-authorized source files."""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.file_access_policy import FileAccessPolicy


@dataclass(frozen=True)
class SourceReferenceResolution:
    ok: bool
    ref: str
    chars: int | None = None
    bytes: int = 0
    sha256: str = ""
    encoding: str | None = None
    mime_type: str = "application/octet-stream"
    is_text: bool = False
    error_code: str = ""
    error_message: str = ""


def resolve_source_file_metadata(
    path: str | Path,
    *,
    operation: str,
    expected_sha256: str | None = None,
    expected_chars: int | None = None,
    expected_bytes: int | None = None,
) -> SourceReferenceResolution:
    """Validate a source file through FileAccessPolicy and resolve real metadata."""

    raw_ref = str(path or "").strip()
    if not raw_ref:
        return _error(raw_ref, "replay_source_missing", "Source reference is empty.")
    raw_path = Path(raw_ref).expanduser()
    if not raw_path.is_absolute() and str(operation or "").strip().lower().startswith("replay"):
        return _error(raw_ref, "replay_source_invalid_ref", "Source reference must be an absolute path.")

    policy_operation = "write" if str(operation or "").strip().lower() in {"write", "write_result"} else "read"
    decision = FileAccessPolicy().evaluate(
        raw_path,
        operation=policy_operation,
        raw_requested_path=raw_ref,
    )
    if not decision.allowed:
        invalid_codes = {"path_empty", "path_traversal_blocked"}
        code = "replay_source_invalid_ref" if decision.code in invalid_codes else "replay_source_access_denied"
        return _error(str(decision.resolved_path or raw_ref), code, str(decision.reason or decision.code))

    try:
        resolved = Path(decision.resolved_path or raw_path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return _error(raw_ref, "replay_source_invalid_ref", "Source reference could not be resolved.")
    try:
        if not resolved.exists() or not resolved.is_file():
            return _error(str(resolved), "replay_source_missing", "Source file does not exist.")
        raw = resolved.read_bytes()
    except OSError as exc:
        return _error(str(resolved), "replay_source_access_denied", str(exc))

    digest = hashlib.sha256(raw).hexdigest()
    byte_count = len(raw)
    if expected_sha256 and digest != str(expected_sha256):
        return _error(str(resolved), "replay_source_hash_mismatch", "Source file hash changed.", raw=raw, sha256=digest)
    mime_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
    is_text = is_text_source(resolved, mime_type, raw)
    chars: int | None = None
    encoding: str | None = None
    if is_text:
        try:
            chars = len(raw.decode("utf-8"))
            encoding = "utf-8"
        except UnicodeDecodeError:
            # Source metadata is descriptive: an unknown text encoding must not
            # reverse an already-successful tool result.
            encoding = "unknown"
    if chars is not None and expected_chars is not None and not _expected_int_matches(expected_chars, chars):
        return SourceReferenceResolution(
            False, str(resolved), chars, byte_count, digest, encoding, mime_type, is_text,
            "replay_source_chars_mismatch", "Source character count changed."
        )
    if expected_bytes is not None and not _expected_int_matches(expected_bytes, byte_count):
        return SourceReferenceResolution(
            False, str(resolved), chars, byte_count, digest, encoding, mime_type, is_text,
            "replay_source_bytes_mismatch", "Source byte count changed."
        )
    return SourceReferenceResolution(True, str(resolved), chars, byte_count, digest, encoding, mime_type, is_text)


TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".markdown", ".json", ".jsonl", ".xml", ".html", ".htm", ".csv", ".tsv",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".log", ".py", ".js", ".ts", ".tsx",
    ".jsx", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".sh", ".sql",
})
BINARY_EXTENSIONS = frozenset({
    ".pdf", ".docx", ".xlsx", ".pptx", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".bmp", ".tif", ".tiff", ".ico", ".gz", ".bz2", ".xz", ".7z", ".rar",
})


def is_text_source(path: str | Path, mime_type: str, raw_bytes: bytes) -> bool:
    """Classify a source without treating unknown binary bytes as an error."""

    suffix = Path(path).suffix.lower()
    if suffix in BINARY_EXTENSIONS:
        return False
    if str(mime_type or "").lower().startswith("text/") or suffix in TEXT_EXTENSIONS:
        return True
    try:
        raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _expected_int_matches(expected: Any, actual: int) -> bool:
    try:
        return int(expected) == actual
    except (TypeError, ValueError):
        return False


def _error(
    ref: str,
    code: str,
    message: str,
    *,
    raw: bytes = b"",
    sha256: str = "",
) -> SourceReferenceResolution:
    mime_type = mimetypes.guess_type(ref)[0] or "application/octet-stream"
    return SourceReferenceResolution(False, ref, None, len(raw), sha256, None, mime_type, False, code, message)


__all__ = ["SourceReferenceResolution", "is_text_source", "resolve_source_file_metadata"]
