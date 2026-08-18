"""Export a clean project zip without local caches or secrets."""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "dist" / "agent_export.zip"
EXCLUDED_DIRS = {
    ".git",
    ".horizon_runtime_validation",
    ".idea",
    ".pytest_cache",
    ".vscode",
    ".venv",
    "__pycache__",
    "dist",
    "logs",
    "node_modules",
    "playwright-report",
    "test-results",
}
STORE_DIRS = {
    "cache_store",
    "context_cache",
    "document_store",
    "memory_store",
    "rag_cache",
    "usage_store",
    "vector_store",
    "workspace_store",
    "sandbox_store",
    "browser_artifacts",
    "exports",
}
EXCLUDED_SUFFIXES = {".bak", ".db", ".pyc", ".sqlite", ".tmp", ".temp", ".webm", ".zip"}
EXCLUDED_NAMES = {".DS_Store"}
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"tvly-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|password|passwd|token|secret)\s*[:=]\s*[^\s#]+"),
]
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".toml", ".yaml", ".yml", ".ts", ".tsx", ".js", ".css", ".html"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help="project root to export")
    parser.add_argument("--output", type=Path, default=None, help="zip output path")
    args = parser.parse_args()

    root = args.root.resolve()
    output_path = (args.output or (root / "dist" / "agent_export.zip")).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    candidates = [path for path in sorted(root.rglob("*")) if path.is_file() and path.resolve() != output_path]
    included = [path for path in candidates if not _should_exclude(path, root)]
    blocked = _find_blocked_env_files(included)
    warnings = _scan_for_possible_secrets(included, root)

    if blocked:
        print("Security check failed: an env file would be exported; aborting.")
        for path in blocked:
            print(f"- {path.relative_to(root)}")
        sys.exit(1)

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in included:
            archive.write(path, path.relative_to(root))

    for warning in warnings:
        print(f"warning: {warning}")
    print(f"exported_files={len(included)}")
    print(f"zip_path={output_path}")


def _should_exclude(path: Path, root: Path = PROJECT_ROOT) -> bool:
    """Return True when a path should not be exported."""

    try:
        relative_parts = path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        return True

    if _is_env_file(path):
        return True
    if _is_local_mcp_config(path, root):
        return True
    if path.name in EXCLUDED_NAMES:
        return True
    if any(part in EXCLUDED_DIRS for part in relative_parts):
        return True
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return True
    if path.name.endswith(".sandbox.log"):
        return True
    for index, part in enumerate(relative_parts):
        if part == "frontend" and index + 1 < len(relative_parts) and relative_parts[index + 1] in {"dist", "node_modules"}:
            return True
        if part == "sandbox_store":
            return True
        if part in STORE_DIRS:
            return path.name != ".gitkeep"
    return False


def _is_env_file(path: Path) -> bool:
    if path.name == ".env.example":
        return False
    return path.name == ".env" or path.name.startswith(".env.")


def _is_local_mcp_config(path: Path, root: Path = PROJECT_ROOT) -> bool:
    """Return True for local MCP config files that should stay out of exports."""

    try:
        relative_parts = path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        return False

    if not relative_parts or relative_parts[0] != "config":
        return False

    if len(relative_parts) == 2:
        name = relative_parts[1]
        if name.endswith(".example.json"):
            return False
        return name == "mcp_servers.local.json" or name.endswith(".local.json")

    if len(relative_parts) == 3 and relative_parts[1] == "mcp.d":
        name = relative_parts[2]
        if name == ".gitkeep":
            return False
        if name == "README.md":
            return True
        if name.endswith(".example.json"):
            return False
        return name.endswith(".json")

    return False


def _find_blocked_env_files(paths: list[Path]) -> list[Path]:
    """Return env files that slipped through exclusion rules."""

    return [path for path in paths if _is_env_file(path)]


def _scan_for_possible_secrets(paths: list[Path], root: Path = PROJECT_ROOT) -> list[str]:
    """Warn when exported source files contain key-like tokens without printing secrets."""

    warnings: list[str] = []
    for path in paths:
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                warnings.append(f"possible secret-like token in {path.relative_to(root)}")
                break
    return warnings


if __name__ == "__main__":
    main()
