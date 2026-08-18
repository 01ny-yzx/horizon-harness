"""Compact stale domain-specific research guidance in task history files."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STALE_MARKERS = (
    "docs.legacy-example.invalid",
    "examplelib",
    "直接尝试" "官方子页面",
    "优先用 fetch_url 读取 " "docs.legacy-example.invalid",
    "/agents/",
    "/tools/",
    "/guardrails/",
    "/handoffs/",
    "/models/",
    "/tracing/",
)
COMPACTED_SUMMARY = "Research task history entry was compacted to remove stale domain-specific failure guidance."


def _task_history_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    top_level = root / "memory_store" / "task_history.json"
    if top_level.exists():
        paths.append(top_level)
    workspace_store = root / "workspace_store"
    if workspace_store.exists():
        paths.extend(workspace_store.glob("*/*/memory_store/task_history.json"))
    return sorted({path.resolve() for path in paths})


def _is_polluted(task: dict[str, Any]) -> bool:
    text = json.dumps(task, ensure_ascii=False).lower()
    return any(marker.lower() in text for marker in STALE_MARKERS)


def _compact_task(task: dict[str, Any]) -> dict[str, Any]:
    compacted = dict(task)
    compacted["summary"] = COMPACTED_SUMMARY
    return compacted


def clean_file(path: Path, *, apply: bool = False) -> tuple[int, int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return (0, 0)
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return (0, 0)
    changed = 0
    cleaned_tasks: list[Any] = []
    for task in tasks:
        if isinstance(task, dict) and _is_polluted(task):
            cleaned_tasks.append(_compact_task(task))
            changed += 1
        else:
            cleaned_tasks.append(task)
    if apply and changed:
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        data["tasks"] = cleaned_tasks
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return (len(tasks), changed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write cleaned task history files; default is dry-run")
    parser.add_argument("--root", type=Path, default=ROOT, help="project root to scan")
    args = parser.parse_args()

    paths = _task_history_paths(args.root)
    total_entries = 0
    total_changed = 0
    changed_files = 0
    for path in paths:
        entries, changed = clean_file(path, apply=args.apply)
        total_entries += entries
        total_changed += changed
        if changed:
            changed_files += 1
    mode = "apply" if args.apply else "dry-run"
    print(
        f"{mode}: scanned {len(paths)} task_history files, "
        f"{total_entries} entries, {total_changed} polluted entries in {changed_files} files."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
