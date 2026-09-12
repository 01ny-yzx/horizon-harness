"""Reset persistent memory and optional local runtime stores for development.

Default mode is dry-run. Pass --apply to clear long-term-memory rows.
The script reports counts only and never prints stored contents.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.database import get_default_database_path
from core.persistent_memory import PersistentMemory


PRESERVE_NAMES = {
    ".env",
    ".env.example",
    ".gitkeep",
    "requirements.txt",
    "package.json",
    "package-lock.json",
}


@dataclass
class ResetPlan:
    database_path: Path
    memory_counts: dict[str, int]
    runtime_entries: set[Path]
    rag_entries: set[Path]

    @property
    def total(self) -> int:
        return sum(self.memory_counts.values()) + len(self.runtime_entries) + len(self.rag_entries)


def _is_preserved(path: Path) -> bool:
    return path.name in PRESERVE_NAMES


def _add_existing_file(paths: set[Path], path: Path) -> None:
    if path.exists() and path.is_file() and not _is_preserved(path):
        paths.add(path.resolve())


def _add_dir_children(paths: set[Path], directory: Path) -> None:
    if not directory.exists() or not directory.is_dir():
        return
    for child in directory.iterdir():
        if _is_preserved(child):
            continue
        paths.add(child.resolve())


def _collect_runtime_entries(root: Path) -> set[Path]:
    entries: set[Path] = set()
    for directory_name in ("cache_store", "usage_store"):
        directory = root / directory_name
        if directory.exists():
            for path in directory.glob("*.json"):
                _add_existing_file(entries, path)
    for directory_name in ("browser_artifacts", "traces", "logs"):
        _add_dir_children(entries, root / directory_name)
    workspace = root / "workspace_store"
    if workspace.exists():
        for directory_name in ("cache_store", "usage_store", "browser_artifacts", "traces", "logs"):
            for directory in workspace.glob(f"**/{directory_name}"):
                _add_dir_children(entries, directory)
    return entries


def _collect_rag_entries(root: Path) -> set[Path]:
    entries: set[Path] = set()
    for directory_name in ("document_store", "vector_store"):
        directory = root / directory_name
        if directory.exists():
            for path in directory.glob("*.json"):
                _add_existing_file(entries, path)
    workspace = root / "workspace_store"
    if workspace.exists():
        for directory_name in ("document_store", "vector_store"):
            for directory in workspace.glob(f"**/{directory_name}"):
                _add_dir_children(entries, directory)
    return entries


def build_plan(
    root: Path,
    *,
    database_path: Path | str | None = None,
    include_runtime: bool = False,
    include_rag: bool = False,
) -> ResetPlan:
    root = root.resolve()
    resolved_database = (
        Path(database_path).expanduser().resolve()
        if database_path is not None
        else get_default_database_path()
    )
    counted = PersistentMemory.count_all_memory(resolved_database)
    if not counted.get("success"):
        raise RuntimeError(str(counted.get("error") or "Could not inspect persistent memory."))
    return ResetPlan(
        database_path=resolved_database,
        memory_counts=dict(counted.get("data", {}).get("counts", {})),
        runtime_entries=_collect_runtime_entries(root) if include_runtime else set(),
        rag_entries=_collect_rag_entries(root) if include_rag else set(),
    )


def _remove_entry(path: Path) -> None:
    if _is_preserved(path) or not path.exists():
        return
    if path.is_dir():
        for child in list(path.iterdir()):
            _remove_entry(child)
        try:
            path.rmdir()
        except OSError:
            pass
        return
    path.unlink()


def apply_plan(plan: ResetPlan) -> dict[str, object]:
    reset = PersistentMemory.reset_all_memory(plan.database_path)
    if not reset.get("success"):
        return reset
    for path in sorted(plan.runtime_entries | plan.rag_entries):
        _remove_entry(path)
    return reset


def _print_summary(plan: ResetPlan, *, apply: bool) -> None:
    mode = "apply" if apply else "dry-run"
    verb = "reset" if apply else "would reset"
    print(
        f"{mode}: {verb} {sum(plan.memory_counts.values())} persistent-memory rows, "
        f"{len(plan.runtime_entries)} runtime entries, {len(plan.rag_entries)} RAG entries."
    )
    print(f"database: {plan.database_path}")
    print("preserved: .gitkeep, .env, dependency manifests, source and test files.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="project root for optional runtime/RAG cleanup")
    parser.add_argument("--database-path", type=Path, help="explicit Horizon database path")
    parser.add_argument("--dry-run", action="store_true", help="show planned reset without deleting anything")
    parser.add_argument("--apply", action="store_true", help="clear selected local state")
    parser.add_argument("--include-runtime", action="store_true", help="also clear runtime stores such as cache/logs/browser artifacts")
    parser.add_argument("--include-rag", action="store_true", help="also clear document/vector stores")
    parser.add_argument("--all", action="store_true", help="clear memory, runtime stores, and RAG/document stores")
    args = parser.parse_args()

    include_runtime = args.include_runtime or args.all
    include_rag = args.include_rag or args.all
    try:
        plan = build_plan(
            args.root,
            database_path=args.database_path,
            include_runtime=include_runtime,
            include_rag=include_rag,
        )
    except RuntimeError as exc:
        print(f"reset failed: {exc}")
        return 1
    should_apply = bool(args.apply)
    _print_summary(plan, apply=should_apply)
    if should_apply:
        result = apply_plan(plan)
        if not result.get("success"):
            print(f"reset failed: {result.get('error', 'unknown database error')}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
