"""Reset local memory stores for development.

Default mode is dry-run. Pass --apply to delete selected local state files.
The script reports counts only and never prints file contents.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRESERVE_NAMES = {
    ".env",
    ".env.example",
    ".gitkeep",
    "requirements.txt",
    "package.json",
    "package-lock.json",
}
MEMORY_JSON_NAMES = {
    "task_history.json",
    "user_memory.json",
    "project_memory.json",
    "stable_facts.json",
    "preferences.json",
    "memory_index.json",
}


@dataclass
class ResetPlan:
    memory_files: set[Path]
    runtime_entries: set[Path]
    rag_entries: set[Path]

    @property
    def total(self) -> int:
        return len(self.memory_files) + len(self.runtime_entries) + len(self.rag_entries)


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


def _collect_memory_files(root: Path) -> set[Path]:
    files: set[Path] = set()
    top_memory = root / "memory_store"
    if top_memory.exists():
        for path in top_memory.glob("*.json"):
            _add_existing_file(files, path)
    workspace = root / "workspace_store"
    if workspace.exists():
        for path in workspace.glob("**/memory_store/*.json"):
            _add_existing_file(files, path)
        for name in MEMORY_JSON_NAMES:
            for path in workspace.glob(f"**/{name}"):
                _add_existing_file(files, path)
    return files


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


def build_plan(root: Path, *, include_runtime: bool = False, include_rag: bool = False) -> ResetPlan:
    root = root.resolve()
    return ResetPlan(
        memory_files=_collect_memory_files(root),
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


def apply_plan(plan: ResetPlan) -> None:
    for path in sorted(plan.memory_files | plan.runtime_entries | plan.rag_entries):
        _remove_entry(path)


def _print_summary(plan: ResetPlan, *, apply: bool) -> None:
    mode = "apply" if apply else "dry-run"
    verb = "reset" if apply else "would reset"
    print(
        f"{mode}: {verb} {len(plan.memory_files)} memory files, "
        f"{len(plan.runtime_entries)} runtime entries, {len(plan.rag_entries)} RAG entries."
    )
    print("preserved: .gitkeep, .env, dependency manifests, source and test files.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="project root to reset")
    parser.add_argument("--dry-run", action="store_true", help="show planned reset without deleting anything")
    parser.add_argument("--apply", action="store_true", help="delete selected local memory files")
    parser.add_argument("--include-runtime", action="store_true", help="also clear runtime stores such as cache/logs/browser artifacts")
    parser.add_argument("--include-rag", action="store_true", help="also clear document/vector stores")
    parser.add_argument("--all", action="store_true", help="clear memory, runtime stores, and RAG/document stores")
    args = parser.parse_args()

    include_runtime = args.include_runtime or args.all
    include_rag = args.include_rag or args.all
    plan = build_plan(args.root, include_runtime=include_runtime, include_rag=include_rag)
    should_apply = bool(args.apply)
    _print_summary(plan, apply=should_apply)
    if should_apply:
        apply_plan(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
