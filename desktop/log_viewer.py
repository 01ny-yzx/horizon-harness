"""Safe local log listing and tail reading for the desktop client."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from desktop.paths import DesktopPaths


DEFAULT_MAX_LOG_FILES = 20
DEFAULT_MAX_BYTES = 64 * 1024
DEFAULT_TAIL_LINES = 200
ALLOWED_LOG_SUFFIXES = (".log", ".txt", ".out", ".err")

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"tvly-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)\b(api[_-]?key|apikey|token|password|secret|credential)\b\s*([:=])\s*([^\s,;]+)"),
    re.compile(r"(?i)\b(authorization)\b\s*:\s*bearer\s+([^\s,;]+)"),
]


@dataclass(frozen=True)
class DesktopLogFile:
    name: str
    path: str
    size_bytes: int
    modified_time: float
    suffix: str


@dataclass(frozen=True)
class DesktopLogListResult:
    ok: bool
    logs_dir: str
    files: list[DesktopLogFile] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DesktopLogReadResult:
    ok: bool
    logs_dir: str
    file_name: str | None = None
    content: str = ""
    lines: list[str] = field(default_factory=list)
    truncated: bool = False
    bytes_read: int = 0
    total_size_bytes: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def sanitize_log_text(value: object, *, max_length: int | None = None) -> str:
    try:
        text = str(value)
    except Exception:
        text = "<unprintable>"
    for pattern in SECRET_PATTERNS[:2]:
        text = pattern.sub("[REDACTED_KEY]", text)
    text = SECRET_PATTERNS[2].sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)
    text = SECRET_PATTERNS[3].sub(lambda match: f"{match.group(1)}: Bearer [REDACTED]", text)
    if max_length is not None and max_length >= 0 and len(text) > max_length:
        suffix = "...[truncated]"
        keep = max(0, max_length - len(suffix))
        return text[:keep] + suffix
    return text


def is_allowed_log_file(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink() and path.suffix.lower() in ALLOWED_LOG_SUFFIXES
    except OSError:
        return False


def safe_resolve_log_path(logs_dir: str | Path, file_name: str) -> Path | None:
    try:
        candidate_name = Path(file_name)
        if candidate_name.is_absolute() or candidate_name.name != file_name:
            return None
        base = Path(logs_dir).resolve()
        candidate = base / file_name
        if not candidate.exists() or candidate.is_symlink() or not is_allowed_log_file(candidate):
            return None
        resolved = candidate.resolve()
        if resolved.parent != base:
            return None
        return resolved
    except (OSError, ValueError):
        return None


def list_desktop_log_files(
    logs_dir: str | Path,
    *,
    max_files: int = DEFAULT_MAX_LOG_FILES,
) -> DesktopLogListResult:
    directory = Path(logs_dir)
    safe_max_files = _positive_int(max_files, DEFAULT_MAX_LOG_FILES)
    if not directory.exists():
        return DesktopLogListResult(ok=False, logs_dir=str(directory), errors=["logs_dir_missing"])
    if not directory.is_dir():
        return DesktopLogListResult(ok=False, logs_dir=str(directory), errors=["logs_dir_not_directory"])
    files: list[DesktopLogFile] = []
    warnings: list[str] = []
    try:
        for path in directory.iterdir():
            if not is_allowed_log_file(path):
                continue
            stat = path.stat()
            files.append(
                DesktopLogFile(
                    name=path.name,
                    path=str(path),
                    size_bytes=stat.st_size,
                    modified_time=stat.st_mtime,
                    suffix=path.suffix.lower(),
                )
            )
    except OSError as exc:
        return DesktopLogListResult(ok=False, logs_dir=str(directory), errors=[_safe_error(exc)])
    files.sort(key=lambda item: item.modified_time, reverse=True)
    return DesktopLogListResult(ok=True, logs_dir=str(directory), files=files[:safe_max_files], warnings=warnings)


def read_desktop_log_tail(
    logs_dir: str | Path,
    file_name: str,
    *,
    tail_lines: int = DEFAULT_TAIL_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    encoding: str = "utf-8",
) -> DesktopLogReadResult:
    directory = Path(logs_dir)
    safe_tail_lines = _positive_int(tail_lines, DEFAULT_TAIL_LINES)
    safe_max_bytes = _positive_int(max_bytes, DEFAULT_MAX_BYTES)
    path = safe_resolve_log_path(directory, file_name)
    if path is None:
        return DesktopLogReadResult(ok=False, logs_dir=str(directory), file_name=file_name, errors=["invalid_log_file"])
    try:
        total_size = path.stat().st_size
        offset = max(0, total_size - safe_max_bytes)
        with path.open("rb") as handle:
            handle.seek(offset)
            raw = handle.read(safe_max_bytes)
        decoded = raw.decode(encoding, errors="replace")
        split_lines = decoded.splitlines()
        truncated = offset > 0 or len(split_lines) > safe_tail_lines
        selected_lines = split_lines[-safe_tail_lines:]
        sanitized_lines = [sanitize_log_text(line) for line in selected_lines]
        content = sanitize_log_text("\n".join(selected_lines))
        return DesktopLogReadResult(
            ok=True,
            logs_dir=str(directory),
            file_name=path.name,
            content=content,
            lines=sanitized_lines,
            truncated=truncated,
            bytes_read=len(raw),
            total_size_bytes=total_size,
        )
    except OSError as exc:
        return DesktopLogReadResult(
            ok=False,
            logs_dir=str(directory),
            file_name=file_name,
            errors=[_safe_error(exc)],
        )


def desktop_log_file_to_dict(item: DesktopLogFile) -> dict[str, object]:
    return asdict(item)


def desktop_log_list_result_to_dict(result: DesktopLogListResult) -> dict[str, object]:
    return {
        "ok": result.ok,
        "logs_dir": result.logs_dir,
        "files": [desktop_log_file_to_dict(item) for item in result.files],
        "errors": list(result.errors),
        "warnings": list(result.warnings),
    }


def desktop_log_read_result_to_dict(result: DesktopLogReadResult) -> dict[str, object]:
    return asdict(result)


def list_desktop_logs_from_paths(
    paths: DesktopPaths,
    *,
    max_files: int = DEFAULT_MAX_LOG_FILES,
) -> DesktopLogListResult:
    return list_desktop_log_files(paths.logs_dir, max_files=max_files)


def read_desktop_log_tail_from_paths(
    paths: DesktopPaths,
    file_name: str,
    *,
    tail_lines: int = DEFAULT_TAIL_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> DesktopLogReadResult:
    return read_desktop_log_tail(paths.logs_dir, file_name, tail_lines=tail_lines, max_bytes=max_bytes)


def _positive_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _safe_error(exc: BaseException) -> str:
    return sanitize_log_text(" ".join(str(exc).split())[:200])
