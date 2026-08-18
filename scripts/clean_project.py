"""Clean generated local files from the project.

Default safe mode removes disposable build/cache artifacts only.
Use --stores for local data stores and --deep for large dependency folders.
"""

from __future__ import annotations

import argparse
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRESERVE_NAMES = {".gitkeep", ".env", ".env.example"}
SAFE_DIR_NAMES = {"__pycache__"}
SAFE_SUFFIXES = {".pyc", ".tmp", ".temp"}
SAFE_DIR_CHILDREN = {
    "logs",
}
SAFE_JSON_STORES = {"cache_store", "usage_store"}
STORE_DIRS = {"memory_store", "document_store", "vector_store", "workspace_store", "sandbox_store"}
DEEP_DIRS = {".venv", "node_modules"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help="project root to clean")
    parser.add_argument("--dry-run", action="store_true", help="show what would be deleted without deleting")
    parser.add_argument("--stores", action="store_true", help="also clean local memory/document/vector/workspace/sandbox stores")
    parser.add_argument("--deep", action="store_true", help="also clean .venv and frontend/node_modules")
    args = parser.parse_args()

    targets = build_clean_plan(args.root, include_stores=args.stores, include_deep=args.deep)
    for path in targets:
        action = "would delete" if args.dry_run else "deleted"
        print(f"{action}: {path}")
        if not args.dry_run:
            _remove_entry(path)
    print("clean complete")
    print(f"targets={len(targets)}")
    print(f"mode={'dry-run' if args.dry_run else 'apply'} stores={args.stores} deep={args.deep}")


def build_clean_plan(root: Path, *, include_stores: bool = False, include_deep: bool = False) -> list[Path]:
    root = root.resolve()
    targets: set[Path] = set()
    if not root.exists():
        return []
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if _is_preserved(path) or _is_inside_protected_dir(path, include_deep=include_deep):
            continue
        relative_parts = path.relative_to(root).parts
        if not relative_parts:
            continue
        if path.is_dir() and path.name in SAFE_DIR_NAMES:
            targets.add(path.resolve())
            continue
        if path.is_file() and path.suffix.lower() in SAFE_SUFFIXES:
            targets.add(path.resolve())
            continue
        if path.is_dir() and len(relative_parts) >= 2 and relative_parts[-2:] == ("frontend", "dist"):
            targets.add(path.resolve())
            continue
        if path.is_file() and len(relative_parts) >= 2 and relative_parts[0] == "dist" and path.suffix.lower() == ".zip":
            targets.add(path.resolve())
            continue
        if len(relative_parts) >= 2 and relative_parts[0] in SAFE_DIR_CHILDREN:
            targets.add(path.resolve())
            continue
        if path.is_file() and len(relative_parts) >= 2 and relative_parts[0] in SAFE_JSON_STORES and path.suffix.lower() == ".json":
            targets.add(path.resolve())
            continue
        if include_stores and _is_store_runtime_path(relative_parts, path):
            targets.add(path.resolve())
            continue
        if include_deep and path.is_dir() and (path.name == ".venv" or path.name == "node_modules"):
            targets.add(path.resolve())
            continue
    return sorted(_remove_nested_targets(targets), key=lambda item: str(item))


def _is_store_runtime_path(relative_parts: tuple[str, ...], path: Path) -> bool:
    if not relative_parts:
        return False
    for part in relative_parts:
        if part in STORE_DIRS:
            return not _is_preserved(path)
    return False


def _remove_nested_targets(targets: set[Path]) -> set[Path]:
    kept: set[Path] = set()
    for path in targets:
        if any(parent in targets for parent in path.parents):
            continue
        kept.add(path)
    return kept


def _remove_entry(path: Path) -> None:
    if _is_preserved(path) or not path.exists():
        return
    if path.is_dir():
        for child in sorted(path.iterdir(), key=lambda item: len(item.parts), reverse=True):
            _remove_entry(child)
        try:
            path.rmdir()
        except OSError:
            pass
        return
    path.unlink(missing_ok=True)


def _is_preserved(path: Path) -> bool:
    return path.name in PRESERVE_NAMES


def _is_inside_protected_dir(path: Path, *, include_deep: bool) -> bool:
    protected = {".git", ".idea"}
    if not include_deep:
        protected |= DEEP_DIRS
    return any(part in protected for part in path.parts)


if __name__ == "__main__":
    main()
